"""Portable model discovery adapter used by the custom chat job modules."""
import json,pwd,re,shutil
from pathlib import Path
from .system import user_env

def available_models(request):
    home=Path(pwd.getpwnam(request.app.state.settings.desktop_user).pw_dir)
    env=user_env(request.app.state.settings.desktop_user)
    rows=[]
    if shutil.which('codex',path=env['PATH']):
        try:
            cached=json.loads((home/'.codex/models_cache.json').read_text())
            rows += [{'id':'model/codex/'+r['slug'],'name':r.get('display_name',r['slug'])} for r in cached['models'] if r.get('visibility')=='list' and re.fullmatch(r'[a-zA-Z0-9_.-]+',r['slug'])]
        except (OSError,ValueError,KeyError):pass
    if shutil.which('claude',path=env['PATH']):rows += [{'id':'model/claude/'+s,'name':'Claude '+s+' (CLI alias)'} for s in ('sonnet','opus','haiku')]
    return rows
