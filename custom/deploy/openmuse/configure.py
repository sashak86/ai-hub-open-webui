#!/usr/bin/env python3
"""Create private local-module settings; never prints credentials or pulls models."""
import argparse
import base64
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from urllib.request import urlopen

parser=argparse.ArgumentParser()
parser.add_argument('--public-url',required=True,help='Owner-gateway HTTPS origin, e.g. https://ai-hub:8474')
parser.add_argument('--model',help='Already-resident Ollama model; never downloaded here')
args=parser.parse_args()
url=urlsplit(args.public_url)
if url.scheme!='https' or not url.hostname or url.username or url.password or url.path not in {'','/'} or url.query or url.fragment:
    raise SystemExit('Use an HTTPS gateway origin without path or credentials')
with urlopen('http://127.0.0.1:11434/api/ps',timeout=5) as response:
    resident={m['name'] for m in json.load(response)['models']}
model=args.model or (next(iter(resident)) if len(resident)==1 else None)
if not model or model not in resident:raise SystemExit('Select a model already resident in Ollama; no model will be loaded or pulled')
home=Path.home();directory=home/'.config/openmuse';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
env=directory/'runtime.env'
if env.exists():raise SystemExit('Private runtime.env exists; refusing to overwrite')
tokenfile=directory/'proxy.token'
if not tokenfile.exists():tokenfile.write_text(secrets.token_urlsafe(48)+'\n');tokenfile.chmod(0o600)
key=tokenfile.read_text().strip()
settings={'LOCAL_ONLY':'true','WORKSPACE_MODE':'live','AGENT_BACKEND':'model','MODEL':'openai/'+model,'LOCAL_OLLAMA_MODEL':model,'PORT':'8797','HOST':'127.0.0.1','PUBLIC_API_URL':args.public_url.rstrip('/'),'DATA_DIR':str(home/'.local/share/openmuse/data'),'OPENMUSE_ACCESS_KEY':key,'TOKEN_ENCRYPTION_KEY':base64.b64encode(secrets.token_bytes(32)).decode(),'ALLOWED_ORIGINS':args.public_url.rstrip('/'),'TASK_WORKER_ENABLED':'true','WEB_SEARCH_ENABLED':'false','COMPUTER_ENABLED':'false','JEV_MODE':'off','COPILOTKIT_TELEMETRY_DISABLED':'true','DO_NOT_TRACK':'1','EXPO_NO_TELEMETRY':'1','NODE_OPTIONS':'--max-old-space-size=512'}
if any('\n' in value or '\r' in value for value in settings.values()):raise SystemExit('Invalid multiline value')
(home/'.local/share/openmuse/data').mkdir(parents=True,exist_ok=True,mode=0o700)
env.write_text(''.join(f'{name}={value}\n' for name,value in settings.items()));env.chmod(0o600)
print('Private settings created; no credential was printed and no model was loaded')
