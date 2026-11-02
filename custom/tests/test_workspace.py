import asyncio
import getpass
import hashlib
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from hub_console import chat_workspace as w

from .test_standalone import client

def project(c):
    r=c.post('/v1/chat-workspace/projects',json={'name':'Test','folder':'test'});assert r.status_code==200,r.text
    return r.json()['id']
def root(c,ident):
    return next((c.app.state.chat_jobs_root/'workspace').glob('*/files/test'))

def test_files_moves_and_owner_isolation(client):
    ident=project(client);base='/v1/chat-workspace/projects/'+ident
    assert client.put(base+'/file?path=hello.txt',content=b'hello').status_code==200
    assert client.get(base+'/file?path=hello.txt').content==b'hello'
    assert client.post(base+'/files',json={'action':'mkdir','path':'sub'}).status_code==200
    assert client.post(base+'/files',json={'action':'move','path':'hello.txt','to':'sub/a.txt'}).status_code==200
    assert client.get(base+'/files?path=sub').json()['items'][0]['name']=='a.txt'
    assert client.patch(base,json={'name':'New','folder':'renamed'}).json()['folder']=='renamed'
    client.headers['X-WebUI-User']='b'*64
    assert client.get(base+'/files').status_code==404
    assert client.get('/v1/chat-workspace').json()['projects']==[]

def test_traversal_symlink_and_root_mutation_rejected(client,tmp_path):
    ident=project(client);base='/v1/chat-workspace/projects/'+ident
    for path in ('../escape','/etc/passwd','x/../../escape','x\\escape'):
        assert client.put(base+'/file',params={'path':path},content=b'bad').status_code==422
    outside=tmp_path/'outside';outside.write_text('secret');(root(client,ident)/'link').symlink_to(outside)
    assert client.get(base+'/file?path=link').status_code==422
    assert client.post(base+'/files',json={'action':'delete','path':''}).status_code==422
    assert client.post(base+'/files',json={'action':'move','path':'','to':'x'}).status_code==422

def test_backups_restore_preserve_secrets_and_verify_hash(client):
    ident=project(client);base='/v1/chat-workspace/projects/'+ident
    client.put(base+'/file?path=a.txt',content=b'original');client.put(base+'/file?path=.env',content=b'private')
    backup=client.post(base+'/backups').json();assert backup['files']==1
    client.put(base+'/file?path=a.txt',content=b'changed');client.put(base+'/file?path=extra.txt',content=b'extra')
    r=client.post(base+'/restore',json={'backup_id':backup['id']});assert r.status_code==200,r.text
    assert client.get(base+'/file?path=a.txt').content==b'original'
    assert client.get(base+'/file?path=extra.txt').status_code==404
    assert client.get(base+'/file?path=.env').content==b'private'
    archive=next((client.app.state.chat_jobs_root/'workspace').glob('*/backups/'+ident+'/'+backup['id']+'.zip'));archive.write_bytes(b'corrupt')
    assert client.post(base+'/restore',json={'backup_id':backup['id']}).status_code==409

def test_schedule_rehydrates_without_http_request(client):
    ident=project(client);base='/v1/chat-workspace/projects/'+ident
    assert client.put(base+'/schedule',json={'enabled':True,'interval_hours':1,'keep':2}).status_code==200
    registry=next((client.app.state.chat_jobs_root/'workspace').glob('*/registry.json'));data=json.loads(registry.read_text());data['projects'][0]['schedule']['next_at']=0;registry.write_text(json.dumps(data))
    from starlette.requests import Request
    request=Request({'type':'http','method':'GET','path':'/','headers':[],'app':client.app,'state':{'workspace_owner':registry.parent.name}})
    asyncio.run(w.run_due(request))
    assert len(client.get(base+'/backups').json()['items'])==1
    assert json.loads(registry.read_text())['projects'][0]['schedule']['next_at']>0

def test_storage_secrets_redacted_and_allowlist(client,tmp_path,monkeypatch):
    assert client.post('/v1/chat-workspace/storages',json={'name':'private','type':'local','path':str(tmp_path)}).status_code==422
    monkeypatch.setenv('AI_HUB_WORKSPACE_ROOTS',str(tmp_path))
    assert client.post('/v1/chat-workspace/storages',json={'name':'local','type':'local','path':str(tmp_path)}).status_code==200
    r=client.post('/v1/chat-workspace/storages',json={'name':'cloud','type':'webdav','url':'https://example.test/dav','username':'user','password':'private-token'});assert r.status_code==200
    assert 'private-token' not in r.text
    assert 'private-token' not in client.get('/v1/chat-workspace').text
    row=r.json();row['name']='edited';assert client.post('/v1/chat-workspace/storages',json=row).status_code==200
    registry=next((client.app.state.chat_jobs_root/'workspace').glob('*/registry.json'));assert json.loads(registry.read_text())['storages'][1]['password']=='private-token'

