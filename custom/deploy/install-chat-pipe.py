#!/usr/bin/env python3
"""Install/update the reviewed local Pipe through WebUI's authenticated admin API.

Run as the same normal user as the standalone bridge: python3 custom/deploy/install-chat-pipe.py. No credentials or chat bodies are printed.
The function is idempotent; private backup contains only previous function/model config.
"""
import json
import os
from pathlib import Path
from datetime import datetime
import httpx

SOURCE = Path(__file__).with_name('chat_pipe.py')


def install(base='http://127.0.0.1:3080', env_path=str(Path(__file__).resolve().parents[1]/'runtime/private.env')):
    env = {}
    for line in Path(env_path).read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            env[key] = value.strip().strip('"').strip("'")
    with httpx.Client(base_url=base, timeout=45, trust_env=False) as client:
        auth = client.post('/api/v1/auths/signin', json={
            'email': env['WEBUI_ADMIN_EMAIL'], 'password': env['WEBUI_ADMIN_PASSWORD']})
        auth.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + auth.json()['token']
        existing = client.get('/api/v1/functions/id/ai_hub')
        backup_dir = Path.home()/'.local/state/sasha-workbench/pipe-backups'
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = backup_dir / ('pipe-before-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
        if existing.is_success:
            fd = os.open(backup, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(existing.json(), file)
        elif existing.status_code not in {400, 401, 404}:
            existing.raise_for_status()
        payload = {'id': 'ai_hub', 'name': 'AI Hub — агенты', 'content': SOURCE.read_text(),
                   'meta': {'description': 'Codex и Claude Code: модели, ход работы, очередь и файлы.'}}
        route = '/api/v1/functions/id/ai_hub/update' if existing.is_success else '/api/v1/functions/create'
        result = client.post(route, json=payload)
        result.raise_for_status()
        if not result.json().get('is_active'):
            client.post('/api/v1/functions/id/ai_hub/toggle').raise_for_status()
        stop_existing=client.get('/api/v1/functions/id/ai_hub_stop')
        stop_payload={'id':'ai_hub_stop','name':'Остановить агента','content':SOURCE.with_name('chat_stop_action.py').read_text(),
                      'meta':{'description':'Останавливает выполнение заданий текущего чата.'}}
        route='/api/v1/functions/id/ai_hub_stop/update' if stop_existing.is_success else '/api/v1/functions/create'
        stop=client.post(route,json=stop_payload);stop.raise_for_status()
        if not stop.json().get('is_active'):client.post('/api/v1/functions/id/ai_hub_stop/toggle').raise_for_status()
        # Native Action availability gate requires assignment for ordinary owners.
        if not stop.json().get('is_global'):client.post('/api/v1/functions/id/ai_hub_stop/toggle/global').raise_for_status()
        # Keep native uploads/previews on and bypass generic RAG. Dynamic Pipe IDs
        # are real models; no virtual session-creation selectors enter this picker.
        models = client.get('/api/models')
        models.raise_for_status()
        selected = [m for m in models.json().get('data', []) if m['id'].startswith('ai_hub.')]
        if not selected:
            raise RuntimeError('Pipe installed but model catalog empty; verify bridge jobs scope/config')
        users_response = client.get('/api/v1/users/all')
        users_response.raise_for_status()
        users=users_response.json()
        if isinstance(users,dict):users=users.get('users',users.get('data',[]))
        if not isinstance(users,list):raise RuntimeError('Unsupported users response')
        grants = [{'principal_type': 'user', 'principal_id': user['id'], 'permission': 'read'}
                  for user in users if isinstance(user,dict) and user.get('role') in {'admin', 'user'}]
        with httpx.Client(verify=True, timeout=30, trust_env=False) as bridge:
            catalog_response = bridge.get(env.get('HUB_CHAT_URL','http://127.0.0.1:8088').rstrip('/')+'/v1/jobs/models', headers={'Authorization': 'Bearer ' + env['HUB_CHAT_TOKEN']})
            catalog_response.raise_for_status()
            catalog = {entry['id']: entry for entry in catalog_response.json().get('data', [])}
        for model in selected:
            mid = model['id']
            record = client.get('/api/v1/models/model', params={'id': mid})
            current = record.json() if record.is_success else {}
            meta = current.get('meta') or {}
            descriptor = catalog.get(mid.split('.', 1)[-1], {})
            caps = descriptor.get('caps') or descriptor.get('capabilities') or {}
            vision = caps.get('image', caps.get('vision', descriptor.get('provider') in {'codex', 'claude'}))
            meta['capabilities'] = {**(meta.get('capabilities') or {}), 'vision': bool(vision),
                'file_upload': True, 'file_context': False, 'builtin_tools': False,
                'web_search': False, 'code_interpreter': False, 'image_generation': False}
            obj = {'id': mid, 'base_model_id': None, 'name': model['name'], 'meta': meta,
                   'params': current.get('params') or {},
                   'access_grants': grants}
            # Explicit grants to existing approved users; never disables auth.
            route = '/api/v1/models/model/update' if record.is_success else '/api/v1/models/create'
            response = client.post(route, params={'id': mid} if record.is_success else None, json=obj)
            response.raise_for_status()
        print('Installed native AI Hub Pipe; available models:', len(selected))


if __name__ == '__main__':
    install()
