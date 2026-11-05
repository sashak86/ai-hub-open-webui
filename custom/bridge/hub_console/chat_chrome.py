"""Read-only readiness for official Claude in Chrome; never grants browser access."""
import asyncio
import json
from pathlib import Path

from fastapi import Request

EXTENSION = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'
STORE = 'https://chromewebstore.google.com/detail/claude/'+EXTENSION
DOCS = 'https://code.claude.com/docs/en/chrome'


def status(home):
    home=Path(home)
    cli=home/'.local/bin/claude'
    manifest=home/'.config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json'
    host=False
    try:
        value=json.loads(manifest.read_text())
        host=value.get('type')=='stdio' and Path(value.get('path','')).is_file() and value.get('allowed_origins')==['chrome-extension://'+EXTENSION+'/']
    except (OSError,ValueError,TypeError):
        pass
    # File presence cannot prove extension sign-in or live IPC. Do not claim connection.
    installed=any((home/'.config/google-chrome').glob('*/Extensions/'+EXTENSION+'/*/manifest.json'))
    return {'cli_installed':cli.is_file(),'native_host_registered':host,'extension_files_detected':installed,'connected':None,'connection_status':'not_verified','store_url':STORE,'docs_url':DOCS,'provider':'claude','requires_desktop_chrome':True}


def install(app,owner_setup,home_for_request):
    @app.get('/v1/harness/chrome')
    async def chrome_status(request: Request):
        owner_setup(request)
        from .chat_jobs import owner_id
        owner_id(request)
        return await asyncio.to_thread(status,home_for_request(request))
