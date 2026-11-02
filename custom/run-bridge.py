#!/usr/bin/env python3
from pathlib import Path
import os,sys
root=Path(__file__).resolve().parent
for line in (root/'runtime/private.env').read_text().splitlines():
 if '=' in line and not line.startswith('#'):
  k,v=line.split('=',1);os.environ.setdefault(k,v)
os.environ.setdefault('SASHA_WORKBENCH_ASSETS',str(root/'runtime/overrides/ai-hub'))
sys.path.insert(0,str(root/'bridge'))
import uvicorn
uvicorn.run('hub_console.standalone:create_app',factory=True,host=os.environ.get('WORKBENCH_HOST','127.0.0.1'),port=int(os.environ.get('WORKBENCH_PORT','8088')),access_log=False)
