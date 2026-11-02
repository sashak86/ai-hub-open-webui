"""Existing-user OpenCode agents, with whitelisted metadata and JSONL output.

Uses existing OpenCode free catalog, configured gateway models and already loaded
Ollama. Never provisions credentials, updates CLIs, emits raw errors/tool arguments,
or loads an unloaded local model. Gateway retains its existing retry policy.
Catalog/configuration availability is separate from verified execution; successful
turns store verified_at, quota failures store a five-minute retry cooldown.
"""
from __future__ import annotations

import json
import hashlib
import urllib.request
import uuid
import math
import os
import pwd
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import time

from .config import load_settings
from .system import as_user_kwargs

_CACHE: tuple[float, list[dict]] = (0, [])
MAX_RECORD = 8_000_000


def _context():
    username = load_settings().desktop_user if os.geteuid() == 0 else pwd.getpwuid(os.geteuid()).pw_name
    kw = as_user_kwargs(username)
    env = dict(kw.get('env', os.environ))
    env['OPENCODE_DISABLE_AUTOUPDATE'] = 'true'
    exe = shutil.which('opencode', path=env.get('PATH'))
    kw['env'] = env
    return exe, kw


def _parse_catalog(text):
    """Verbose CLI output is provider/model then one JSON object per model."""
    rest = text
    decoder = json.JSONDecoder()
    rows = []
    while rest.strip():
        model, _, rest = rest.lstrip().partition('\n')
        try:
            raw, length = decoder.raw_decode(rest.lstrip())
        except (ValueError, TypeError):
            break
        rest = rest.lstrip()[length:]
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]+/[a-zA-Z0-9_.:/-]+', model):
            continue
        provider = model.split('/', 1)[0]
        cost = raw.get('cost', {})
        # Existing local configuration and explicitly zero-cost Zen models only.
        if provider != 'ollama' and not (provider == 'opencode' and cost.get('input') == 0 and cost.get('output') == 0):
            continue
        inputs = raw.get('capabilities', {}).get('input', {})
        limits = raw.get('limit', {})
        rows.append({
            'id': ('ollama/' + model.split('/', 1)[1]) if provider == 'ollama' else 'opencode/' + model,
            'name': str(raw.get('name') or model)[:120],
            'provider': 'ollama' if provider == 'ollama' else 'opencode',
            'available': raw.get('status') != 'deprecated',
            'availability': 'catalog',
            'reason': 'Доступен в установленном каталоге; квота проверяется при запросе.',
            'capabilities': {**{kind: inputs.get(kind) is True for kind in ('text', 'image', 'audio', 'video', 'pdf')}, 'tools': True},
            'limits': {key: limits[key] for key in ('context', 'input', 'output') if isinstance(limits.get(key), int) and 0 < limits[key] < 100_000_000},
            'metadata_source': 'installed_opencode_catalog',
        })
    return rows


def _state_root():
    _, kw = _context()
    return Path(kw['env']['HOME']) / '.local/state/hub-chat/providers'


def _status(model, write=False):
    root = _state_root()
    path = root / ('status-' + hashlib.sha256(model.encode()).hexdigest() + '.json')
    try:
        if write:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            value = {'verified_at': time.time()} if write == 'verified' else {'reason': 'quota_exhausted', 'until': time.time() + 300}
            temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(value, f)
            temp.replace(path)
            return value
        if path.is_symlink() or path.stat().st_mode & 0o022:
            return {}
        value = json.loads(path.read_text())
        return value if value.get('until', 0) > time.time() or isinstance(value.get('verified_at'), (int, float)) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _get_local(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read(2_000_000))


