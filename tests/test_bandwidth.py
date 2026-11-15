import threading
import time
from types import SimpleNamespace

import pytest

from uav_downloader.core import config
from uav_downloader.sites import base
from uav_downloader.ui.bandwidth import parse_bandwidth, BandwidthControl


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', '-1', '', 'oops', '0.001', '10001'])
def test_invalid_input_rejected(value):
    with pytest.raises(ValueError):
        parse_bandwidth(value)


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
def test_corrupt_saved_nonfinite_limit_is_safe(value):
    assert config.normalize_speed_limit_mbps(value) == 0


def test_fractional_values_and_unlimited():
    assert parse_bandwidth(' 2.5 ') == 2.5
    assert parse_bandwidth('0') == 0
    assert parse_bandwidth('0.01') == 0.01


def test_unlimited_change_wakes_waiting_worker():
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.01)
    done = threading.Event()
    thread = threading.Thread(target=lambda: (limiter.acquire(1024 * 1024), done.set()), daemon=True)
    thread.start()
    assert not done.wait(0.08)
    limiter.set_limit(0)
    assert done.wait(0.5)
    thread.join()


def test_cancellation_wakes_limited_worker():
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.01)
    stop = threading.Event()
    result = []
    thread = threading.Thread(target=lambda: result.append(limiter.acquire(1024 * 1024, stop.is_set)))
    thread.start()
    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive() and result == [False]


