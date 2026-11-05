"""Owner-only TLS gateways for separately hosted local applications.

Application origins never share Open WebUI localStorage. Opaque, app-bound
HttpOnly cookies are exchanged only after validating a native WebUI session.
No browser cookies, Authorization or identity headers reach the native apps.
"""
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect

from .module_security import NetworkGuard, same_origin

CONFIG_PATH = Path(os.environ.get('SASHA_WORKBENCH_MODULES_CONFIG', str(Path.home()/'.config/sasha-workbench/local-modules.json')))
LOG = logging.getLogger(__name__)
COOKIE_TTL = 3600
MAX_BODY = 128 * 1024 * 1024
REQUEST_HEADERS = {'accept', 'accept-encoding', 'content-type', 'content-length',
                   'user-agent', 'range', 'if-none-match', 'if-modified-since', 'cache-control'}
DROP_RESPONSE = {'set-cookie', 'connection', 'keep-alive', 'transfer-encoding',
                 'server', 'access-control-allow-origin', 'access-control-allow-credentials'}


def load_config(path=None):
    path = CONFIG_PATH if path is None else path
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, PermissionError):
        return {'modules': {}, 'owners': []}
    owners = raw.get('owners', [])
    if not isinstance(owners, list) or any(not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in owners):
        raise ValueError('Invalid module owner allowlist')
    modules = raw.get('modules', {})
    ports = set()
    for name, item in modules.items():
        if name not in {'voicestudio', 'openmuse'}:
            raise ValueError('Unknown local module')
        origin = urlsplit(item['upstream'])
        if origin.scheme != 'http' or origin.hostname != '127.0.0.1' or not origin.port or origin.path not in {'', '/'} or origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError('Module upstream must be a loopback HTTP origin')
        port = item['port']
        if type(port) is not int or not 1025 <= port <= 65535 or port in {8445, 8446} or port in ports:
            raise ValueError('Invalid local module TLS port')
        ports.add(port)
        if item.get('secret_header') and not re.fullmatch('[A-Za-z][A-Za-z0-9-]{1,100}', item['secret_header']):
            raise ValueError('Invalid module service header')
        if not item.get('health_path', '/health').startswith('/'):
            raise ValueError('Invalid health route')
    return raw