def _other_models():
    rows = []
    try:
        exe, kw = _context()
        home = Path(kw['env']['HOME'])
        cfg = home / '.config/ai-gateway'
        summary = json.loads((cfg / 'models.json').read_text())
        if (cfg / 'master.env').is_file() and shutil.which('claude', path=kw['env'].get('PATH')):
            for name, item in summary.get('models', {}).items():
                # Local alias may load an unloaded model; use direct loaded-only Ollama instead.
                if str(item.get('model', '')).startswith('ollama'):
                    continue
                if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
                    continue
                rows.append({'id': 'gateway/' + name, 'name': str(item.get('model', name))[:120],
                             'provider': 'gateway', 'available': True, 'availability': 'configured',
                             'reason': 'Настроенный шлюз; доступность и квота проверяются при запросе.',
                             'capabilities': {'text': True, 'image': False, 'tools': True},
                             'limits': {'context': item['ctx']} if isinstance(item.get('ctx'), int) else {},
                             'metadata_source': 'configured_gateway_models'})
    except (OSError, ValueError, TypeError):
        pass
    try:
        for item in _get_local('http://127.0.0.1:11434/api/ps').get('models', []):
            name = item.get('name', '')
            if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,150}', name):
                continue
            rows.append({'id': 'ollama/' + name, 'name': name + ' (локальный чат)',
                         'provider': 'ollama', 'available': True, 'availability': 'loaded',
                         'reason': 'Модель уже загружена. Локальный чат без выполнения команд.',
                         'capabilities': {'text': True, 'image': False, 'tools': False},
                         'limits': {}, 'metadata_source': 'ollama_api_ps'})
    except Exception:
        pass
    return rows


