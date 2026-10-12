"""Configuration guidance; never installs packages, downloads weights or generates images."""
import argparse,json,sys
from pathlib import Path
from .configuration import backend_template,write_backend_config,diagnose_backend


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    setup=commands.add_parser('setup',help='Select a backend and write a local template')
    setup.add_argument('--backend',choices=['sdxl','qwen_image','cloud_api'])
    setup.add_argument('--output',type=Path,required=True);setup.add_argument('--overwrite',action='store_true')
    doctor=commands.add_parser('doctor',help='Check the chosen backend without generating')
    doctor.add_argument('--backend-config',type=Path,required=True);doctor.add_argument('--fonts',type=Path)
    doctor.add_argument('--font-base',type=Path,default=Path.cwd());doctor.add_argument('--output-root',type=Path)
    args=parser.parse_args(argv)
    if args.command=='setup':
        kind=args.backend
        if kind is None:
            print('Choose a backend: 1 SDXL / 2 Qwen-Image / 3 cloud API',file=sys.stderr)
            answer=input().strip()
            kind={'1':'sdxl','2':'qwen_image','3':'cloud_api'}.get(answer,answer)
        try:
            path=write_backend_config(args.output,backend_template(kind),overwrite=args.overwrite)
        except (ValueError,OSError) as error:
            print(json.dumps({'ready':False,'error_type':type(error).__name__,'message':'Cannot create config; choose a valid backend and a new output or --overwrite'}))
            return 1
        print(json.dumps({'backend':kind,'config':str(path),'ready':False,'next_step':'Fill the local config/environment and run doctor; see docs/backend-setup.md'}))
        return 0
    try:
        config=json.loads(args.backend_config.read_text(encoding='utf-8-sig'))
        result=diagnose_backend(config,fonts_config=args.fonts,font_base=args.font_base,output_root=args.output_root)
    except (OSError,ValueError):
        result={'ready':False,'checks':[{'code':'INVALID_CONFIGURATION','ok':False,'message':'Cannot read a valid backend JSON','remediation':'Run setup then fill the selected profile'}]}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['ready'] else 1
