"""#59: proxy/HTTP failures are not evidence of a Cloudflare challenge."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest

from uav_downloader.core import config
from uav_downloader.sites import base, supjav


@pytest.fixture(autouse=True)
def isolated_routes(monkeypatch):
    monkeypatch.setattr(config, 'MIRRORS', {'supjav': ['supjav.com']})
    monkeypatch.setattr(config, 'RATE_LIMIT_WAITS', {})
    monkeypatch.setattr(config, 'get_cf_override', lambda host: None)
    monkeypatch.setattr(config, '_proxy_mode_cache', 'direct')
    monkeypatch.setattr(base, '_active_host', {})


def response(status=200, content=b'<html>ok</html>', **headers):
    return SimpleNamespace(status_code=status, content=content, headers=headers,
                           url='https://supjav.com/')


def fetch(resp, validate=lambda r: True):
    scraper = SimpleNamespace(get=lambda *a, **kw: resp)
    return base.fetch_with_mirrors(scraper, 'https://supjav.com/', 'supjav', validate)


@pytest.mark.parametrize('status', [401, 403, 404, 407, 429, 500, 502, 503])
def test_plain_http_failure_is_not_a_challenge_or_valid_page(status):
    with pytest.raises(RuntimeError, match=f'HTTP {status}'):
        fetch(response(status, server='cloudflare'))


@pytest.mark.parametrize('status', [200, 403, 503])
def test_actual_challenge_still_has_blocked_result(status):
    assert fetch(response(status, b'<title>Just a moment...</title>'))[2] == 'blocked'


def test_cf_mitigated_challenge_header_is_evidence():
    assert fetch(response(403, **{'cf-mitigated': 'challenge'}))[2] == 'blocked'


def test_transport_failure_hides_sensitive_exception_details():
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise ConnectionError('http://fixture-user:fixture-secret@proxy.invalid:8888/private')

    with pytest.raises(RuntimeError, match='Network/proxy') as caught:
        base.fetch_with_mirrors(SimpleNamespace(get=fail), 'https://supjav.com/',
                                'supjav', lambda r: True)
    assert len(calls) == 2
    assert 'fixture-secret' not in str(caught.value)
    assert 'proxy.invalid' not in str(caught.value)
    assert caught.value.__context__ is None


def test_off_allowlist_redirect_is_not_reported_as_cf_block():
    resp = response()
    resp.url = 'https://untrusted.invalid/private?fixture-secret'
    with pytest.raises(RuntimeError, match='redirect') as caught:
        fetch(resp)
    assert 'fixture-secret' not in str(caught.value)


def test_failure_on_first_mirror_does_not_prevent_success(monkeypatch):
    monkeypatch.setattr(config, 'MIRRORS', {'supjav': ['supjav.com', 'mirror.test']})

    def get(url, **kwargs):
        resp = response(503 if 'supjav.com' in url else 200)
        resp.url = url
        return resp

    assert base.fetch_with_mirrors(SimpleNamespace(get=get), 'https://supjav.com/',
                                   'supjav', lambda r: True)[2] == 'ok'


def test_recovered_transport_retry_does_not_mask_actual_challenge():
    responses = iter([None, response(403, b'<title>Just a moment...</title>')])

    def get(*args, **kwargs):
        result = next(responses)
        if result is None:
            raise ConnectionError('fixture reset')
        return result

    assert base.fetch_with_mirrors(SimpleNamespace(get=get), 'https://supjav.com/',
                                   'supjav', lambda r: True)[2] == 'blocked'


def test_mixed_challenge_and_transport_failure_is_not_all_mirrors_blocked(monkeypatch):
    monkeypatch.setattr(config, 'MIRRORS', {'supjav': ['supjav.com', 'mirror.test']})

    def get(url, **kwargs):
        if 'supjav.com' in url:
            return response(403, b'<title>Just a moment...</title>')
        raise ConnectionError('fixture reset')

    with pytest.raises(RuntimeError, match='Network/proxy.*site challenge'):
        base.fetch_with_mirrors(SimpleNamespace(get=get), 'https://supjav.com/',
                                'supjav', lambda r: True)


@pytest.mark.parametrize('mode', ['manual', 'system'])
def test_supjav_really_routes_https_through_selected_proxy(monkeypatch, mode):
    # A loopback proxy rejects CONNECT without contacting the real website.
    # This verifies actual curl_cffi routing, not merely mocked kwargs.
    connects = []

    class RejectingProxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            connects.append(self.path)
            self.send_error(502, 'Fixture proxy refuses tunnel')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), RejectingProxy)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    proxy = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(config, '_proxy_mode_cache', mode)
    monkeypatch.setattr(config, '_proxy_url_cache', proxy)
    monkeypatch.setattr(config, '_system_proxy_cache', (
        {'http': proxy, 'https': proxy}, 'detected'))
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    from curl_cffi import requests
    with requests.Session(impersonate='chrome') as session:
        monkeypatch.setattr(supjav.SupJavBrowser, '_get_scraper', lambda: session)
        try:
            with pytest.raises(RuntimeError, match='Network/proxy'):
                supjav.SupJavBrowser.fetch_page('https://supjav.com/')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
    assert connects == ['supjav.com:443', 'supjav.com:443']


def test_browser_error_replaces_previous_cf_placeholder():
    from uav_downloader.apps.browse import ModernApp
    refreshes = []
    app = SimpleNamespace(
        _is_closing=False, _page_req=2, _build_gen=1,
        _page=1, _last_loaded_page=1, _has_next=False, _videos=[],
        _browse_blocked=True, _browse_empty_message='',
        _page_lbl=SimpleNamespace(configure=lambda **kw: None),
        _status_lbl=SimpleNamespace(configure=lambda **kw: None),
    )
    app._refresh_grid = lambda: refreshes.append((app._browse_blocked, app._browse_empty_message))
    ModernApp._apply_page(app, 2, [], 1, error='Network/proxy failed')
    assert app._browse_blocked is False
    assert len(refreshes) == 1
    assert 'Network/proxy failed' in refreshes[0][1]


def test_browser_navigation_error_preserves_loaded_results():
    from uav_downloader.apps.browse import ModernApp
    videos = [{'url': 'https://supjav.com/1.html'}]
    app = SimpleNamespace(
        _is_closing=False, _page_req=2, _build_gen=1,
        _page=2, _last_loaded_page=1, _has_next=True, _videos=videos,
        _browse_blocked=False, _browse_empty_message='',
        _page_lbl=SimpleNamespace(configure=lambda **kw: None),
        _status_lbl=SimpleNamespace(configure=lambda **kw: None),
        _refresh_grid=lambda: pytest.fail('Do not rebuild successful cards on navigation failure'),
    )
    ModernApp._apply_page(app, 2, [], 2, error='Network/proxy failed')
    assert app._videos is videos
    assert app._page == 1
    assert app._has_next is False