def discover():
    global _CACHE
    now = time.monotonic()
    if now - _CACHE[0] < 30:
        return json.loads(json.dumps(_CACHE[1]))
    try:
        exe, kw = _context()
        if not exe:
            raise OSError('missing')
        result = subprocess.run([exe, 'models', '--verbose'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=12, **kw)
        if result.returncode or len(result.stdout) > 8_000_000:
            raise OSError('catalog')
        rows = _parse_catalog(result.stdout.decode(errors='replace'))
        if not rows:
            raise OSError('empty')
    except Exception:
        rows = [{'id': 'opencode/unavailable', 'name': 'OpenCode / DeepSeek', 'provider': 'opencode',
                 'available': False, 'reason': 'Установленный CLI или каталог сейчас недоступен.',
                 'availability': 'unavailable', 'capabilities': {}, 'limits': {}}]
    # Local configured OpenCode Ollama names may load a different, heavy model.
    rows = [r for r in rows if r.get('provider') != 'ollama'] + _other_models()
    for row in rows:
        status = _status(row['id'])
        row['checked_at'] = time.time()
        if status.get('verified_at'):
            row['verified_at'] = status['verified_at']
        if status.get('reason') == 'quota_exhausted':
            row.update(available=False, availability='quota_exhausted', retry_after=status['until'],
                       reason='Провайдер сообщил об исчерпании бесплатного лимита. Повторная проверка через 5 минут.')
    _CACHE = (now, rows)
    return json.loads(json.dumps(rows))


def _native_model(model):
    if model.startswith('opencode/'):
        return model[len('opencode/'):]
    if model.startswith('ollama/'):
        return model
    raise RuntimeError('provider_unsupported')


def _usage(part):
    tokens = part.get('tokens', {})
    out = {}
    for key in ('input', 'output', 'reasoning'):
        value = tokens.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            out[key + '_tokens'] = value
    # Catalog context is model capacity; never present it as remaining quota.
    return out


def _stop(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3)
    except ProcessLookupError:
        pass


def run_provider(model: str, text: str, attachments: list, cwd: str,
                 native_id: str | None, emit, cancelled):
    if cancelled():
        raise InterruptedError('cancelled')
    desc = next((r for r in discover() if r['id'] == model and r['available']), None)
    if desc is None:
        raise RuntimeError('provider_unavailable')
    protocol = model.split('/', 1)[0]
    if native_id and not re.fullmatch(r'(?:ses_[A-Za-z0-9_-]{4,160}|ollama_[a-f0-9]{32}|[a-fA-F0-9-]{36})', native_id):
        raise RuntimeError('invalid_provider_session')
    exe, kw = _context()
    if not exe and protocol == 'opencode':
        raise RuntimeError('provider_unavailable')
    cmd = [exe, 'run', '--print-logs', '--log-level', 'ERROR', '--format', 'json', '--model', _native_model(model)] if protocol == 'opencode' else []
    if native_id and protocol == 'opencode':
        cmd += ['--session', native_id]
    prompt = text
    messages = []
    history_path = None
    if protocol == 'ollama':
        native_id = native_id or 'ollama_' + uuid.uuid4().hex
        if not native_id.startswith('ollama_'):
            raise RuntimeError('invalid_provider_session')
        history_path = _state_root() / (native_id + '.json')
        try:
            if history_path.is_symlink():
                raise RuntimeError('invalid_provider_session')
            messages = json.loads(history_path.read_text())
        except FileNotFoundError:
            messages = []
    for item in attachments:
        path = Path(item['path'])
        mime = item.get('mime', '')
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise RuntimeError('attachment_invalid')
        kind = mime.split('/', 1)[0]
        if kind in ('image', 'audio', 'video') or mime == 'application/pdf':
            cap = 'pdf' if mime == 'application/pdf' else kind
            if not desc.get('capabilities', {}).get(cap):
                raise RuntimeError('attachment_unsupported')
            cmd += ['--file', str(path)]
        elif protocol == 'ollama':
            if not (mime.startswith('text/') or mime in ('application/json', 'application/xml')):
                raise RuntimeError('attachment_unsupported')
            if path.stat().st_size > 1_000_000:
                raise RuntimeError('attachment_too_large')
            prompt += '\n' + json.dumps(item.get('name', path.name)) + ':\n' + path.read_text(errors='replace')
        else:
            # The native coding agent reads the same owner-scoped local files.
            prompt += '\nПрикреплённый файл пользователя: ' + json.dumps(str(path), ensure_ascii=False)
    # -- prevents messages beginning with a dash from becoming CLI options.
    payload = None
    if protocol == 'opencode':
        cmd += ['--', prompt]
    elif protocol == 'gateway':
        claude = shutil.which('claude', path=kw['env'].get('PATH'))
        if not claude:
            raise RuntimeError('provider_unavailable')
        cfg = Path(kw['env']['HOME']) / '.config/ai-gateway'
        master = next((line.split('=', 1)[1].strip() for line in (cfg / 'master.env').read_text().splitlines() if line.startswith('LITELLM_MASTER_KEY=')), '')
        if not master:
            raise RuntimeError('provider_credentials_missing')
        kw['env'].update(ANTHROPIC_BASE_URL='http://127.0.0.1:4000', ANTHROPIC_AUTH_TOKEN=master,
                         DISABLE_AUTOUPDATER='1', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
        cmd = [claude, '--print', '--verbose', '--output-format', 'stream-json',
               '--include-partial-messages', '--permission-mode', 'acceptEdits',
               '--permission-prompts', 'none', '--model', model.split('/', 1)[1]]
        if native_id:
            cmd += ['--resume', native_id]
        payload = prompt.encode()
    elif protocol == 'ollama':
        try:
            loaded = {x.get('name') for x in _get_local('http://127.0.0.1:11434/api/ps').get('models', [])}
        except Exception:
            raise RuntimeError('local_model_unavailable') from None
        if model.split('/', 1)[1] not in loaded:
            raise RuntimeError('local_model_not_loaded')
        curl = shutil.which('curl', path=kw['env'].get('PATH'))
        if not curl:
            raise RuntimeError('provider_unavailable')
        messages.append({'role': 'user', 'content': prompt})
        payload = json.dumps({'model': model.split('/', 1)[1], 'messages': messages,
                              'stream': True, 'think': False,
                              'options': {'num_predict': 2048}}).encode()
        cmd = [curl, '--no-buffer', '--fail', '--silent', '--connect-timeout', '5',
               '--header', 'Content-Type: application/json', '--data-binary', '@-',
               'http://127.0.0.1:11434/api/chat']
    else:
        raise RuntimeError('provider_unsupported')
    kw['cwd'] = cwd
    emit({'type': 'status', 'description': 'Запускает ' + desc['name']})
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, **kw)
        if payload is not None:
            proc.stdin.write(payload)
            proc.stdin.close()
    except OSError:
        raise RuntimeError('provider_start_failed') from None
    usage = {}
    saw_text = False
    finished = False
    failed = False
    buffer = b''
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    sel.register(proc.stderr, selectors.EVENT_READ)
    partial = False
    answer = ''
    stderr_tail = ''

    def consume(line):
        nonlocal native_id, saw_text, finished, failed, partial, answer
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            return
        if not isinstance(row, dict):
            return
        if protocol == 'ollama':
            chunk = row.get('message', {}).get('content', '')
            if chunk:
                emit({'type': 'text', 'text': chunk}); saw_text = True; answer += chunk
            if row.get('error'):
                failed = True
            if row.get('done') is True:
                finished = True
                for source, dest in [('prompt_eval_count', 'input_tokens'), ('eval_count', 'output_tokens')]:
                    if isinstance(row.get(source), int):
                        usage[dest] = row[source]
            return
        if protocol == 'gateway':
            if row.get('error') or row.get('isApiErrorMessage'):
                failed = True
                return
            session = row.get('session_id')
            if isinstance(session, str) and re.fullmatch(r'[a-fA-F0-9-]{36}', session):
                native_id = session
            if row.get('type') == 'stream_event':
                event = row.get('event', {}); delta = event.get('delta', {})
                if delta.get('type') == 'text_delta' and isinstance(delta.get('text'), str):
                    emit({'type': 'text', 'text': delta['text']}); saw_text = True; partial = True
                if event.get('type') == 'content_block_start' and event.get('content_block', {}).get('type') == 'tool_use':
                    emit({'type': 'status', 'description': 'Использует инструмент'})
            if row.get('type') == 'assistant' and not partial:
                for block in row.get('message', {}).get('content', []):
                    if block.get('type') == 'text' and block.get('text'):
                        emit({'type': 'text', 'text': block['text']}); saw_text = True
            if row.get('type') == 'result':
                failed = row.get('is_error', False); finished = not failed
                for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
                    value = row.get('usage', {}).get(key)
                    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                        usage[key] = value
            return
        session = row.get('sessionID')
        if isinstance(session, str) and re.fullmatch(r'ses_[A-Za-z0-9_-]{4,160}', session):
            native_id = session
        typ, part = row.get('type'), row.get('part', {})
        if not isinstance(part, dict):
            return
        if typ == 'text' and isinstance(part.get('text'), str) and part['text']:
            emit({'type': 'text', 'text': part['text']})
            saw_text = True
        elif typ == 'tool_use':
            # Tool arguments/output can contain credentials. Never emit either.
            tool = part.get('tool', '')
            safe = tool if tool in ('read', 'edit', 'write', 'bash', 'glob', 'grep', 'webfetch', 'websearch', 'task') else 'инструмент'
            emit({'type': 'status', 'description': 'Использует инструмент: ' + safe})
        elif typ == 'step_finish':
            for key, value in _usage(part).items():
                usage[key] = usage.get(key, 0) + value
            if part.get('reason') in ('stop', 'end_turn'):
                finished = True
        elif typ == 'error':
            failed = True

    try:
        while True:
            if cancelled():
                raise InterruptedError('cancelled')
            ready = sel.select(.2)
            if ready:
                for key, _ in ready:
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    if key.fileobj is proc.stderr:
                        stderr_tail = (stderr_tail + data.decode(errors='replace'))[-16384:]
                        low = stderr_tail.lower()
                        if 'free usage limit' in low and ('reached' in low or 'exceeded' in low):
                            _status(model, write=True)
                            global _CACHE
                            _CACHE = (0, [])
                            raise RuntimeError('provider_quota_exhausted')
                        continue
                    buffer += data
                if len(buffer) > MAX_RECORD:
                    raise RuntimeError('provider_record_limit')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    consume(line)
            if not sel.get_map():
                break
            if proc.poll() is not None and not ready:
                break
        if buffer:
            consume(buffer)
        try:
            code = proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise RuntimeError('provider_exit_timeout') from None
        if cancelled():
            raise InterruptedError('cancelled')
        if code != 0 or failed or not saw_text or not finished or not native_id:
            raise RuntimeError('provider_response_incomplete')
        if history_path is not None:
            messages.append({'role': 'assistant', 'content': answer})
            history_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temp = history_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(messages, f)
            temp.replace(history_path)
        _status(model, write='verified')
        _CACHE = (0, [])
        return {'native_id': native_id, 'usage': usage}
    finally:
        sel.close()
        _stop(proc)
        proc.stdout.close()
        proc.stderr.close()
