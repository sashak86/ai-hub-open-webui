"""Private pinned upstream installations; no global configuration mutations.

Invoked only by the server with fixed recipes. Long installs survive bridge reload.
Optional daemon workers run as desktop user under systemd resource limits.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import pwd
import resource
import secrets
import shutil
import socket
import subprocess
import sys
import time

VERSIONS={'omniroute':'3.8.52','claude-mem':'15.9.0','headroom':'0.42.0'}
INTEGRITIES={'omniroute':'sha512-R+GUs3vDWpX36n+oOwjRSJN/7dGoXC3ehSV7mSz41nZM4mFhUo2+YepYzXMk5Hf6sIwCKRpGBaPMDVR8xbvwew==','claude-mem':'sha512-HeC81WqxfnKA9+Hj0spVY9jEO5lq0pR0IeiQbeyXL/V6gKvPghXl2gQ/MT1RCbW2AgRFXQdBsVNs+dLgBtOmRw=='}


def read(path,default=None):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {} if default is None else default


def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data));os.chmod(tmp,0o600)
    if os.geteuid()==0:
        owner=path.parent.stat();os.chown(tmp,owner.st_uid,owner.st_gid)
    os.replace(tmp,path)


def env_for(base):
    env=dict(os.environ)
    # A package must not inherit credentials or a cloud proxy from the hub.
    for key in list(env):
        if key in {'CODEX_HOME','CLAUDE_CONFIG_DIR','CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR','GOOGLE_APPLICATION_CREDENTIALS','AWS_SHARED_CREDENTIALS_FILE','SSH_AUTH_SOCK'}:env.pop(key,None);continue
        if any(x in key.upper() for x in ('TOKEN','API_KEY','SECRET','PASSWORD','AUTHORIZATION')):env.pop(key,None)
    env.update(HOME=str(base/'home'),XDG_DATA_HOME=str(base/'home/.local/share'),XDG_CONFIG_HOME=str(base/'home/.config'),XDG_CACHE_HOME=str(base/'home/.cache'),HEADROOM_TELEMETRY='off',HEADROOM_OFFLINE='true',DO_NOT_TRACK='1',NEXT_TELEMETRY_DISABLED='1',LITELLM_LOG='ERROR',LITELLM_LOCAL_MODEL_COST_MAP='True',PIP_DISABLE_PIP_VERSION_CHECK='1',npm_config_update_notifier='false',CLAUDE_MEM_TELEMETRY='false',CLAUDE_MEM_TELEMETRY_ERRORS='false',CLAUDE_MEM_CHROMA_ENABLED='false',NODE_OPTIONS='--max-old-space-size=768')
    return env


def limits():
    # Shared VM: <=2 CPUs and 1.5GiB for installers; never load ML weights.
    try:os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    except (AttributeError,OSError):pass
    os.nice(10)
    # V8 reserves a large virtual cage; cgroup MemoryMax limits resident memory.
    pass


def run(args,base,timeout=1200,env=None):
    subprocess.run(args,cwd=base,env=env or env_for(base),check=True,timeout=timeout,stdin=subprocess.DEVNULL)


def install(base,ident,user):
    person=pwd.getpwnam(user)
    if os.geteuid()==0:
        os.chown(base,person.pw_uid,person.pw_gid)
        os.setgroups([]);os.setgid(person.pw_gid);os.setuid(person.pw_uid)
    limits();base.mkdir(mode=0o700,parents=True,exist_ok=True);(base/'home').mkdir(mode=0o700,exist_ok=True)
    try:
        if ident=='headroom':
            import venv
            venv.EnvBuilder(with_pip=True).create(base/'venv')
            run([str(base/'venv/bin/python'),'-m','pip','install','--no-input','headroom-ai=='+VERSIONS[ident]],base)
            run([str(base/'venv/bin/python'),'-c','from headroom import compress; r=compress([{"role":"user","content":"Hello"}], model="gpt-4o", mode="agent"); assert r.messages'],base,timeout=60)
        else:
            # Registry lock pins transitive dependencies for this installation.
            run(['npm','install','--prefix',str(base/'package'),'--no-audit','--no-fund','--omit=dev',ident+'@'+VERSIONS[ident]],base)
            lock=read(base/'package/package-lock.json')
            if lock.get('packages',{}).get('node_modules/'+ident,{}).get('integrity')!=INTEGRITIES[ident]:raise RuntimeError('package_integrity_mismatch')
            if ident=='claude-mem':run(['npm','install','--prefix',str(base/'package'),'--no-audit','--no-fund','bun@1.3.11'],base)
        write(base/'runtime.json',dict(status='installed',installed=True,version=VERSIONS[ident],installed_at=time.time(),message='Upstream установлен в отдельную папку; включение отдельно.'))
    except Exception as error:
        # No raw package output/errors in API. Detailed log stays private on disk.
        print(type(error).__name__,str(error),flush=True)
        write(base/'runtime.json',dict(status='install_failed',installed=False,version=VERSIONS[ident],failed_at=time.time(),message='Установка не завершена. Подробности в приватном install.log.'))
        raise


def unit_name(base,ident):return 'hub-addon-'+hashlib.sha256(str(base.parent).encode()).hexdigest()[:12]+'-'+ident


def control(base,ident,user,enabled):
    state=read(base/'runtime.json');name=unit_name(base,ident)
    if ident=='headroom':
        state['status']='enabled' if enabled else 'disabled';write(base/'runtime.json',state);return False
    manager=['systemctl']+([] if os.geteuid()==0 else ['--user'])
    if not enabled:
        subprocess.run(manager+['stop',name],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        state['status']='disabled';write(base/'runtime.json',state);return
    if not state.get('installed'):raise RuntimeError('addon_not_installed')
    if subprocess.run(manager+['is-active','--quiet',name],check=False).returncode==0:return False
    # Per-owner loopback port. Binding race is resolved by daemon startup failure,
    # never by attaching to an unrelated service's occupied port.
    with socket.socket() as handle:
        try:handle.bind(('127.0.0.1',state.get('port') or urlsplit(state.get('endpoint','')).port or 0))
        except OSError:handle.bind(('127.0.0.1',0))
        port=handle.getsockname()[1]
    env=env_for(base);env.update(PORT=str(port),HOST='127.0.0.1',HOSTNAME='127.0.0.1',DATA_DIR=str(base/'data'))
    if ident=='omniroute':
        state.setdefault('admin_password',secrets.token_urlsafe(32))
        env['INITIAL_PASSWORD']=state['admin_password']
        args=['node',str(base/'package/node_modules/omniroute/bin/omniroute.mjs'),'serve','--port',str(port),'--no-open','--no-tray']
        env['OMNIROUTE_DATA_DIR']=str(base/'data')
        env['OMNIROUTE_SERVER_HOST']='127.0.0.1'
    else:
        env.update(CLAUDE_MEM_WORKER_PORT=str(port),CLAUDE_MEM_WORKER_HOST='127.0.0.1',CLAUDE_MEM_DATA_DIR=str(base/'data'),CLAUDE_MEM_TELEMETRY='false',CLAUDE_MEM_TELEMETRY_ERRORS='false',CLAUDE_MEM_CHROMA_ENABLED='false')
        args=[str(base/'package/node_modules/.bin/bun'),str(base/'package/node_modules/claude-mem/plugin/scripts/worker-service.cjs'),'--daemon']
    envfile=base/'runtime.env';envfile.write_text('\n'.join(k+'='+json.dumps(v) for k,v in env.items() if re_env_key(k)));os.chmod(envfile,0o600)
    person=pwd.getpwnam(user);os.chown(envfile,person.pw_uid,person.pw_gid)
    # systemd owns cleanup and cgroup limits. Private source/package installs only.
    subprocess.run(manager+['reset-failed',name],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    cmd=['systemd-run']+(['--uid',user] if os.geteuid()==0 else ['--user'])+['--quiet','--collect','--unit',name,'--property','CPUQuota=100%','--property','MemoryMax=1500M','--property','TasksMax=128','--property','KillMode=control-group','--property','EnvironmentFile='+str(envfile),'--property','WorkingDirectory='+str(base),'--property','Restart=on-failure',*args]
    subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    state.update(port=port,endpoint='http://127.0.0.1:'+str(port),status='starting',unit=name);write(base/'runtime.json',state)
    return True


def install_active(base):
    name='hub-addon-install-'+hashlib.sha256(str(base).encode()).hexdigest()[:16]
    manager=['systemctl']+([] if os.geteuid()==0 else ['--user'])
    try:return subprocess.run(manager+['is-active','--quiet',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=4).returncode==0
    except (OSError,subprocess.SubprocessError):return False


def re_env_key(value):
    import re
    return bool(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',value))


def compress(base,payload):
    # Upstream's lossless-only mode does not require a CCR tool/model/download.
    from headroom import compress as upstream
    result=upstream(payload['messages'],model=payload.get('model','gpt-4o'),mode='agent',protect_recent=1)
    return dict(messages=result.messages,tokens_before=result.tokens_before,tokens_after=result.tokens_after,tokens_saved=result.tokens_saved,lossless=result.lossless,tokenizer_model='gpt-4o',estimated=True)


if __name__=='__main__':
    operation=sys.argv[1];base=Path(sys.argv[2]).resolve()
    if operation=='install':install(base,sys.argv[3],sys.argv[4])
    elif operation=='compress':
        limits();print(json.dumps(compress(base,json.load(sys.stdin))),flush=True)
    else:raise SystemExit(2)
