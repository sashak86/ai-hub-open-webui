"""Native host readiness must not masquerade as a connected browser."""
import importlib.util
import json
from pathlib import Path

from hub_console.chat_chrome import status,EXTENSION
from .test_standalone import client


def test_register_native_host_without_extension_or_live_connection(tmp_path):
    spec=importlib.util.spec_from_file_location('chrome_installer',Path(__file__).resolve().parents[1]/'install-claude-chrome-host.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    cli=tmp_path/'.local/bin/claude';cli.parent.mkdir(parents=True);cli.write_text('#!/bin/sh\nexit 0\n');cli.chmod(0o700)
    result=installer.install(tmp_path,cli)
    assert result['native_host_registered'] and not result['extension_installed']
    readiness=status(tmp_path)
    assert readiness['cli_installed'] and readiness['native_host_registered']
    assert readiness['connected'] is None and not readiness['extension_files_detected']
    assert '--chrome-native-host' in (tmp_path/'.local/share/claude-chrome/native-host').read_text()
    assert not (tmp_path/'.config/google-chrome/Default').exists()


def test_extension_files_are_not_claimed_as_live_connection(tmp_path):
    manifest=tmp_path/'.config/google-chrome/Default/Extensions'/EXTENSION/'1.0/manifest.json'
    manifest.parent.mkdir(parents=True);manifest.write_text('{}')
    result=status(tmp_path)
    assert result['extension_files_detected']
    assert result['connection_status']=='not_verified' and result['connected'] is None
    assert 'token' not in json.dumps(result)


def test_readiness_requires_owner_auth_and_has_no_mutating_route(client):
    r=client.get('/v1/harness/chrome');assert r.status_code==200
    assert r.json()['connection_status']=='not_verified'
    assert client.post('/v1/harness/chrome',json={'enabled':True}).status_code in {404,405}
    client.headers.pop('X-WebUI-User')
    assert client.get('/v1/harness/chrome').status_code==400
    client.headers.pop('Authorization')
    assert client.get('/v1/harness/chrome').status_code==401


def test_installer_preserves_existing_different_launcher(tmp_path):
    import pytest
    spec=importlib.util.spec_from_file_location('chrome_installer_guard',Path(__file__).resolve().parents[1]/'install-claude-chrome-host.py')
    installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    cli=tmp_path/'claude';cli.write_text('official cli')
    launcher=tmp_path/'.local/bin/claude-chrome';launcher.parent.mkdir(parents=True);launcher.write_text('existing unrelated launcher')
    with pytest.raises(SystemExit):installer.install(tmp_path,cli)
    assert launcher.read_text()=='existing unrelated launcher'
    assert not (tmp_path/'.config/google-chrome/NativeMessagingHosts').exists()
