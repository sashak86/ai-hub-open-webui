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

def test_exact_native_pipe_discovery_without_browser_owner_header(client,monkeypatch):
    import asyncio,importlib.util,httpx
    from hub_console import agent_bridge,chat_providers
    monkeypatch.setattr(agent_bridge,'available_models',lambda request:[{'id':'model/codex/offline','name':'Offline Codex'}])
    monkeypatch.setattr(chat_providers,'discover',lambda:[])
    del client.headers['X-WebUI-User']
    response=client.get('/v1/jobs/models');assert response.status_code==200,response.text
    assert any(row['id']=='codex/offline' for row in response.json()['data'])
    source=Path(__file__).resolve().parents[1]/'deploy/chat_pipe.py'
    spec=importlib.util.spec_from_file_location('public_chat_pipe',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    pipe=module.Pipe()
    monkeypatch.setattr(pipe,'client',lambda:httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),base_url='http://bridge',headers={'Authorization':client.headers['Authorization']}))
    rows=asyncio.run(pipe.pipes())
    assert any(row['id']=='codex/offline' for row in rows)

def test_configured_origin_and_input_body_cap(client,monkeypatch):
    from hub_console.standalone import valid_origin
    assert not valid_origin('https://not-allowed.invalid','http://localhost:8088')
    monkeypatch.setenv('HUB_PUBLIC_ORIGINS','https://workbench.example.com')
    assert valid_origin('https://workbench.example.com','http://localhost:8088')
    response=client.put('/v1/harness/settings',content=b'x'*600000)
    assert response.status_code==413

def test_connected_api_models_get_owner_only_native_presets(client,monkeypatch):
    import httpx
    from hub_console import chat_harness
    original=client.app.state.http;records=[]
    async def handler(request):
        if request.url.path=='/api/v1/auths/':return httpx.Response(200,json={'id':'preset-owner','role':'admin'})
        if request.url.path=='/api/models':return httpx.Response(200,json={'data':[{'id':'ai_hub.codex/offline'}]})
        if request.method=='GET':return httpx.Response(404)
        if request.url.path=='/api/v1/models/create':
            import json
            records.append(json.loads(request.content));return httpx.Response(200,json={'ok':True})
        return httpx.Response(404)
    async def connected(request,provider,body):return {'ok':True,'status':'connected','models':['test-model']}
    monkeypatch.setattr(chat_harness,'connect',connected)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=client.post('/ai-hub/api/harness/connect/openai',headers={'Authorization':'Bearer browser-test'},json={'action':'test'})
    assert result.status_code==200,result.text
    assert result.json()['model_records']==[records[0]['id']]
    assert records[0]['params']['custom_params']['hub_api_model']=='api/openai/test-model'
    assert records[0]['access_grants']==[{'principal_type':'user','principal_id':'preset-owner','permission':'read'}]
    client.app.state.http=original
