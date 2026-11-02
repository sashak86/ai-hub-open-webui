"""Offline runnable adapter smoke: no cloud model, account, or credential needed."""
import hashlib,importlib,sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('SASHA_WORKBENCH_DATA',str(tmp_path/'data'))
    monkeypatch.setenv('SASHA_WORKBENCH_PROJECTS',str(tmp_path/'projects'))
    monkeypatch.setenv('HUB_CHAT_TOKEN','synthetic-offline-token-for-test-only')
    from hub_console import config
    importlib.reload(config)
    from hub_console import standalone
    importlib.reload(standalone)
    with TestClient(standalone.create_app()) as c:
        c.headers.update({'Authorization':'Bearer synthetic-offline-token-for-test-only','X-WebUI-User':'a'*64})
        yield c

def test_authentication_and_workspace_binary_roundtrip(client):
    assert client.get('/ai-hub/health').status_code==200
    assert client.get('/v1/chat-workspace',headers={'Authorization':'bad'}).status_code==401
    project=client.post('/v1/chat-workspace/projects',json={'name':'Offline'});assert project.status_code==200,project.text
    ident=project.json()['id'];base='/v1/chat-workspace/projects/'+ident
    assert client.put(base+'/file?path=test.txt',content=b'offline test').status_code==200
    assert client.get(base+'/file?path=test.txt').content==b'offline test'
    backup=client.post(base+'/backups');assert backup.status_code==200
    assert client.get(base+'/backups/'+backup.json()['id']).content[:2]==b'PK'
    client.headers['X-WebUI-User']='b'*64
    assert client.get(base+'/files').status_code==404

def test_durable_job_complete_offline(client,monkeypatch):
    from hub_console import chat_jobs as jobs,chat_worker as worker
    monkeypatch.setattr(jobs,'model_catalog',lambda request:[{'id':'codex/offline','provider':'codex','available':True}])
    monkeypatch.setattr(jobs,'start_worker',lambda *args:None)
    def native(desc,native,events,cancelled):
        events.emit({'type':'text','text':'Offline provider completed'})
        return {'native_id':'offline-provider'}
    monkeypatch.setattr(worker,'native_turn',native)
    r=client.post('/v1/jobs',json={'model':'codex/offline','chat_id':'offline','message_id':'one','text':'Hello'})
    assert r.status_code==200,r.text
    ident=r.json()['id'];ref=worker.load(client.app.state.chat_jobs_root/'index'/(ident+'.json'))
    folder=client.app.state.chat_jobs_root/ref['owner']/ref['chat']/ref['folder']
    worker.main(folder.parent)
    assert client.get('/v1/jobs/'+ident).json()['status']=='completed'
    events=client.get('/v1/jobs/'+ident+'/events').text
    assert 'Offline provider completed' in events
    assert '"type": "done"' in events

def test_browser_proxy_validates_native_jwt_and_preserves_binary(client):
    import httpx
    original=client.app.state.http
    async def handler(request):
        if request.url.path=='/api/v1/auths/':
            if request.headers.get('authorization')=='Bearer browser-test':return httpx.Response(200,json={'id':'offline-user','role':'admin'})
            return httpx.Response(401)
        return httpx.Response(404)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    headers={'Authorization':'Bearer browser-test'}
    project=client.post('/ai-hub/api/workspace/projects',headers=headers,json={'name':'Proxy'});assert project.status_code==200,project.text
    base='/ai-hub/api/workspace/projects/'+project.json()['id']
    assert client.put(base+'/file?path=proxy.txt',headers=headers,content=b'raw file').status_code==200
    file=client.get(base+'/file?path=proxy.txt',headers=headers);assert file.content==b'raw file';assert 'content-disposition' in file.headers
    assert client.get('/ai-hub/api/workspace',headers={'Authorization':'Bearer invalid'}).status_code==401
    assert client.get('/ai-hub/api/workspace',headers={**headers,'Origin':'https://evil.invalid'}).status_code==403
    client.app.state.http=original
