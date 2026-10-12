"""Self-contained local review/annotation pages. No server or model required."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path

from .adapters.local_input import load_input
from .contracts import parse_report
from .data import LabelSheet, read_model, require_inputs, validate_labels
from .errors import InputError
from .evaluation_io import read_json, read_results, read_run
from .identity import input_fingerprint
from .reporting import file_lock, write_text


STYLE = """
:root{font-family:system-ui,-apple-system,sans-serif;color:#172a32;background:#f1f4f3;line-height:1.55}
*{box-sizing:border-box}body{margin:0}header{background:#123e42;color:#fff;padding:32px max(24px,calc((100vw - 1200px)/2));}
.eyebrow{letter-spacing:.12em;font-size:12px;opacity:.8}h1{font-size:30px;margin:8px 0}header p{max-width:850px;margin:8px 0;opacity:.9}
main{max-width:1200px;margin:auto;padding:24px}.toolbar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:22px}
button,select,input,textarea{font:inherit}button{background:#126568;border:0;color:white;padding:11px 18px;border-radius:6px;cursor:pointer}
select{padding:8px;border:1px solid #b8c9c8;background:white;border-radius:4px}button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #f2bd5f;outline-offset:2px}
.sample{background:white;border:1px solid #d9e3df;border-radius:10px;padding:22px;margin-bottom:24px;box-shadow:0 3px 12px #123e4208}
.sample[hidden]{display:none}.sample h2{font-size:20px;margin:0 0 18px}.grid{display:grid;grid-template-columns:minmax(230px,350px) minmax(0,1fr);gap:28px}
.poster{position:relative;align-self:start;background:#e8eeeb}.poster img{display:block;width:100%;height:auto}.poster svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
rect{fill:none;stroke:#e24646;stroke-width:4}h3{font-size:14px;margin:18px 0 8px;color:#365e60}h3:first-child{margin-top:0}
.pill,.badge{display:inline-block;padding:3px 9px;border-radius:5px;margin:2px 5px 2px 0;font-size:13px;background:#e9f0ed}
.pass{background:#d8f0e2;color:#17563d}.fail{background:#ffe2db;color:#973f22}.unknown,.error{background:#fff0cc;color:#705615}
.muted{font-size:12px;color:#627979}.issue{border-left:3px solid #d46e52;padding:8px 12px;margin:10px 0;background:#fff8f4;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:13px;table-layout:fixed}td,th{border-bottom:1px solid #e4eae6;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:#f3f6f4;padding:12px}label{display:block;font-size:13px;font-weight:600;margin-top:12px}
input,textarea{display:block;width:100%;padding:9px;border:1px solid #b8c9c8;border-radius:4px;margin-top:5px}textarea{min-height:90px;resize:vertical}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.note{background:#e7eddd;padding:12px 15px;border-radius:6px;font-size:13px}
.secondary{background:#e7eeea;color:#214b4c}button:disabled{opacity:.5;cursor:default}.box-tools{display:flex;gap:12px;align-items:center;margin:10px 0;flex-wrap:wrap}
.poster .annotation-layer{pointer-events:none}.poster .annotation-layer.drawing{pointer-events:auto;cursor:crosshair;touch-action:none;user-select:none}
.annotation-layer rect{stroke:#df3b3b;fill:#e246461c;pointer-events:none}.annotation-layer rect.pending{stroke-dasharray:10 7}
.region-row{border:1px solid #d9e3df;padding:10px;margin:10px 0;border-radius:6px}.region-row button{margin-top:10px}
#boot-status,#save-status{overflow-wrap:anywhere;margin:10px 0}details{margin:12px 0}summary{cursor:pointer}#json-preview{height:250px;font-family:monospace;font-size:12px}
footer{padding:16px;text-align:center;font-size:12px;color:#58716d}@media(max-width:700px){.grid{grid-template-columns:1fr}.poster{max-width:350px}.sample{padding:16px}.form-row{grid-template-columns:1fr}}
@media print{.toolbar{display:none}.sample{break-inside:avoid;box-shadow:none}body{background:white}header{color:#172a32;background:white}}
"""

SCRIPT = Path(__file__).with_name('annotation.js').read_text(encoding='utf-8')


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def pretty(value: object) -> str:
    return esc(json.dumps(value, ensure_ascii=False, indent=2))


def select(name: str, options: list[tuple[str, str]], selected: str) -> str:
    return '<select name="' + name + '">' + ''.join(
        f'<option value="{key}"' + (' selected' if key == selected else '') + f'>{esc(label)}</option>'
        for key, label in options) + '</select>'


def card(index: int, audit, report: dict | None, *, repeat: int | None = None, label=None) -> str:
    state = report.get('decision', 'error') if report else 'unassessed'
    image = 'data:' + audit.image.media_type + ';base64,' + base64.b64encode(audit.image.data).decode()
    boxes = ''.join(f'<rect x="{box[0]}" y="{box[1]}" width="{max(0, box[2]-box[0])}" height="{max(0, box[3]-box[1])}"/>'
                    for issue in (report or {}).get('issues', []) if (box := issue.get('bbox_xyxy')) is not None)
    poster = (f'<div class="poster"><img alt="{esc(audit.sample_id)} 海报" draggable="false" src="{image}"><svg class="annotation-layer" viewBox="0 0 720 1280" aria-label="人工问题框，点击开始画框后在图片上拖动" role="img"></svg></div>' if label is not None else
              f'<div class="poster"><img alt="{esc(audit.sample_id)} 海报" src="{image}"><svg viewBox="0 0 720 1280" aria-hidden="true">{boxes}</svg></div>')
    if label is not None:
        poster = ('<div><div class="box-tools"><button type="button" class="draw-toggle" aria-pressed="false">开始画框</button>'
                  '<span class="box-count" role="status"></span></div>' + poster +
                  '<p class="muted">先点“开始画框”，再在图片上按住并拖动。可以画多个框；框是可选证据，不代替结论、code 和文字依据。</p><div class="regions"></div></div>')
    parts = [f'<article class="sample" id="sample-{index}" data-sample-id="{esc(audit.sample_id)}" data-state="{esc(state)}"><h2>{esc(audit.sample_id)}',
             f' · 第 {repeat} 遍' if repeat else '', f' <span class="badge {esc(state)}">{esc(state)}</span></h2><div class="grid">',
             poster + '<div>',
             '<h3>原始关键词</h3>', ''.join(f'<span class="pill">{esc(k.text)}</span>' for k in audit.keywords),
             '<h3>用户约束</h3><pre>' + pretty([c.model_dump(mode='json') for c in audit.user_constraints]) + '</pre>']
    observed = {t['copy_id']: t for t in (report or {}).get('text_checks', [])}
    parts.append('<h3>文案核对</h3><table><thead><tr><th>采用文案 / 核对规则</th><th>观察文字</th><th>准确 / 可读</th></tr></thead><tbody>')
    for item in audit.approved_copy or ():
        check = observed.get(item.copy_id, {})
        policy = '逐字' if item.match_policy == 'exact' else '排版等价：大小写、空白、连接符和引号'
        observed_text = check.get('observed_text')
        display = ('未审核' if 'observed_text' not in check else '无法辨认 / 缺证据'
                   if observed_text is None else '明确未出现' if observed_text == '' else observed_text)
        parts.append(f'<tr><td>{esc(item.text)}<p class="muted">{esc(policy)}</p></td><td>{esc(display)}</td><td>{esc(check.get("status", "未审核"))} / {esc(check.get("readability", "未审核"))}</td></tr>')
    parts.append('</tbody></table>')
    if report:
        parts.append('<h3>审核记录</h3>')
        if report.get('kind') == 'execution_error':
            parts.append('<div class="note">执行未完成：' + esc(report.get('error_type')) + '。不能据此判断海报合格。</div>')
        for issue in report.get('issues', []):
            parts.append(f'<div class="issue"><strong>{esc(issue["code"])}</strong> · {esc(issue["severity"])}<p>{esc(issue["evidence"])}</p><p>{esc(issue["suggestion"])}</p></div>')
        if report.get('uncertainties'):
            parts.append('<div class="note">未确定：' + esc('；'.join(report['uncertainties'])) + '</div>')
        parts.append('<h3>修改建议</h3><pre>' + pretty(report.get('repair_hint')) + '</pre>')
        parts.append('<details><summary>检查范围和调用记录</summary><pre>' + pretty({
            'coverage': report.get('coverage'), 'calls': report.get('calls'), 'model': report.get('model')}) + '</pre></details>')
    if label is not None:
        parts.extend(['<h3>你的独立判断</h3><div class="form-row"><label>总体结论',
            select('decision', [('', '未标注'), ('pass', 'pass · 符合'), ('fail', 'fail · 有明确问题'), ('unknown', 'unknown · 证据不足')], label.decision or ''),
            '</label><label>可归因动作（当前版本）',
            select('repair', [('unassessed', '不参与动作评估'), ('none', '不应给自动建议'), ('layout', 'layout · 明确布局问题')],
                   'unassessed' if label.repair_stages is None else 'none' if not label.repair_stages else label.repair_stages[0]),
            '</label></div><label>具体问题 code（英文逗号分隔）<input name="issues" value="' + esc(', '.join(label.issue_codes)) + '"></label>',
            '<label>判断依据<textarea name="evidence">' + esc(label.evidence) + '</textarea></label>'])
    parts.append(('<p class="muted">人工框保存到 regions 字段，不参与当前结论一致性评分，也不证明上游模块的根因。</p>' if label is not None else
                  '<p class="muted">红框仅显示报告提供的位置；没有定位证据时不画框。</p>') + '</div></div></article>')
    return ''.join(parts)


def render(cards: list[str], *, title: str, note: str, labels: LabelSheet | None = None) -> str:
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    policy = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-" + digest + "'; connect-src 'none'; form-action 'none'; base-uri 'none'"
    toolbar = ('<button id="export-labels" type="button" disabled>保存标注 JSON</button>'
               '<button id="copy-json" type="button" class="secondary" disabled>复制 JSON</button>'
               '<button id="recover-draft" type="button" class="secondary" disabled>恢复浏览器草稿</button>'
               '<span id="progress" role="status"></span>' if labels else
               '<label for="filter">显示结果</label><select id="filter"><option value="all">全部</option><option value="fail">fail</option><option value="unknown">unknown</option><option value="pass">pass</option><option value="error">运行错误</option></select>')
    payload = ''
    annotation_tools = ''
    if labels:
        safe = labels.model_dump_json().replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
        payload = '<script type="application/json" id="annotation-data">' + safe + '</script>'
        annotation_tools = ('<p id="boot-status" class="note" role="status">交互尚未启动。若一直显示此提示，请用 Chrome 或 Safari 打开本文件；不要将空白模板当作已保存的标注。</p>'
                            '<p id="save-status" role="status"></p><details id="json-panel"><summary>查看本次导出的 JSON（实时更新）</summary>'
                            '<label for="json-preview">当前标注 JSON，可选中复制</label><textarea id="json-preview" readonly></textarea></details>'
                            '<details><summary>继续上次标注：导入 JSON</summary><label for="import-file">选择原标注文件</label>'
                            '<input id="import-file" type="file" accept=".json,application/json">'
                            '<label for="import-text">或者粘贴原标注 JSON</label><textarea id="import-text"></textarea>'
                            '<button id="import-json" type="button" disabled>导入粘贴的 JSON</button></details>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="' + esc(policy) + '"><title>' + esc(title) + '</title><style>' + STYLE + '</style></head><body>'
            '<header><div class="eyebrow">A4 / LOCAL REVIEW</div><h1>' + esc(title) + '</h1><p>' + esc(note) + '</p></header>'
            '<main><div class="toolbar">' + toolbar + '</div>' + annotation_tools + ''.join(cards) + '</main><footer>所有图片已嵌入本页 · 无需模型账号 · 页面不会发送网络请求</footer>'
            + payload + '<script>' + SCRIPT + '</script></body></html>')


def build_page(out: Path, *, dataset: Path | None = None, run: Path | None = None,
               labels: Path | None = None, input_path: Path | None = None, report_path: Path | None = None) -> None:
    cards, sheet = [], None
    if labels:
        if dataset is None or run or input_path or report_path:
            raise InputError('annotation requires only --dataset and --labels; model results are not shown')
        _, audits = require_inputs(dataset)
        sheet = read_model(labels, LabelSheet); validate_labels(sheet, audits, complete=False)
        if any(item.repair_stages is not None and any(stage != 'layout' for stage in item.repair_stages) for item in sheet.items):
            raise InputError('annotation page supports current layout actions only; preserve other stages in JSON')
        cards = [card(i, audits[item.sample_id], None, label=item) for i, item in enumerate(sheet.items)]
        title, note = '独立标注 · ' + sheet.annotator, '只依据原始需求和图片判断。本页不展示模型报告或数据集金标准；保存草稿后可重新导入。'
    elif dataset and run and not input_path and not report_path:
        _, audits = require_inputs(dataset)
        if not run.is_dir():
            raise InputError('saved run directory not found')
        with file_lock(run / '.run.lock'):
            metadata = read_run(run); rows, _ = read_results(run, metadata)
            for index, row in sorted(rows.items()):
                job = metadata['spec']['jobs'][index]; audit = audits.get(job['sample_id'])
                if audit is None or input_fingerprint(audit) != job['input_sha256']:
                    raise InputError('report inputs differ from the supplied dataset')
                path = run / row['report_path']
                value = read_json(path) if path.exists() else {'kind': 'execution_error', 'error_type': 'UnfinishedRequest'}
                cards.append(card(index, audit, value, repeat=job['repeat']))
        title = '海报审核对照'
        note = ('固定回放 · 用于检查软件流程，不代表真实模型识别效果。' if metadata['spec']['evidence_kind'] == 'software_demo_only'
                else '模型审核记录 · 结合原始需求和图像复核，不把模型判断当作人工金标准。')
    elif input_path and report_path and not dataset and not run:
        audit = load_input(input_path); report = parse_report(report_path.read_text())
        if report.sample_id != audit.sample_id or report.image_sha256 != audit.image.sha256:
            raise InputError('report sample/image does not match input')
        cards = [card(0, audit, report.model_dump(mode='json'))]
        title, note = '单张海报审核', ('固定回放 · 非真实模型评测。' if report.execution_mode == 'fake' else '模型审核记录 · 请人工复核。')
    else:
        raise InputError('choose --dataset/--run, --dataset/--labels, or --input/--report')
    write_text(out, render(cards, title=title, note=note, labels=sheet))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'run', 'labels', 'input', 'report'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        build_page(args.out, dataset=args.dataset, run=args.run, labels=args.labels,
                   input_path=args.input, report_path=args.report)
        print(args.out.resolve()); return 0
    except (InputError, OSError, ValueError) as exc:
        print(f'review error: {exc}'); return 2


if __name__ == '__main__':
    raise SystemExit(main())
