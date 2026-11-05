"""Standalone parallel endpoint and authenticated proxy; no external providers."""
import hashlib
from .test_standalone import client
from hub_console import chat_jobs as j,chat_worker as w

def test_parallel_independent_restore_cancel_and_owner(client,monkeypatch):
    monkeypatch.setattr(j,'model_catalog',lambda request:[{'id':'codex/offline','available':True}])
    monkeypatch.setattr(j,'start_worker',lambda *args:None)
    body={'request_id':'1'*32,'model':'codex/offline','text':'Parallel offline'}
    a=client.post('/v1/parallel',json=body);assert a.status_code==200,a.text
    ident=a.json()['id'];assert client.post('/v1/parallel',json=body).json()['id']==ident
    ref=w.load(client.app.state.chat_jobs_root/'index'/(ident+'.json'));folder=client.app.state.chat_jobs_root/ref['owner']/ref['chat']/ref['folder']
    request=w.load(folder/'request.json');assert request['parallel'] and request['history']==[] and request['cwd']==str(folder/'workspace')
    assert (folder/'workspace/AGENTS.md').is_file()
    assert client.get('/v1/parallel').json()['jobs'][0]['id']==ident
    assert client.post('/v1/parallel/'+ident+'/cancel',json={}).status_code==200
    assert (folder/'cancel').exists()
    client.headers['X-WebUI-User']='b'*64
    assert client.get('/v1/parallel').json()['jobs']==[]
    assert client.get('/v1/parallel/'+ident).status_code==404

def test_native_browser_proxy_parallel_routes_and_asset(client,monkeypatch):
    import httpx
    monkeypatch.setattr(j,'model_catalog',lambda request:[{'id':'codex/offline','available':True}]);monkeypatch.setattr(j,'start_worker',lambda *args:None)
    original=client.app.state.http
    async def handler(request):
        return httpx.Response(200,json={'id':'offline-user','role':'admin'}) if request.url.path=='/api/v1/auths/' else httpx.Response(404)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    headers={'Authorization':'Bearer browser-test'}
    a=client.post('/ai-hub/api/parallel',headers=headers,json={'request_id':'2'*32,'model':'codex/offline','text':'Native parallel'})
    assert a.status_code==200,a.text
    ident=a.json()['id'];assert client.get('/ai-hub/api/parallel',headers=headers).json()['jobs'][0]['id']==ident
    assert client.get('/ai-hub/api/parallel/'+ident,headers=headers).status_code==200
    assert client.post('/ai-hub/api/parallel/'+ident+'/cancel',headers=headers,json={}).status_code==200
    assert client.get('/ai-hub/parallel-ui.js').status_code==200
    client.app.state.http=original
