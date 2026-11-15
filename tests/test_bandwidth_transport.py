"""Transport regressions use synthetic media and loopback HTTP only."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from uav_downloader.sites import base


PAYLOAD = (b'\x47' + b'\x11' * 187) * 2048


@pytest.fixture
def local_media(monkeypatch):
    pytest.importorskip('curl_cffi')
    monkeypatch.setattr(base, '_use_cffi', True)
    monkeypatch.setattr(base, '_cffi_tls', threading.local())
    monkeypatch.setattr(base.config, 'proxy_request_kwargs',
                        lambda: {'proxies': {'http': '', 'https': ''}})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(PAYLOAD)))
            self.end_headers()
            try:
                self.wfile.write(PAYLOAD)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/fixture.ts'
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    for name in ('session', 'limited_session'):
        session = getattr(base._cffi_tls, name, None)
        if session is not None:
            session.close()


def test_limited_cffi_options_and_cookies_are_isolated(monkeypatch):
    from curl_cffi import CurlOpt
    monkeypatch.setattr(base, '_cffi_tls', threading.local())
    ordinary = base._get_cffi_session()
    ordinary.cookies.set('fixture', 'present', domain='example.test')
    limited = base._get_cffi_limited_session(25)
    try:
        assert limited is not ordinary
        assert limited.impersonate == 'chrome'
        assert limited.cookies.jar is ordinary.cookies.jar
        assert limited.cookies.get('fixture') == 'present'
        assert limited.curl_options[CurlOpt.TIMEOUT_MS] == 0
        assert limited.curl_options[CurlOpt.CONNECTTIMEOUT_MS] == 25000
        assert limited.curl_options[CurlOpt.LOW_SPEED_LIMIT] == 1
        assert limited.curl_options[CurlOpt.LOW_SPEED_TIME] == 25
        assert not ordinary.curl_options
        assert base._get_cffi_limited_session(12) is limited
        assert limited.curl_options[CurlOpt.CONNECTTIMEOUT_MS] == 12000
    finally:
        limited.close()
        ordinary.close()


def test_limited_callback_preserves_headers_proxy_and_accounts_once(monkeypatch):
    calls, charged = [], []
    response = SimpleNamespace(content=b'', close=lambda: None)

    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert kwargs['content_callback'](b'first') == 5
        assert kwargs['content_callback'](b'second') == 6
        return response

    monkeypatch.setattr(base, '_use_cffi', True)
    monkeypatch.setattr(base, '_get_cffi_limited_session', lambda timeout: SimpleNamespace(get=get))
    monkeypatch.setattr(base.speed_limiter, 'acquire', lambda size, **kw: charged.append(size) or True)
    monkeypatch.setattr(base.config, 'proxy_request_kwargs', lambda: {'proxies': {'https': 'fixture'}})
    result = base._http_get('https://example.test/a.ts',
                            {'User-Agent': 'old', 'Referer': 'fixture'}, stream=True)
    assert result.content == b'firstsecond'
    assert result._uav_bandwidth_accounted is True
    assert charged == [5, 6]
    assert calls[0][1]['headers'] == {'Referer': 'fixture'}
    assert calls[0][1]['proxies'] == {'https': 'fixture'}
    assert 'stream' not in calls[0][1]


def test_partial_cffi_failure_does_not_mix_fallback_bytes(monkeypatch):
    def fail(url, **kwargs):
        kwargs['content_callback'](b'partial-old-body')
        raise RuntimeError('connection lost')

    fallback = SimpleNamespace(content=b'new-body')
    fallback_calls = []
    monkeypatch.setattr(base, '_use_cffi', True)
    monkeypatch.setattr(base, '_get_cffi_limited_session', lambda timeout: SimpleNamespace(get=fail))
    monkeypatch.setattr(base.speed_limiter, 'acquire', lambda *a, **kw: True)
    monkeypatch.setattr(base, '_get_session', lambda: SimpleNamespace(
        get=lambda *a, **kw: fallback_calls.append(kw) or fallback))
    monkeypatch.setattr(base.config, 'proxy_request_kwargs', lambda: {})
    result = base._http_get('https://example.test/a.ts', {'User-Agent': 'old'}, stream=True)
    assert result is fallback and result.content == b'new-body'
    assert not getattr(result, '_uav_bandwidth_accounted', False)
    assert fallback_calls == [{'headers': {'User-Agent': 'old'}, 'timeout': 20, 'stream': True}]


@pytest.mark.parametrize('callback_failure', [False, True])
def test_callback_abort_uses_sentinel_and_never_falls_back(monkeypatch, callback_failure):
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR

    def acquire(*args, **kwargs):
        if callback_failure:
            raise ValueError('callback failed')
        return False

    def get(url, **kwargs):
        assert kwargs['content_callback'](b'chunk') == CURL_WRITEFUNC_ERROR
        raise RuntimeError('curl write aborted')

    monkeypatch.setattr(base, '_use_cffi', True)
    monkeypatch.setattr(base.speed_limiter, 'acquire', acquire)
    monkeypatch.setattr(base, '_get_cffi_limited_session', lambda timeout: SimpleNamespace(get=get))
    monkeypatch.setattr(base, '_get_session', lambda: pytest.fail('abort must not restart with requests'))
    expected = ValueError if callback_failure else base._MediaDownloadCancelled
    with pytest.raises(expected):
        base._http_get('https://example.test/a.ts', stream=True)


def test_real_cffi_workers_share_callback_budget(local_media, monkeypatch):
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.5)
    monkeypatch.setattr(base, 'speed_limiter', limiter)
    monkeypatch.setattr(base, '_get_session', lambda: pytest.fail('must preserve curl transport'))
    results, errors = [], []

    def download():
        try:
            response = base._http_get(local_media, stream=True)
            results.append(response.content)
            response.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            for name in ('session', 'limited_session'):
                session = getattr(base._cffi_tls, name, None)
                if session is not None:
                    session.close()

    workers = [threading.Thread(target=download) for _ in range(2)]
    start = time.monotonic()
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=6)
        assert all(not worker.is_alive() for worker in workers)
        assert not errors and results == [PAYLOAD, PAYLOAD]
        assert time.monotonic() - start >= 1.25
    finally:
        limiter.set_limit(0)
        for worker in workers:
            worker.join(timeout=3)


@pytest.mark.parametrize('cancel', [False, True])
def test_real_cffi_wait_wakes_for_cancel_or_unlimited(local_media, monkeypatch, cancel):
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.01)
    stop, entered = threading.Event(), threading.Event()
    original_acquire = limiter.acquire

    def acquire(*args, **kwargs):
        entered.set()
        return original_acquire(*args, **kwargs)

    monkeypatch.setattr(limiter, 'acquire', acquire)
    monkeypatch.setattr(base, 'speed_limiter', limiter)
    monkeypatch.setattr(base, '_get_session', lambda: pytest.fail('must not fall back'))
    results = []

    def download():
        try:
            response = base._http_get(local_media, stream=True, cancel_check=stop.is_set)
            results.append(response.content)
            response.close()
        except base._MediaDownloadCancelled:
            results.append('cancelled')
        finally:
            for name in ('session', 'limited_session'):
                session = getattr(base._cffi_tls, name, None)
                if session is not None:
                    session.close()

    worker = threading.Thread(target=download)
    worker.start()
    try:
        assert entered.wait(2)
        assert worker.is_alive()
        if cancel:
            stop.set()
        else:
            limiter.set_limit(0)
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert results == (['cancelled'] if cancel else [PAYLOAD])
    finally:
        stop.set()
        limiter.set_limit(0)
        worker.join(timeout=3)


def test_real_cffi_throttle_can_outlast_original_total_timeout(local_media, monkeypatch):
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.5)
    monkeypatch.setattr(base, 'speed_limiter', limiter)
    monkeypatch.setattr(base, '_get_session', lambda: pytest.fail('curl timeout must not cause fallback'))
    start = time.monotonic()
    response = base._http_get(local_media, timeout=0.1, stream=True)
    try:
        assert response.content == PAYLOAD
        assert time.monotonic() - start >= 0.6
    finally:
        response.close()


def test_scrape_does_not_charge_callback_payload_twice(monkeypatch, tmp_path):
    crawler = base.M3U8Crawler.__new__(base.M3U8Crawler)
    crawler._cancel_job = False
    crawler._storage_error = None
    crawler._temp_folder = str(tmp_path)
    crawler._key_content = None
    crawler._speed_lock = threading.Lock()
    crawler._pending_set = {(0, 'fixture')}
    crawler._bytes_downloaded = 0
    crawler._speed_start = time.time()
    crawler._job_total = 1
    crawler._progress_callback = None
    calls = []

    def media_get(url, timeout, stream):
        calls.append((url, stream))
        return SimpleNamespace(status_code=200, content=PAYLOAD,
                               _uav_bandwidth_accounted=True, close=lambda: None)

    # Keep the per-site media hook in control; _scrape must never bypass it.
    crawler._media_get = media_get
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: pytest.fail('bypassed media hook'))
    monkeypatch.setattr(base.speed_limiter, '_limit_bps', 1024)
    monkeypatch.setattr(base.speed_limiter, 'acquire', lambda *a, **kw: pytest.fail('double charge'))
    assert crawler._scrape((0, 'fixture')) is True
    assert calls == [('fixture', True)]
    assert crawler._bytes_downloaded == len(PAYLOAD)
