#!/usr/bin/env python3
"""Install the bounded user unit after upstream, venv and web build are ready.
No package mutation in shared environments; no model or credentials in git.
"""
import argparse
import json
import shutil
import subprocess
import time
import urllib.request
import wave
import io
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def ensure_profile(home):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base='http://127.0.0.1:8771'
    for attempt in range(30):
        try:
            with opener.open(base+'/health',timeout=2) as r:
                if json.load(r).get('status')=='ok':break
        except Exception:
            pass
        time.sleep(1)
    with opener.open(base+'/profiles',timeout=5) as r:rows=json.load(r)
    name='Ирина · Piper CPU'
    profile=next((row['id'] for row in rows if row.get('name')==name),None)
    if not profile:
        text='Это русский голос Ирины. Локальный синтез речи на процессоре.'
        request=urllib.request.Request(base+'/v1/audio/speech',data=json.dumps({'model':'sherpa-onnx','input':text,'voice':'default','response_format':'wav'}).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=45) as r:audio=r.read()
        with wave.open(io.BytesIO(audio)) as wav:
            assert wav.getframerate()==22050 and wav.getnframes()>22050
        boundary='VoiceStudio'+uuid.uuid4().hex
        chunks=[]
        for key,value in {'name':name,'ref_text':text,'language':'Russian','kind':'clone'}.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="ref_audio"; filename="piper-irina.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()+audio+b'\r\n')
        chunks.append(f'--{boundary}--\r\n'.encode())
        request=urllib.request.Request(base+'/profiles',data=b''.join(chunks),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        with opener.open(request,timeout=30) as r:profile=json.load(r)['id']
    (home/'.local/share/voicestudio-local/profile.json').write_text(json.dumps({'profile_id':profile,'voice':'irina','is_cloned_voice':False},ensure_ascii=False))
    return profile


def install(home, source, origins, start=True):
    home=Path(home)
    deployment=Path(source)/'deploy/voicestudio'
    upstream=home/'.local/share/voicestudio-src'
    python=home/'.local/share/voicestudio-venv/bin/python'
    assert (upstream/'backend/main.py').is_file(), 'VoiceStudio checkout missing'
    assert (upstream/'frontend/dist/index.html').is_file(), 'Build upstream web UI first'
    assert python.is_file(), 'Dedicated venv missing'
    for origin in origins:
        parts=urlsplit(origin)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.path not in {'','/'} or parts.query or parts.fragment:
            raise ValueError('Expected exact HTTPS origin')
    local=home/'.local/share/voicestudio-local'
    config=home/'.config/voicestudio-local'
    units=home/'.config/systemd/user'
    for p in (local,config,units):p.mkdir(parents=True,exist_ok=True,mode=0o700)
    shutil.copy2(deployment/'local_entry.py',local/'local_entry.py')
    (local/'local_entry.py').chmod(0o600)
    env=(deployment/'environment.example').read_text().replace('@HOME@',str(home)).replace('@GATEWAY_ORIGINS@',','.join(origins))
    (config/'environment').write_text(env);(config/'environment').chmod(0o600)
    shutil.copy2(deployment/'hub-voicestudio.service',units/'hub-voicestudio.service')
    # Keep upstream JavaScript intact; an explicit hosted-profile bootstrap
    # replaces the already-completed desktop setup flow for each new browser.
    dist=upstream/'frontend/dist'
    shutil.copy2(deployment/'profile-bootstrap.js',dist/'ai-hub-local-profile.js')
    index=dist/'index.html'
    html=index.read_text()
    tag='<script src="/ai-hub-local-profile.js"></script>'
    if tag not in html:
        html=html.replace('<head>','<head>'+tag,1)
        index.write_text(html)
    if start:
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','hub-voicestudio.service'],check=True)
        subprocess.run(['systemctl','--user','restart','hub-voicestudio.service'],check=True)
        profile=ensure_profile(home)
        bootstrap=(deployment/'profile-bootstrap.js').read_text()
        bootstrap+='\ntry { const k="voicestudio.clone.settings.v1"; if(!localStorage.getItem(k)) localStorage.setItem(k,JSON.stringify({selectedProfileId:'+json.dumps(profile)+',presetVoice:"irina",language:"Russian",autoPlay:false})); if(!location.hash || location.hash==="#/")history.replaceState({},"","/#/clone"); } catch (_) {}\n'
        (dist/'ai-hub-local-profile.js').write_text(bootstrap)
    return {'unit':'hub-voicestudio.service','user_service':True,'native_url':'http://127.0.0.1:8771','cpu_profile':'sherpa-onnx','gateway_origins':origins}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--origin',action='append',required=True)
    parser.add_argument('--no-start',action='store_true')
    args=parser.parse_args()
    print(json.dumps(install(Path.home(),Path(__file__).resolve().parents[2],args.origin,not args.no_start)))
