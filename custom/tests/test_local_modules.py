import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hub_console.config import Settings
from hub_console.webui_local_modules import install, load_config

OWNER = hashlib.sha256(b'owner').hexdigest()


class Bytes(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'module-data'


@pytest.fixture
def modules(tmp_path):
    secret = tmp_path/'key'; secret.write_text('server-only-service-secret')
    config = {'owners': [OWNER], 'modules': {
        'openmuse': {'name': 'OpenMuse', 'upstream': 'http://127.0.0.1:8797', 'port': 8474,
                     'secret_header': 'X-OpenMuse-Service-Key', 'secret_file': str(secret)},
        'voicestudio': {'name': 'VoiceStudio', 'upstream': 'http://127.0.0.1:8771', 'port': 8473}}}
    app = FastAPI()
    manager = install(app, Settings(testing=True), config)
    calls = []
    def upstream(request):
        calls.append(request)
        if request.url.port == 3080:
            uid = 'owner' if request.headers.get('authorization') == 'Bearer owner-token' else 'foreign'
            if request.headers.get('authorization') == 'Bearer revoked':
                return httpx.Response(401)
            return httpx.Response(200, json={'id': uid, 'role': 'admin'})
        if request.url.path == '/health':
            return httpx.Response(200, json={'ok': True})
        if request.url.path == '/redirect':
            return httpx.Response(302, headers={'location': 'https://evil.example/path'})
        return httpx.Response(200, stream=Bytes(), headers={'content-type': 'application/octet-stream', 'set-cookie':'native-secret=never-forward', 'access-control-allow-origin':'*'})
    manager.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream), trust_env=False)
    with TestClient(app, base_url='https://testserver:8446') as client:
        yield client, manager, calls
    import asyncio
    asyncio.run(manager.http.aclose())


def open_app(client, name='openmuse'):
    return client.post('/ai-hub/api/local-modules/'+name+'/open', headers={'Authorization': 'Bearer owner-token', 'Origin':'https://testserver:8446'})


def test_exchange_owner_bound_app_cookie_and_no_secret_in_catalog(modules):
    client, manager, calls = modules
    assert client.get('/ai-hub/api/local-modules').status_code == 401
    assert client.get('/ai-hub/api/local-modules', headers={'Authorization':'Bearer foreign-token'}).status_code == 403
    result = client.get('/ai-hub/api/local-modules', headers={'Authorization':'Bearer owner-token'})
    assert result.status_code == 200
    assert 'server-only-service-secret' not in result.text and 'secret_file' not in result.text
    r = open_app(client)
    assert r.status_code == 200 and r.json()['url'] == 'https://testserver:8474/'
    cookie = r.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
    assert 'owner-token' not in cookie
    token = client.cookies.get('hub_local_openmuse')
    with TestClient(manager.gateway('openmuse'), base_url='https://testserver:8474') as gateway:
        assert gateway.get('/').status_code == 401
        gateway.cookies.set('hub_local_openmuse', token)
        r = gateway.get('/assets/a.js', headers={'Authorization':'Bearer must-not-pass','Cookie':'hub_local_openmuse='+token+'; other-secret=private', 'X-OpenMuse-Service-Key':'spoof'})
        assert r.status_code == 200 and r.content == b'module-data'
        forwarded = calls[-1]
        assert forwarded.headers['X-OpenMuse-Service-Key'] == 'server-only-service-secret'
        assert 'cookie' not in forwarded.headers and 'authorization' not in forwarded.headers
        assert 'set-cookie' not in r.headers and 'access-control-allow-origin' not in r.headers
        assert r.headers['x-frame-options'] == 'SAMEORIGIN'
        # Native upstream cookie jars must never leak across apps or to WebUI.
        gateway.get('/again')
        assert all('cookie' not in request.headers for request in calls)


