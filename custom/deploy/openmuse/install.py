#!/usr/bin/env python3
"""Install pinned OpenMuse + explicit local fork, without provider keys or model pulls.

Run as the target user. Create private runtime.env as described in HANDOFF before
starting the service. Build scheduling is the operator's responsibility.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

UPSTREAM = 'https://github.com/CopilotKit/openmuse.git'
REVISION = 'a4944f4c471d3dc93b8bfc060b5da309b5216ab2'
HERE = Path(__file__).resolve().parent
BASE = Path.home()/'.local/share/openmuse'
SOURCE = BASE/'source'
TOOLING = BASE/'tooling'

def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--build',action='store_true');parser.add_argument('--install-unit',action='store_true');args=parser.parse_args()
    BASE.mkdir(parents=True,exist_ok=True)
    if not SOURCE.exists():
        run('git','clone',UPSTREAM,str(SOURCE));run('git','checkout',REVISION,cwd=SOURCE)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()
    if revision!=REVISION:raise SystemExit('Unexpected upstream revision; refusing to overwrite source')
    patch=HERE/'local.patch'
    # Already-applied sources are accepted only if the exact reverse patch succeeds.
    check=subprocess.run(['git','apply','--reverse','--check',str(patch)],cwd=SOURCE,capture_output=True)
    if check.returncode:
        run('git','apply','--check',str(patch),cwd=SOURCE);run('git','apply',str(patch),cwd=SOURCE)
    if args.build:
        run('npm','install','--prefix',str(TOOLING),'pnpm@11.19.0','--ignore-scripts','--no-audit','--no-fund')
        env=os.environ|{'PATH':str(TOOLING/'node_modules/.bin')+':'+os.environ['PATH'],'NODE_OPTIONS':'--max-old-space-size=1024','CI':'1','EXPO_NO_TELEMETRY':'1'}
        run(str(TOOLING/'node_modules/.bin/pnpm'),'install','--frozen-lockfile','--child-concurrency=1','--network-concurrency=4',cwd=SOURCE,env=env)
        run('node','node_modules/typescript/bin/tsc','-p','tsconfig.build.json',cwd=SOURCE,env=env)
        run(str(TOOLING/'node_modules/.bin/pnpm'),'--dir','apps/mobile','exec','expo','export','--platform','web','--output-dir','dist/web','--max-workers','1',cwd=SOURCE,env=env)
    if args.install_unit:
        target=Path.home()/'.config/systemd/user/openmuse-local.service';target.parent.mkdir(parents=True,exist_ok=True)
        unit=(HERE/'openmuse-local.service').read_text().replace('/usr/local/bin/node',shutil.which('node') or '/usr/bin/node')
        target.write_text(unit)
        run('systemctl','--user','daemon-reload')
    print('Pinned local fork prepared; configure private runtime.env and owner gateway before enabling service')
if __name__=='__main__':main()