class LocalModules:
    def __init__(self, config, settings):
        self.config = config
        self.settings = settings
        self.sessions = {}
        self.servers = []
        self.listeners = set()
        self.http = None

    async def authenticate(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or len(authorization) > 4096:
            raise HTTPException(401, 'Войдите в Open WebUI и откройте локальный модуль снова.')
        result = await self.http.send(httpx.Request('GET', os.environ.get('WEBUI_INTERNAL_URL', 'http://127.0.0.1:3080').rstrip('/') + '/api/v1/auths/', headers={'Authorization': authorization}, extensions={'timeout': httpx.Timeout(15).as_dict()}))
        if result.status_code != 200:
            raise HTTPException(401, 'Сессия Open WebUI истекла. Откройте модуль из чата снова.')
        user = result.json()
        uid = user.get('id')
        if not isinstance(uid, str) or user.get('role') not in {'user', 'admin'}:
            raise HTTPException(403, 'Нет доступа к локальным модулям.')
        owner = hashlib.sha256(uid.encode()).hexdigest()
        if owner not in self.config.get('owners', []):
            raise HTTPException(403, 'Этот локальный модуль доступен только его владельцу.')
        return owner

    def module(self, name):
        item = self.config.get('modules', {}).get(name)
        if not item:
            raise HTTPException(404, 'Локальный модуль не настроен.')
        return item

    def upstream_headers(self, item, headers):
        result = {k: v for k, v in headers.items() if k.lower() in REQUEST_HEADERS}
        if item.get('secret_header'):
            secret = Path(item['secret_file']).read_text().strip()
            if not secret or len(secret) > 4096:
                raise HTTPException(503, 'Ключ локального модуля не настроен.')
            result[item['secret_header']] = secret
        return result

    async def health(self, item):
        try:
            result = await self.http.send(httpx.Request('GET', item['upstream'].rstrip('/') + item.get('health_path', '/health'),
                                         headers=self.upstream_headers(item, {}), extensions={'timeout': httpx.Timeout(4).as_dict()}))
            return result.status_code == 200
        except (OSError, ValueError, httpx.HTTPError, HTTPException):
            return False

    async def catalog(self):
        rows = []
        for name, item in self.config.get('modules', {}).items():
            rows.append({'id': name, 'name': item.get('name', name),
                         'description': item.get('description', ''),
                         'available': await self.health(item) and (self.settings.testing or name in self.listeners), 'port': item['port'],
                         'local': True, 'notice': item.get('notice', '')})
        return {'modules': rows}

    async def session(self, name, cookies):
        token = cookies.get('hub_local_' + name, '')
        record = self.sessions.get(token)
        if not record or record['module'] != name or record['expires'] <= time.time():
            self.sessions.pop(token, None)
            raise HTTPException(401, 'Войдите в Open WebUI и откройте локальный модуль снова.')
        try:
            owner = await self.authenticate(record['authorization'])
        except HTTPException:
            self.sessions.pop(token, None)
            raise
        if owner != record['owner']:
            self.sessions.pop(token, None)
            raise HTTPException(403, 'Неверный владелец локального модуля.')
        return record

    def gateway(self, name):
        item = self.module(name)
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        app.add_middleware(NetworkGuard, networks=self.config.get('networks', ['127.0.0.0/8', '::1/128']), settings=self.settings)

        @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
        async def proxy(path: str, request: Request):
            await self.session(name, request.cookies)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('origin') != 'https://' + request.headers.get('host', ''):
                raise HTTPException(403, 'Неверный источник запроса локального модуля.')
            if request.method == 'OPTIONS':
                raise HTTPException(403, 'Межсайтовый доступ к локальному модулю запрещён.')
            length = request.headers.get('content-length', '')
            if length.isdigit() and int(length) > MAX_BODY:
                raise HTTPException(413, 'Файл превышает лимит 128 МБ.')
            size = 0
            async def body():
                nonlocal size
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise HTTPException(413, 'Файл превышает лимит 128 МБ.')
                    yield chunk
            url = httpx.URL(item['upstream'].rstrip('/') + '/' + path, query=request.url.query.encode())
            outgoing = httpx.Request(request.method, url, headers=self.upstream_headers(item, request.headers), content=body())
            try:
                result = await self.http.send(outgoing, stream=True)
            except httpx.HTTPError:
                raise HTTPException(502, 'Локальный модуль временно недоступен.') from None
            async def stream():
                try:
                    async for chunk in result.aiter_raw():
                        yield chunk
                finally:
                    await result.aclose()
            response = StreamingResponse(stream(), status_code=result.status_code,
                                         background=BackgroundTask(result.aclose))
            headers = []
            for k, v in result.headers.multi_items():
                if k.lower() in DROP_RESPONSE:
                    continue
                if k.lower() == 'location':
                    if v.startswith(item['upstream']):
                        v = v[len(item['upstream']):] or '/'
                    elif urlsplit(v).scheme or v.startswith('//'):
                        await result.aclose()
                        raise HTTPException(502, 'Внешний переход локального модуля запрещён.')
                headers.append((k.encode('latin-1'), v.encode('latin-1')))
            headers.extend([(b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer'),
                            (b'x-frame-options', b'SAMEORIGIN'), (b'content-security-policy', b"frame-ancestors 'self'")])
            response.raw_headers = headers
            return response

        @app.websocket('/{path:path}')
        async def socket(path: str, ws: WebSocket):
            try:
                record = await self.session(name, ws.cookies)
                if ws.headers.get('origin') != 'https://' + ws.headers.get('host', ''):
                    raise HTTPException(403, 'Invalid origin')
            except HTTPException:
                await ws.close(code=1008)
                return
            target = item['upstream'].replace('http://', 'ws://', 1).rstrip('/') + '/' + path
            if ws.url.query:
                target += '?' + ws.url.query
            try:
                async with connect(target, additional_headers=self.upstream_headers(item, {}), max_size=8_000_000,
                                   origin=None, proxy=None) as upstream:
                    await ws.accept()
                    def active():
                        token = ws.cookies.get('hub_local_' + name, '')
                        if self.sessions.get(token) is not record or record['expires'] <= time.time():
                            raise HTTPException(401, 'Module session expired')
                    async def monitor():
                        while True:
                            await asyncio.sleep(5)
                            await self.session(name, ws.cookies)
                    async def relay_down():
                        while True:
                            event = await ws.receive()
                            if event['type'] == 'websocket.disconnect':
                                break
                            active()
                            await upstream.send(event.get('text') if event.get('text') is not None else event.get('bytes', b''))
                    async def relay_up():
                        async for event in upstream:
                            active()
                            if isinstance(event, str):
                                await ws.send_text(event)
                            else:
                                await ws.send_bytes(event)
                    tasks = [asyncio.create_task(relay_down()), asyncio.create_task(relay_up()), asyncio.create_task(monitor())]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        with contextlib.suppress(Exception):
                            await ws.close(code=1000)
            except Exception:
                with contextlib.suppress(Exception):
                    await ws.close(code=1011)
        return app

    async def start(self, client):
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), trust_env=False)
        if self.settings.testing:
            return
        import uvicorn
        for name, item in self.config.get('modules', {}).items():
            cfg = uvicorn.Config(self.gateway(name), host=self.config.get('host', '127.0.0.1'), port=item['port'],
                                 ssl_certfile=self.config['tls_cert'],
                                 ssl_keyfile=self.config['tls_key'],
                                 lifespan='off', proxy_headers=False, server_header=False, access_log=False,
                                 log_level='warning', loop='asyncio', timeout_graceful_shutdown=3)
            server = uvicorn.Server(cfg)
            try:
                cfg.load()
                server.lifespan = cfg.lifespan_class(cfg)
                await server.startup()
                if server.started:
                    self.servers.append(server)
                    self.listeners.add(name)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    raise
                LOG.error('Local module listener could not start: %s', name)

    async def stop(self):
        for server in self.servers:
            with contextlib.suppress(Exception):
                server.should_exit = True
                await server.shutdown()
        self.sessions.clear()
        self.listeners.clear()
        if self.http is not None:
            await self.http.aclose()


