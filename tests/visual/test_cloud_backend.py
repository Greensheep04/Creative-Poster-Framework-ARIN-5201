import asyncio, base64, json, threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from poster_visual_producer.agent import VisualProducerAgent
from poster_visual_producer.generators import compile_request
from tests.visual.helpers import make_session,call_context,fonts,png

@contextmanager
def server(*,url=False,download_fails=False,malformed=False,oversized=False):
    records=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            records.append(('post',dict(self.headers),body))
            self.send_response(200);self.send_header('x-request-id','req-test');self.end_headers()
            if oversized: self.wfile.write(b'x'*200);return
            value={'url':f'http://127.0.0.1:{self.server.server_port}/asset'} if url else {'b64_json':'bad' if malformed else base64.b64encode(png()).decode()}
            self.wfile.write(json.dumps({'data':[value],'usage':{'images':1}}).encode())
        def do_GET(self):
            records.append(('get',dict(self.headers),None))
            if download_fails or self.headers.get('Authorization'):
                self.send_response(503);self.end_headers();return
            self.send_response(200);self.end_headers();self.wfile.write(png())
    service=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{service.server_port}/images/generations',records
    finally:service.shutdown();service.server_close();thread.join(timeout=2)


def generator(endpoint,**extra):
    from poster_visual_producer.backends import create_generator
    return create_generator({'kind':'cloud_api','provider':'test-images','model':'configured-model','endpoint':endpoint,'api_key_env':'POSTER_TEST_KEY','sizes':[[1024,1536]],**extra})


def test_base64_image_flows_through_real_a3(tmp_path,spec_data,brief_data,monkeypatch):
    monkeypatch.setenv('POSTER_TEST_KEY','test-secret-key')
    with server() as (endpoint,records):
        session,spec,_=make_session(tmp_path,spec_data,brief_data);inputs,ctx=call_context(session,spec,'cloud')
        out=asyncio.run(VisualProducerAgent(generator(endpoint),fonts()).run(inputs,ctx))
    assert out.content['status']=='rendered' and out.content['usage_delta']['image_ops']==1
    assert records[0][2]['n']==1 and records[0][2]['size']=='1024x1536'
    assert records[0][2]['model']=='configured-model'
    assert 'negative_prompt' not in records[0][2] and 'seed' not in records[0][2]
    assert out.content['assets'][0]['provenance']['request_id']=='req-test'
    assert 'test-secret-key' not in json.dumps(out.content)


def test_image_download_has_no_api_authorization(tmp_path,spec_data,brief_data,monkeypatch):
    monkeypatch.setenv('POSTER_TEST_KEY','secret')
    with server(url=True) as (endpoint,records):
        session,spec,_=make_session(tmp_path,spec_data,brief_data);inputs,ctx=call_context(session,spec,'cloud')
        out=asyncio.run(VisualProducerAgent(generator(endpoint),fonts()).run(inputs,ctx))
    assert out.content['status']=='rendered'
    assert len(records)==2 and 'Authorization' not in records[1][1]


@pytest.mark.parametrize('failure',['download','malformed','oversized'])
def test_submitted_failures_do_not_resubmit_or_publish_candidate(tmp_path,spec_data,brief_data,monkeypatch,failure):
    monkeypatch.setenv('POSTER_TEST_KEY','secret')
    with server(url=failure=='download',download_fails=failure=='download',malformed=failure=='malformed',oversized=failure=='oversized') as (endpoint,records):
        session,spec,_=make_session(tmp_path,spec_data,brief_data);inputs,ctx=call_context(session,spec,'cloud')
        gen=generator(endpoint,max_response_bytes=128) if failure=='oversized' else generator(endpoint)
        with pytest.raises(ValueError):asyncio.run(VisualProducerAgent(gen,fonts()).run(inputs,ctx))
        assert len([r for r in records if r[0]=='post'])==1
        assert session.usage.image_operations==1
        assert not any(r.purpose.value=='candidate' for r in session.file_store.artifacts)


def test_missing_key_and_invalid_config_do_not_contact_service(spec_data,monkeypatch):
    monkeypatch.delenv('POSTER_TEST_KEY',raising=False)
    with server() as (endpoint,records):
        gen=generator(endpoint)
        request=compile_request(spec_data['asset_plan'][0],gen.capabilities)
        with pytest.raises(ValueError,match='[Kk]ey|[Cc]redential'):asyncio.run(gen.generate(request))
        with pytest.raises(ValueError):generator(endpoint,api_key='must-not-be-inline')
        assert not records


def test_cloud_endpoint_change_invalidates_a3_cache(tmp_path,spec_data,brief_data,monkeypatch):
    monkeypatch.setenv('POSTER_TEST_KEY','secret')
    session,spec,_=make_session(tmp_path,spec_data,brief_data)
    with server() as (a,records_a),server() as (b,records_b):
        for name,url in [('a',a),('b',b)]:
            inputs,ctx=call_context(session,spec,name)
            asyncio.run(VisualProducerAgent(generator(url),fonts()).run(inputs,ctx))
        assert session.usage.image_operations==2 and records_a and records_b
