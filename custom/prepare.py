#!/usr/bin/env python3
"""Generate private local configuration and pinned static overrides; never start services."""
from pathlib import Path
import os,secrets,subprocess,sys,shutil,importlib.util
ROOT=Path(__file__).resolve().parent
IMAGE='ghcr.io/open-webui/open-webui:v0.12.4-slim@sha256:41812075aede8ada8497cb2b71c76412978633eee72775bb77796f593d03b677'
def prepare():
 runtime=ROOT/'runtime';runtime.mkdir(mode=0o700,exist_ok=True)
 env=runtime/'private.env'
 if not env.exists():
  fd=os.open(env,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as h:h.write('HUB_CHAT_TOKEN='+secrets.token_urlsafe(48)+'\nWEBUI_SECRET_KEY='+secrets.token_urlsafe(48)+'\nWEBUI_ADMIN_EMAIL=admin@example.com\nWEBUI_ADMIN_PASSWORD='+secrets.token_urlsafe(24)+'\n')
 subprocess.run(['docker','pull',IMAGE],check=True)
 container=subprocess.check_output(['docker','create',IMAGE],text=True).strip()
 source=runtime/'pinned-build'
 try:
  source.mkdir(exist_ok=True)
  subprocess.run(['docker','cp',container+':/app/build/.',str(source)],check=True)
 finally:subprocess.run(['docker','rm',container],check=True,stdout=subprocess.DEVNULL)
 subprocess.run([sys.executable,str(ROOT/'deploy/patch-webui-queue.py'),str(source),str(runtime/'overrides')],check=True)
 for name in ('harness-ui.js','media-ui.js'):shutil.copyfile(ROOT/'deploy'/name,runtime/'overrides/ai-hub'/name)
 shutil.copytree(ROOT/'deploy/assets',runtime/'overrides/ai-hub/assets',dirs_exist_ok=True)
 print('Prepared pinned overrides. Private settings: custom/runtime/private.env (0600). No service started.')
if __name__=='__main__':prepare()