def test_parallel_workers_share_budget_not_individual_caps():
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.2)
    started = time.monotonic()
    threads = [threading.Thread(target=lambda: limiter.acquire(32768)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=4)
    elapsed = time.monotonic() - started
    assert all(not thread.is_alive() for thread in threads)
    assert elapsed >= 0.55  # 128 KiB / 0.2 MiB/s = 0.625 s, shared across all workers


def test_reapplying_same_limit_does_not_refill_budget():
    limiter = base._SpeedLimiter()
    limiter.set_limit(2)
    limiter._tokens = 19
    before = limiter._last
    limiter.set_limit(2)
    assert limiter._tokens == 19 and limiter._last == before


def test_invalid_ui_edit_preserves_previous_cap():
    control = BandwidthControl.__new__(BandwidthControl)
    values, calls = [], []
    control._value = SimpleNamespace(get=lambda: 'nan', set=values.append)
    control._saved = 2.5
    control._entry = SimpleNamespace(configure=lambda **kw: None)
    control._command = calls.append
    assert control.commit() is False
    assert values == ['2.5'] and not calls


def test_stream_transport_without_cffi_uses_shared_synchronous_session(monkeypatch):
    calls = []
    monkeypatch.setattr(base, '_use_cffi', False)
    monkeypatch.setattr(base, '_get_session', lambda: SimpleNamespace(get=lambda *a, **k: calls.append((a, k))))
    monkeypatch.setattr(base, '_get_cffi_session', lambda: pytest.fail('background prefetch bypasses limit'))
    base._http_get('https://example.test/segment.ts', {'Referer': 'fixture'}, stream=True)
    assert calls[0][1]['stream'] is True
    assert calls[0][1]['headers'] == {'Referer': 'fixture'}


def test_limited_segment_reads_chunks_and_closes_response(monkeypatch, tmp_path):
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
    chunks = [(b'\x47' + b'\x11' * 187) * 2] * 2
    calls, closed = [], []
    class Response:
        status_code = 200
        @property
        def content(self):
            pytest.fail('must not eagerly load a limited segment')
        def iter_content(self, chunk_size):
            assert chunk_size == 65536
            return iter(chunks)
        def close(self):
            closed.append(True)
    def get(url, **kwargs):
        assert kwargs['stream'] is True
        return Response()
    crawler._media_get = get
    monkeypatch.setattr(base.speed_limiter, '_limit_bps', 100)
    monkeypatch.setattr(base.speed_limiter, 'acquire', lambda size, **kw: calls.append(size) or True)
    assert crawler._scrape((0, 'fixture')) is True
    assert calls == [len(c) for c in chunks] and closed == [True]


def test_edit_is_committed_before_ui_rebuild_or_close():
    from uav_downloader.ui.bandwidth import commit_bandwidth_controls
    calls = []
    commit_bandwidth_controls(SimpleNamespace(
        _bandwidth_control=SimpleNamespace(commit=lambda: calls.append('settings')),
        _toolbar_bandwidth=SimpleNamespace(commit=lambda: calls.append('toolbar'))))
    assert calls == ['settings', 'toolbar']
    commit_bandwidth_controls(SimpleNamespace())  # Partially initialized window.


def test_synced_control_does_not_emit_another_change():
    control = BandwidthControl.__new__(BandwidthControl)
    values, calls = [], []
    control._value = SimpleNamespace(get=lambda: 2.5, set=values.append)
    control._entry = SimpleNamespace(configure=lambda **kw: None)
    control._command = calls.append
    control.set_value(2.5)
    assert control.commit() is True
    assert control._saved == 2.5 and not calls


def test_settings_cards_reflow_only_at_breakpoint():
    from uav_downloader.apps.watch import SmallToolApp
    changes = []
    master = SimpleNamespace(grid_columnconfigure=lambda *a, **kw: changes.append(('columns', kw)))
    cards = tuple(SimpleNamespace(master=master,
        grid_configure=lambda **kw: changes.append(('card', kw))) for _ in range(3))
    app = SimpleNamespace(_settings_cards=cards, _settings_columns=None)
    SmallToolApp._reflow_settings(app, 1180)
    assert app._settings_columns == 2 and len(changes) == 4
    SmallToolApp._reflow_settings(app, 1179)
    assert len(changes) == 4
    SmallToolApp._reflow_settings(app, 920)
    assert app._settings_columns == 1 and len(changes) == 8
    assert changes[-2][1]['columnspan'] == 1


def test_refresh_labels_cover_every_supported_language():
    from uav_downloader.i18n.locales import STRINGS, _QUEUE_UI_KEYS
    for labels in STRINGS.values():
        assert all(labels.get(key) for key in _QUEUE_UI_KEYS)
        assert labels['bandwidth_title'] and labels['speed_limit_desc']


def test_real_http_segments_share_limit(monkeypatch, tmp_path):
    """Fallback HTTP integration: streamed media, concurrent workers, real timing."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import requests
    payload = (b'\x47' + b'\x11' * 187) * 1024
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    session = requests.Session()
    session.trust_env = False
    limiter = base._SpeedLimiter()
    limiter.set_limit(0.5)
    monkeypatch.setattr(base, 'speed_limiter', limiter)
    monkeypatch.setattr(base, '_use_cffi', False)
    monkeypatch.setattr(base, '_get_session', lambda: session)
    monkeypatch.setattr(config, 'proxy_request_kwargs', lambda: {})
    crawler = base.M3U8Crawler.__new__(base.M3U8Crawler)
    crawler._extra_headers = {}
    crawler._cancel_job = False
    crawler._storage_error = None
    crawler._temp_folder = str(tmp_path)
    crawler._key_content = None
    crawler._speed_lock = threading.Lock()
    urls = [(n, f'http://127.0.0.1:{server.server_port}/{n}.ts') for n in range(2)]
    crawler._pending_set = set(urls)
    crawler._bytes_downloaded = 0
    crawler._speed_start = time.time()
    crawler._job_total = 2
    crawler._progress_callback = None
    results = []
    workers = [threading.Thread(target=lambda pair=pair: results.append(crawler._scrape(pair))) for pair in urls]
    try:
        started = time.monotonic()
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        assert all(not worker.is_alive() for worker in workers)
        assert results == [True, True]
        assert time.monotonic() - started >= 0.65
        assert crawler._bytes_downloaded == 2 * len(payload)
    finally:
        limiter.set_limit(0)
        for worker in workers:
            worker.join(timeout=2)
        session.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
