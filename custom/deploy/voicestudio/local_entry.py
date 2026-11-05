"""Run unmodified VoiceStudio upstream inside a bounded local CPU profile."""
import json
import os
from pathlib import Path

PROFILE_MESSAGE = ('На этом сервере включён лёгкий профиль Sherpa-ONNX / Piper CPU. '
                   'Тяжёлые модели, клонирование и установка других движков отключены; '
                   'для них требуется отдельная проверка памяти.')
ALLOWED_ENGINES = frozenset(os.environ.get('AI_HUB_VOICE_ALLOWED_ENGINES', 'sherpa-onnx').split(','))
ALLOW_MODEL_INSTALLS = os.environ.get('AI_HUB_VOICE_ALLOW_MODEL_INSTALLS') == '1'
SAFE_MODELS = ALLOWED_ENGINES | {'tts-1', 'tts-1-hd', 'gpt-4o-mini-tts'}


def blocked_path(path, method):
    if method in {'GET', 'HEAD', 'OPTIONS'}:
        return False
    if path in {'/setup/warmup', '/models/install', '/engine/install', '/mcp', '/mcp/'}:
        return not ALLOW_MODEL_INSTALLS
    if path.startswith('/engines/'):
        return not any(path.startswith('/engines/' + engine + '/') for engine in ALLOWED_ENGINES)
    if path.startswith(('/capture/', '/dictation/', '/dub/', '/dubs/', '/voice-design/', '/transcribe', '/v1/audio/transcriptions', '/v1/audio/translations')):
        return True
    return False


class LocalCPUProfile:
    def __init__(self, app):
        self.app = app

    async def reject(self, scope, receive, send):
        from starlette.responses import JSONResponse
        await JSONResponse({'detail': PROFILE_MESSAGE}, status_code=409)(scope, receive, send)

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] == 'websocket' and path in {'/ws/transcribe', '/ws/tts'}:
            await receive()
            await send({'type': 'websocket.close', 'code': 1008, 'reason': 'Local CPU profile: use REST speech'})
            return
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        method = scope.get('method', 'GET')
        if blocked_path(path, method):
            return await self.reject(scope, receive, send)
        if path == '/v1/audio/speech' and method == 'POST':
            chunks = []
            size = 0
            while True:
                part = await receive()
                if part['type'] != 'http.request':
                    return
                chunks.append(part)
                size += len(part.get('body', b''))
                if size > 32768:
                    return await self.reject(scope, receive, send)
                if not part.get('more_body'):
                    break
            try:
                body = json.loads(b''.join(x.get('body', b'') for x in chunks))
                model = body.get('model')
            except (ValueError, AttributeError):
                model = None
            if model not in SAFE_MODELS:
                return await self.reject(scope, receive, send)
            async def replay():
                return chunks.pop(0) if chunks else await receive()
            return await self.app(scope, replay, send)
        if path in {'/engines', '/engines/tts', '/engines/asr'} and method == 'GET':
            messages = []
            async def collect(message):
                messages.append(message)
            await self.app(scope, receive, collect)
            start = next((x for x in messages if x['type'] == 'http.response.start'), None)
            raw = b''.join(x.get('body', b'') for x in messages if x['type'] == 'http.response.body')
            if start and start['status'] == 200:
                try:
                    data = json.loads(raw)
                    groups = [data] if 'backends' in data else [data.get(k, {}) for k in ('tts', 'asr')]
                    for group in groups:
                        for row in group.get('backends', []):
                            row['one_click_install'] = False
                            if row.get('id') not in ALLOWED_ENGINES:
                                row.update(available=False, ready=False, last_error=PROFILE_MESSAGE,
                                           unavailable_reason=PROFILE_MESSAGE, local_profile_disabled=True)
                    data['local_profile'] = {'allowed_engines': sorted(ALLOWED_ENGINES),
                                             'model_installs_enabled': ALLOW_MODEL_INSTALLS}
                    raw = json.dumps(data, ensure_ascii=False).encode()
                    start['headers'] = [(k, v) for k, v in start['headers'] if k.lower() != b'content-length']
                    start['headers'].append((b'content-length', str(len(raw)).encode()))
                    await send(start)
                    await send({'type': 'http.response.body', 'body': raw})
                    return
                except (ValueError, TypeError, AttributeError):
                    pass
            for message in messages:
                await send(message)
            return
        return await self.app(scope, receive, send)


def create_app():
    from backend.main import app
    from services import tts_backend
    # This profile installs one known Piper speaker; advertise the genuine
    # preset capability so upstream renders plain TTS rather than a clone.
    tts_backend.SherpaOnnxBackend.PRESET_VOICES = ['irina']
    tts_backend.SherpaOnnxBackend.DEFAULT_VOICE = 'irina'
    tts_backend.SherpaOnnxBackend.display_name = 'Piper · Ирина · русский · CPU'
    tts_backend.SherpaOnnxBackend.gpu_compat = ('cpu',)
    tts_backend.SherpaOnnxBackend.supported_languages = property(lambda self: ['ru'])
    original = tts_backend.get_backend_class
    def allowed_backend(engine_id):
        if engine_id not in ALLOWED_ENGINES:
            raise ValueError(PROFILE_MESSAGE)
        return original(engine_id)
    tts_backend.get_backend_class = allowed_backend
    return LocalCPUProfile(app)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8771, proxy_headers=False)