def test_csrf_cookie_wrong_app_revocation_and_all_nested_paths(modules):
    client, manager, calls = modules
    assert client.post('/ai-hub/api/local-modules/openmuse/open', headers={'Authorization':'Bearer owner-token','Origin':'https://evil.example'}).status_code == 403
    open_app(client)
    token=client.cookies.get('hub_local_openmuse')
    with TestClient(manager.gateway('openmuse'), base_url='https://testserver:8474') as gateway:
        gateway.cookies.set('hub_local_openmuse',token)
        assert gateway.post('/api/files',headers={'Origin':'https://evil.example'}).status_code == 403
        assert gateway.post('/api/files').status_code == 403
        assert gateway.post('/api/files',headers={'Origin':'https://testserver:8474'},content=b'abc').status_code == 200
        assert gateway.get('/redirect',follow_redirects=False).status_code == 502
        manager.sessions[token]['authorization']='Bearer revoked'
        assert gateway.get('/_expo/static/js/bundle.js').status_code == 401
    open_app(client)
    token=client.cookies.get('hub_local_openmuse')
    with TestClient(manager.gateway('voicestudio'), base_url='https://testserver:8473') as gateway:
        gateway.cookies.set('hub_local_voicestudio',token)
        assert gateway.get('/api/files').status_code == 401
    client.post('/ai-hub/api/local-modules/close',headers={'Authorization':'Bearer owner-token'})
    assert not manager.sessions


def test_upload_limit_and_websocket_guard(modules):
    client,manager,calls=modules
    open_app(client)
    token=client.cookies.get('hub_local_openmuse')
    with TestClient(manager.gateway('openmuse'), base_url='https://testserver:8474') as gateway:
        gateway.cookies.set('hub_local_openmuse',token)
        assert gateway.post('/api/files',headers={'Origin':'https://testserver:8474','Content-Length':str(129*1024*1024)}).status_code == 413
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with gateway.websocket_connect('/ws',headers={'Origin':'https://evil.example'}):pass


def test_config_rejects_nonloopback_and_invalid_ports(tmp_path):
    p=tmp_path/'modules.json'
    for upstream, port in [('http://169.254.169.254',8474),('https://127.0.0.1:8797',8474),('http://127.0.0.1:8797',8446)]:
        p.write_text(json.dumps({'owners':[OWNER],'modules':{'openmuse':{'upstream':upstream,'port':port}}}))
        with pytest.raises(ValueError):load_config(p)


def test_websocket_relays_text_binary_and_revokes_on_close(modules, monkeypatch):
    import asyncio
    from hub_console import webui_local_modules as native
    from starlette.websockets import WebSocketDisconnect
    client, manager, calls = modules
    seen = {}
    class Peer:
        async def __aenter__(self):
            self.queue=asyncio.Queue()
            return self
        async def __aexit__(self, *args):pass
        async def send(self, value):await self.queue.put(value)
        def __aiter__(self):return self
        async def __anext__(self):return await self.queue.get()
    def connect(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Peer()
    monkeypatch.setattr(native,'connect',connect)
    open_app(client)
    token=client.cookies.get('hub_local_openmuse')
    with TestClient(manager.gateway('openmuse'),base_url='https://testserver:8474') as gateway:
        gateway.cookies.set('hub_local_openmuse',token)
        with gateway.websocket_connect('wss://testserver:8474/ws/events',headers={'Origin':'https://testserver:8474','Authorization':'Bearer private','Cookie':'hub_local_openmuse='+token+'; unrelated=secret'}) as ws:
            ws.send_text('hello');assert ws.receive_text()=='hello'
            ws.send_bytes(b'voice');assert ws.receive_bytes()==b'voice'
            assert seen['additional_headers']=={'X-OpenMuse-Service-Key':'server-only-service-secret'}
            client.post('/ai-hub/api/local-modules/close',headers={'Authorization':'Bearer owner-token'})
            ws.send_text('must-not-forward')
            with pytest.raises(WebSocketDisconnect):ws.receive_text()


def test_gateway_rejects_nonallowed_network_before_native_auth(modules):
    client,manager,calls=modules
    count=len(calls)
    with TestClient(manager.gateway('openmuse'),base_url='https://testserver:8474',client=('198.51.100.25',1234)) as outsider:
        assert outsider.get('/assets/app.js').status_code==403
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with outsider.websocket_connect('wss://testserver:8474/ws',headers={'Origin':'https://testserver:8474'}):pass
    assert len(calls)==count
