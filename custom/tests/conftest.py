"""Run offline coroutine tests even without optional pytest-asyncio."""
import asyncio
import inspect
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'asyncio: coroutine test with asyncio.run fallback')


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if pyfuncitem.config.pluginmanager.hasplugin('asyncio') or not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    args={name:pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(pyfuncitem.obj(**args))
    return True


@pytest.fixture(autouse=True)
def private_recovery_is_not_read_by_offline_tests(tmp_path,monkeypatch):
    from hub_console import webui_password_reset
    monkeypatch.setattr(webui_password_reset,'CONFIG_PATH',tmp_path/'unconfigured-recovery.json')