def test_existing_import_is_scoped_and_never_moved(client,tmp_path,monkeypatch):
    imported=tmp_path/'external';imported.mkdir();(imported/'hello').write_text('old');(tmp_path/'private').write_text('private')
    monkeypatch.setenv('AI_HUB_WORKSPACE_ROOTS',str(imported))
    r=client.post('/v1/chat-workspace/projects',json={'name':'Existing','import_path':str(imported)});assert r.status_code==200,r.text
    ident=r.json()['id'];base='/v1/chat-workspace/projects/'+ident
    assert client.get(base+'/file?path=hello').content==b'old'
    assert client.get(base+'/file?path=../private').status_code==422
    assert client.patch(base,json={'folder':'moved'}).status_code==409
    assert imported.exists()
    assert client.patch(base,json={'name':'Rename display'}).status_code==200

def test_active_job_blocks_move_restore_delete(client):
    ident=project(client);base='/v1/chat-workspace/projects/'+ident
    client.put(base+'/file?path=a.txt',content=b'x');backup=client.post(base+'/backups').json()
    registry=next((client.app.state.chat_jobs_root/'workspace').glob('*/registry.json'))
    job=client.app.state.chat_jobs_root/registry.parent.name/'chat'/'job-one';job.mkdir(parents=True)
    (job/'request.json').write_text(json.dumps({'harness':{'project_id':ident}}));(job/'state.json').write_text(json.dumps({'status':'running'}))
    assert client.patch(base,json={'folder':'other'}).status_code==409
    assert client.post(base+'/files',json={'action':'delete','path':'a.txt'}).status_code==409
    assert client.post(base+'/restore',json={'backup_id':backup['id']}).status_code==409
    assert client.patch(base,json={'name':'Rename display'}).status_code==200

def test_restore_executable_and_reject_malicious_archive(client):
    import zipfile
    ident=project(client);base='/v1/chat-workspace/projects/'+ident
    script=root(client,ident)/'run.sh';script.write_text('#!/bin/sh\necho ok');script.chmod(0o755)
    backup=client.post(base+'/backups').json();script.unlink()
    assert client.post(base+'/restore',json={'backup_id':backup['id']}).status_code==200
    assert script.stat().st_mode&0o111
    archive=next((client.app.state.chat_jobs_root/'workspace').glob('*/backups/'+ident+'/'+backup['id']+'.zip'))
    with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape','bad')
    meta=archive.with_suffix('.json');row=json.loads(meta.read_text());row['sha256']=hashlib.sha256(archive.read_bytes()).hexdigest();meta.write_text(json.dumps(row))
    assert client.post(base+'/restore',json={'backup_id':backup['id']}).status_code==422
    assert not (script.parent.parent/'escape').exists()

def test_webdav_real_http_contract_and_external_browser(client,monkeypatch):
    import httpx
    original=w.httpx.AsyncClient;seen=[]
    xml=b'''<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response><d:response><d:href>/dav/hello.txt</d:href><d:propstat><d:prop><d:resourcetype/><d:getcontentlength>5</d:getcontentlength></d:prop></d:propstat></d:response></d:multistatus>'''
    def handler(request):
        seen.append((request.method,str(request.url),dict(request.headers),request.content))
        assert request.headers['Authorization'].startswith('Basic ')
        if request.method=='PROPFIND':return httpx.Response(207,content=xml)
        if request.method=='GET':return httpx.Response(200,content=b'hello')
        return httpx.Response(201)
    monkeypatch.setattr(w.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    storage=client.post('/v1/chat-workspace/storages',json={'name':'Cloud','type':'webdav','url':'https://example.test/dav','username':'user','password':'private-token'}).json();base='/v1/chat-workspace/storages/'+storage['id']
    assert client.post(base+'/test').status_code==200
    listing=client.get(base+'/files').json();assert listing['items'][0]['name']=='hello.txt'
    assert client.get(base+'/file?path=hello.txt').content==b'hello'
    assert client.put(base+'/file?path=a.txt',content=b'upload').status_code==200
    assert client.post(base+'/files',json={'action':'mkdir','path':'sub'}).status_code==200
    assert client.post(base+'/files',json={'action':'move','path':'a.txt','to':'sub/a.txt'}).status_code==200
    assert client.post(base+'/files',json={'action':'delete','path':'sub/a.txt'}).status_code==200
    assert client.get(base+'/file?path=../secret').status_code==422
    move=next(r for r in seen if r[0]=='MOVE');assert move[2]['destination']=='https://example.test/dav/sub/a.txt';assert move[2]['overwrite']=='F'
    ident=project(client);backup=client.post('/v1/chat-workspace/projects/'+ident+'/backups').json()
    assert client.post('/v1/chat-workspace/projects/'+ident+'/backups/'+backup['id']+'/export',json={'storage_id':storage['id']}).status_code==200
    assert any(r[0]=='PUT' and r[3][:2]==b'PK' for r in seen)

def test_invalid_payload_and_missing_files_are_safe_errors(client):
    assert client.post('/v1/chat-workspace/projects',content=b'broken').status_code==422
    assert client.post('/v1/chat-workspace/projects',json=[]).status_code==422
    assert client.post('/v1/chat-workspace/storages',json={'name':'x','type':'local','path':None}).status_code==422
    ident=project(client)
    assert client.post('/v1/chat-workspace/projects/'+ident+'/files',json={'action':'delete','path':'missing'}).status_code==404
