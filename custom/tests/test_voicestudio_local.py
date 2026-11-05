import importlib.util
from pathlib import Path
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

spec=importlib.util.spec_from_file_location('voice_local',Path(__file__).resolve().parents[1]/'deploy/voicestudio/local_entry.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.mark.parametrize('path,method,blocked',[
 ('/models/install','POST',True),('/setup/warmup','POST',True),
 ('/engines/omnivoice/selftest','POST',True),('/engines/sherpa-onnx/selftest','POST',False),
 ('/engines','GET',False),('/mcp','POST',True),('/profiles','POST',False),('/dub/start','POST',True),
])
def test_bounded_profile(path,method,blocked):
 assert module.blocked_path(path,method) is blocked


def test_speech_model_guard_replays_body():
 async def handler(request):return JSONResponse(await request.json())
 app=module.LocalCPUProfile(Starlette(routes=[Route('/v1/audio/speech',handler,methods=['POST'])]))
 with TestClient(app) as client:
  assert client.post('/v1/audio/speech',json={'model':'omnivoice','input':'x'}).status_code==409
  assert client.post('/v1/audio/speech',json={'input':'x'}).status_code==409
  result=client.post('/v1/audio/speech',json={'model':'sherpa-onnx','input':'test'})
  assert result.status_code==200 and result.json()['input']=='test'


def test_inventory_marks_heavy_engines_unconfigured():
 async def handler(request):return JSONResponse({'active':'sherpa-onnx','backends':[{'id':'sherpa-onnx','available':True},{'id':'omnivoice','available':True,'one_click_install':True}]})
 app=module.LocalCPUProfile(Starlette(routes=[Route('/engines/tts',handler)]))
 with TestClient(app) as client:
  result=client.get('/engines/tts')
  assert result.status_code==200
  rows=result.json()['backends']
  assert rows[0]['available'] is True
  assert rows[1]['available'] is False and rows[1]['local_profile_disabled'] is True
  assert rows[1]['one_click_install'] is False
  assert int(result.headers['content-length'])==len(result.content)