def install(app, settings, module_config=None):
    manager = LocalModules(load_config() if module_config is None else module_config, settings)
    app.state.local_modules = manager

    async def owner(request):
        if not same_origin(request.headers, settings):
            raise HTTPException(403, 'Invalid Origin')
        return await manager.authenticate(request.headers.get('authorization', ''))

    @app.get('/ai-hub/api/local-modules')
    async def catalog(request: Request):
        await owner(request)
        return await manager.catalog()

    @app.post('/ai-hub/api/local-modules/{name}/open')
    async def open_module(name: str, request: Request):
        uid = await owner(request)
        item = manager.module(name)
        if not (settings.testing or name in manager.listeners) or not await manager.health(item):
            raise HTTPException(503, 'Локальный модуль запускается или временно недоступен.')
        now = time.time()
        manager.sessions = {k: v for k, v in manager.sessions.items() if v['expires'] > now}
        # Re-opening an app revokes older handles for that owner/application.
        manager.sessions = {k: v for k, v in manager.sessions.items() if (v['owner'], v['module']) != (uid, name)}
        if len(manager.sessions) >= 100:
            raise HTTPException(429, 'Слишком много открытых модулей.')
        token = secrets.token_urlsafe(32)
        manager.sessions[token] = {'owner': uid, 'module': name,
                                   'authorization': request.headers['authorization'], 'expires': now + COOKIE_TTL}
        host = request.url.hostname
        if ':' in host:
            host = '[' + host + ']'
        response = JSONResponse({'url': 'https://' + host + ':' + str(item['port']) + '/', 'expires_in': COOKIE_TTL})
        response.set_cookie('hub_local_' + name, token, max_age=COOKIE_TTL, httponly=True, secure=True, samesite='strict', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/ai-hub/api/local-modules/close')
    async def close_modules(request: Request):
        uid = await owner(request)
        manager.sessions = {k: v for k, v in manager.sessions.items() if v['owner'] != uid}
        response = JSONResponse({'ok': True})
        for name in manager.config.get('modules', {}):
            response.delete_cookie('hub_local_' + name, path='/', httponly=True, secure=True, samesite='strict')
        return response
    return manager
