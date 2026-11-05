"""Composio v3.1 adapter. Credentials/session IDs stay in the owner's store.

No SDK or ambient account credentials. The router user is derived by the host,
not submitted by the browser. Every general execution and account link goes
through the host's exact, single-use confirmation boundary.
"""
import hashlib
import re
from urllib.parse import urlsplit

from fastapi import HTTPException

API = 'https://backend.composio.dev/api/v3.1/tool_router/session'
# Explicit audited reads, not inferred from untrusted remote descriptions/tags.
# An unlisted tool can still be used with the generic confirmed `call` action.
READ_TOOLS = {
    'GMAIL_FETCH_EMAILS', 'GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID',
    'GITHUB_GET_THE_AUTHENTICATED_USER', 'GITHUB_GET_AN_ISSUE',
    'SLACK_SEARCH_ALL', 'SLACK_SEARCH_MESSAGES',
    'GOOGLECALENDAR_EVENTS_LIST', 'GOOGLECALENDAR_EVENTS_GET',
    'GOOGLECALENDAR_LIST_CALENDARS',
}


def toolkits(credentials):
    values = credentials.get('toolkits', '').lower().split(',')
    values = sorted(set(v.strip() for v in values if v.strip()))
    if not 1 <= len(values) <= 30 or any(not re.fullmatch('[a-z][a-z0-9_]{0,63}', v) for v in values):
        raise HTTPException(422, 'Укажите от 1 до 30 приложений Composio через запятую, например gmail,slack,github')
    return values


def prepare(row, owner):
    """Only local identity is accepted; neither user_id nor session_id is a field."""
    toolkits(row['credentials'])
    row['composio'] = {'user_id': 'workbench_' + hashlib.sha256((owner+':'+row['id']).encode()).hexdigest()}


def session_id(row):
    value = row.get('composio', {}).get('session_id', '')
    if not re.fullmatch(r'trs_[A-Za-z0-9_-]{1,180}', value):
        raise HTTPException(409, 'Сначала нажмите «Проверить»: это создаст личную сессию Composio')
    return value


async def create_session(row):
    from .chat_connectors import http
    return await http('POST', API, headers={'x-api-key': row['credentials']['token']}, json={
        'user_id': row['composio']['user_id'],
        'toolkits': {'enable': toolkits(row['credentials'])},
        'instant': False,
        'workbench': {'enable': False},
        'proxy_execute': {'enable': False},
        'manage_connections': {'enable': True, 'enable_connection_removal': False},
    })


async def adapter(row, name, args):
    from .chat_connectors import http, arg
    base = API + '/' + session_id(row)
    headers = {'x-api-key': row['credentials']['token']}
    if name == 'list':
        cursor = arg(args, 'cursor', 1000)
        return await http('GET', base+'/tools', headers=headers,
                          params={'limit': 100, **({'cursor': cursor} if cursor else {})})
    if name == 'connect':
        toolkit = arg(args, 'toolkit', 64).lower()
        if toolkit not in toolkits(row['credentials']):
            raise HTTPException(403, 'Это приложение не включено в данное подключение')
        result = await http('POST', base+'/link', headers=headers, json={'toolkit': toolkit})
        url = result.get('redirect_url', '')
        p = urlsplit(url)
        if p.scheme != 'https' or p.hostname not in {'app.composio.dev', 'connect.composio.dev', 'backend.composio.dev'} or p.username or p.password:
            raise HTTPException(502, 'Composio вернул недопустимую ссылку авторизации')
        # Link is user-facing, not a key. Do not forward link_token or account ACLs.
        return {'authorization_url': url, 'toolkit': toolkit,
                'message': 'Откройте ссылку и проверьте аккаунт и запрашиваемые права. После входа повторите чтение.'}
    if name not in {'read', 'call'}:
        raise HTTPException(404, 'Инструмент Composio не найден')
    slug = arg(args, 'tool_slug', 150)
    if not re.fullmatch('[A-Z][A-Z0-9_]{1,149}', slug) or slug.startswith('COMPOSIO_'):
        raise HTTPException(422, 'Нужен slug инструмента приложения из списка Composio; meta/workbench-инструменты отключены')
    if slug.split('_', 1)[0].lower() not in toolkits(row['credentials']):
        raise HTTPException(403, 'Приложение инструмента не разрешено')
    if name == 'read' and slug not in READ_TOOLS:
        raise HTTPException(403, 'Этот инструмент не входит в проверенный список чтения. Используйте «call» с подтверждением')
    arguments = args.get('arguments', {})
    if not isinstance(arguments, dict):
        raise HTTPException(422, 'arguments должен быть JSON объектом')
    return await http('POST', base+'/execute', headers=headers,
                      json={'tool_slug': slug, 'arguments': arguments})
