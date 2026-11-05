#!/usr/bin/env python3
"""Register Claude Code's built-in Chrome native host for the current user.
Does not install extensions, touch browser profiles, grant sites or start Chrome.
"""
import json
import os
import shutil
from pathlib import Path

EXTENSION = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'


def install(home=None, claude=None):
    home = Path(home or Path.home())
    cli = Path(claude or shutil.which('claude') or '')
    if not cli.is_file():
        raise SystemExit('Install official Claude Code first')
    directory = home/'.local/share/claude-chrome'
    host = directory/'native-host'
    manifest = home/'.config/google-chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json'
    launcher=home/'.local/bin/claude-chrome'
    import shlex
    host_content='#!/bin/sh\nexec '+shlex.quote(str(cli))+' --chrome-native-host "$@"\n'
    launcher_content='#!/bin/sh\nexec '+shlex.quote(str(cli))+' --chrome "$@"\n'
    value = {'name':'com.anthropic.claude_code_browser_extension','description':'Claude Code native messaging host','path':str(host),'type':'stdio','allowed_origins':['chrome-extension://'+EXTENSION+'/']}
    # Validate every target before changing any of them. Preserve existing setup.
    for path,content in [(host,host_content),(launcher,launcher_content)]:
        if path.exists() and path.read_text()!=content:
            raise SystemExit('Existing launcher differs; preserve it and use official claude --chrome setup')
    if manifest.exists() and json.loads(manifest.read_text()) != value:
        raise SystemExit('Existing native host differs; keep it and use official claude --chrome setup')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    host.write_text(host_content);host.chmod(0o700)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(value, indent=2)+'\n');manifest.chmod(0o600)
    launcher.parent.mkdir(parents=True,exist_ok=True)
    launcher.write_text(launcher_content);launcher.chmod(0o700)
    return {'native_host_registered':True,'extension_installed':False,'connected':False}

if __name__=='__main__':
    print(json.dumps(install()))
