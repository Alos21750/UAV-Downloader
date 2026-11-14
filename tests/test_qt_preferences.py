from types import SimpleNamespace

import pytest

from uav_downloader.core import config, preferences
from uav_downloader.core.task_service import TaskService
from uav_downloader.sites import base


@pytest.fixture
def profile(monkeypatch, tmp_path):
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(tmp_path / 'preferences.json'))
    monkeypatch.setattr(config, '_proxy_mode_cache', config._PROXY_UNSET)
    monkeypatch.setattr(config, '_proxy_url_cache', config._PROXY_UNSET)
    monkeypatch.setattr(config, '_system_proxy_cache', config._PROXY_UNSET)
    monkeypatch.setattr(base, '_resolution_pref', base.get_resolution_pref())
    return tmp_path


def test_preferences_validate_before_write_and_keep_other_fields(profile):
    config._save_prefs(dict(lang='ja', filename_mode='full-title'))
    manager = SimpleNamespace(max_concurrent=2)
    with pytest.raises(ValueError):
        preferences.save_preferences(dict(download_concurrency=3, proxy_mode='manual', proxy_url=''), manager)
    assert manager.max_concurrent == 2 and config._load_prefs() == dict(lang='ja', filename_mode='full-title')
    result = preferences.save_preferences(dict(download_concurrency=3, resolution='720',
        proxy_mode='manual', proxy_url='127.0.0.1:7890'), manager)
    assert manager.max_concurrent == 3 and base.get_resolution_pref() == '720'
    assert config._load_prefs()['lang'] == 'ja'
    assert result['proxy_url'] == 'http://127.0.0.1:7890'
    assert config.proxy_request_kwargs()['proxies']['https'] == 'http://127.0.0.1:7890'


def test_failed_preference_write_keeps_runtime_and_disk_state(profile, monkeypatch):
    config._save_prefs(dict(resolution='1080', proxy_mode='direct'))
    manager = SimpleNamespace(max_concurrent=2)
    original_resolution = base.get_resolution_pref()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(config, '_save_prefs', fail)
    with pytest.raises(OSError, match='disk full'):
        preferences.save_preferences(dict(download_concurrency=4, resolution='360',
                                         proxy_mode='manual', proxy_url='localhost:8080'), manager)
    assert manager.max_concurrent == 2 and base.get_resolution_pref() == original_resolution
    assert config.get_proxy_mode() == 'direct' and config.get_resolution_pref() == '1080'


def test_translation_projection_never_returns_api_key(monkeypatch):
    monkeypatch.setattr(preferences.translation, 'get_translation_settings', lambda *args:
        SimpleNamespace(provider='openai', base_url='https://example.test', model='test',
                        api_key='secret-test-value', api_key_source='environment'))
    projection = preferences.read_translation()
    assert 'secret-test-value' not in repr(projection)
    assert projection['key_source'] == 'environment' and 'api_key' not in projection


def test_settings_write_is_ordered_before_close(profile):
    service = TaskService(str(profile / 'queue.csv'))
    try:
        service.ready.result(3)
        saved = service.submit('set_preferences', dict(download_concurrency=5, resolution='480'))
        closed = service.close()
        assert closed.result(3) and saved.result(0)['resolution'] == '480'
        assert config.get_download_concurrency() == 5
    finally:
        service.abandon()


def test_cloudflare_write_preserves_cookie_without_exposing_it_and_is_domain_scoped(profile, monkeypatch):
    monkeypatch.setattr(config, '_cf_store_path', lambda: str(profile / 'cf.json'))
    monkeypatch.setattr(config, 'CF_OVERRIDES', {})
    projected = preferences.save_cloudflare(dict(host='JABLE.TV', cookie='cf_clearance=secret-cookie', ua='Browser UA'))
    assert projected['host'] == 'jable.tv' and projected['has_cookie']
    assert 'secret-cookie' not in repr(projected)
    preferences.save_cloudflare(dict(host='missav.ai', cookie='another-cookie', ua='Other UA'))
    preferences.save_cloudflare(dict(host='jable.tv', cookie=None, ua='New UA'))
    assert config.get_cf_override('jable.tv') == dict(cookie='secret-cookie', ua='New UA')
    preferences.save_cloudflare(dict(host='jable.tv', clear=True))
    assert config.get_cf_override('jable.tv') is None
    assert config.get_cf_override('missav.ai')['cookie'] == 'another-cookie'
    config.load_cf_overrides()
    assert config.cf_override_hosts() == ['missav.ai']


def test_cloudflare_failed_write_cannot_change_live_credentials(profile, monkeypatch):
    monkeypatch.setattr(config, '_cf_store_path', lambda: str(profile / 'cf.json'))
    monkeypatch.setattr(config, 'CF_OVERRIDES', {})
    preferences.save_cloudflare(dict(host='jable.tv', cookie='original-cookie', ua='Original UA'))
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(config, '_write_cf_snapshot', fail)
    with pytest.raises(OSError, match='disk full'):
        preferences.save_cloudflare(dict(host='jable.tv', cookie='replacement-cookie', ua='New UA'))
    assert config.get_cf_override('jable.tv') == dict(cookie='original-cookie', ua='Original UA')
    for host in ['https://jable.tv/path', 'jable.tv:443', 'jable.tv\nHost:evil.test']:
        with pytest.raises(ValueError, match='domain'):
            preferences.save_cloudflare(dict(host=host, clear=True))
    config.load_cf_overrides()
    assert config.get_cf_override('jable.tv')['cookie'] == 'original-cookie'


def test_close_retry_really_saves_failed_preferences_before_exiting(profile, monkeypatch):
    original = config._save_prefs
    attempts = []
    def fail_once(prefs):
        attempts.append(dict(prefs))
        if len(attempts) == 1:
            raise OSError('disk temporarily unavailable')
        return original(prefs)
    monkeypatch.setattr(config, '_save_prefs', fail_once)
    service = TaskService(str(profile / 'queue.csv'))
    try:
        service.ready.result(3)
        saving = service.submit('set_preferences', dict(resolution='720'))
        with pytest.raises(OSError):
            saving.result(3)
        with pytest.raises(OSError):
            service.close().result(3)
        assert not (profile / 'preferences.json').exists()
        assert service.close().result(3)
        assert config.get_resolution_pref() == '720' and len(attempts) == 2
    finally:
        service.abandon()
