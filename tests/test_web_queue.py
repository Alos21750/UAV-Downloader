"""Offline contract/security tests for the opt-in NAS queue (issue #47)."""
import json
from http.client import HTTPConnection
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from uav_downloader.web.queue import QueueManager, run_download, validate_job
from uav_downloader.web.server import make_server, validate_token


TOKEN = 'test-only-Z9k2W5m8P3r6D1v4J7x0N2c5'
URL = 'https://jable.tv/videos/test-123/'


def wait_for(predicate):
    until = time.monotonic() + 4
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.mark.parametrize('url', [
    'http://jable.tv/videos/test-123/', 'https://localhost/videos/test-123/',
    'https://jable.tv.evil.invalid/videos/test-123/',
    'https://user:pass@jable.tv/videos/test-123/',
    'https://jable.tv:8443/videos/test-123/', 'file:///etc/passwd',
    'https://127.0.0.1/', 'https://jable.tv/videos/test-123/#secret',
    'https://jable.tv/videos/../../escape/',
    'https://jable.tv/videos/%2e%2e/escape/',
    'https://jable.tv/videos/a%2fb/',
    'https://jable.tv/videos/a%252fb/',
    'https://supjav.com/a/../462685.html',
    'https://missav.ai/ja/../test-123',
    'https://hanime1.me/a/../watch?v=12345',
    'https://jable.tv/videos/test-123/?token=private',
    'https://hanime1.me/watch?v=12345&token=private',
])
def test_reject_unsafe_queue_urls(url):
    with pytest.raises(ValueError):
        validate_job({'url': url})


@pytest.mark.parametrize('value', [None, '', 'short', 'x' * 50, 'a b' * 20])
def test_token_required_and_high_entropy(value):
    with pytest.raises(ValueError):
        validate_token(value)


@pytest.mark.parametrize('extra', [
    {'destination': '../../escape'}, {'max_workers': 17},
    {'resolution': '4k'}, {'speed_limit_mib': -1},
    {'speed_limit_mib': float('nan')}, {'max_workers': True},
    {'speed_limit_mib': 10**500},
])
def test_settings_bounded(extra):
    with pytest.raises(ValueError):
        validate_job({'url': URL, **extra})


def test_queue_persistence_and_recovery(tmp_path):
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    job = q.add([{'url': URL}])[0]
    stored = json.loads((tmp_path / 'state' / 'queue.json').read_text())
    stored['jobs'][0]['status'] = 'running'
    (tmp_path / 'state' / 'queue.json').write_text(json.dumps(stored))
    recovered = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    assert recovered.snapshot()['jobs'][0]['status'] == 'interrupted'
    recovered.retry(job['id'])
    assert recovered.snapshot()['jobs'][0]['status'] == 'pending'


def test_bad_batch_is_atomic_and_unknown_ids_rejected(tmp_path):
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    with pytest.raises(ValueError):
        q.add([{'url': URL}, {'url': 'https://localhost/'}])
    assert q.snapshot()['jobs'] == []
    with pytest.raises(KeyError):
        q.cancel('missing')


def test_worker_progress_cancel_retry_and_only_one_active(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    def runner(job, root, cancel, progress):
        calls.append(job['id'])
        started.set()
        progress(2, 4, 128, 'segments')
        while not release.wait(.01):
            if cancel.is_set():
                return False
        return True

    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', runner=runner)
    try:
        a, b = q.add([{'url': URL}, {'url': URL.replace('123', '456')}])
        assert started.wait(2)
        wait_for(lambda: q.snapshot()['jobs'][0]['done'] == 2)
        assert len(calls) == 1
        q.cancel(a['id'])
        wait_for(lambda: q.snapshot()['jobs'][0]['status'] == 'cancelled')
        release.set()
        wait_for(lambda: q.snapshot()['jobs'][1]['status'] == 'completed')
        q.retry(a['id'])
        wait_for(lambda: q.snapshot()['jobs'][0]['status'] == 'completed')
        assert len(calls) == 3
    finally:
        release.set()
        q.close()


def test_storage_failure_halts_pending_and_requires_explicit_resume(tmp_path):
    from uav_downloader.core.storage import StorageWriteError
    calls = []

    def runner(job, *args):
        calls.append(job['id'])
        raise StorageWriteError(tmp_path, OSError('disk failed'))

    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', runner=runner)
    try:
        q.add([{'url': URL}, {'url': URL.replace('123', '456')}])
        wait_for(lambda: q.snapshot()['halted'])
        assert len(calls) == 1
        assert q.snapshot()['jobs'][1]['status'] == 'pending'
        q.close()
        restored = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
        assert restored.snapshot()['halted']
        restored.resume()
        assert not restored.snapshot()['halted']
    finally:
        q.close()


def test_corrupt_state_fails_closed(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'queue.json').write_text('{bad')
    with pytest.raises(ValueError):
        QueueManager(tmp_path / 'downloads', state)
    assert (state / 'queue.json').read_text() == '{bad'


def test_state_write_failure_halts_without_accepting_unpersisted_jobs(tmp_path, monkeypatch):
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    def disk_failure(*args):
        raise OSError('disk is full')
    monkeypatch.setattr('uav_downloader.web.queue.os.replace', disk_failure)
    with pytest.raises(OSError):
        q.add([{'url': URL}])
    assert q.snapshot()['halted']
    assert q.snapshot()['jobs'] == []


def test_cancel_old_job_cannot_cancel_next_job(tmp_path):
    started = threading.Event()
    second = threading.Event()
    finish = threading.Event()
    calls = []
    def runner(job, root, cancel, progress):
        calls.append(job['id'])
        if len(calls) == 1:
            started.set()
            assert cancel.wait(2)
            return False
        second.set()
        assert finish.wait(2)
        assert not cancel.is_set()
        return True
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', runner=runner)
    try:
        a, b = q.add([{'url': URL}, {'url': URL.replace('123', '456')}])
        assert started.wait(2)
        q.cancel(a['id'])
        assert second.wait(2)
        q.cancel(a['id'])
        q.retry(a['id'])
        q.cancel(a['id'])
        finish.set()
        wait_for(lambda: q.snapshot()['jobs'][1]['status'] == 'completed')
        assert q.snapshot()['jobs'][0]['status'] == 'cancelled'
    finally:
        finish.set()
        q.close()


def test_cancel_at_completion_boundary_is_not_overwritten(tmp_path):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    def runner(*args):
        entered.set()
        assert release.wait(2)
        returned.set()
        return True
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', runner=runner)
    try:
        job = q.add([{'url': URL}])[0]
        assert entered.wait(2)
        with q._condition:
            release.set()
            assert returned.wait(2)
            time.sleep(.05)  # Worker reaches its final publication lock while we own it.
            q.cancel(job['id'])
        wait_for(lambda: q.snapshot()['jobs'][0]['status'] in {'cancelled', 'completed'})
        assert q.snapshot()['jobs'][0]['status'] == 'cancelled'
    finally:
        release.set()
        q.close()


def test_cancelled_transport_exception_is_cancelled_not_failed(tmp_path):
    entered = threading.Event()
    def runner(job, root, cancel, progress):
        entered.set()
        assert cancel.wait(2)
        raise ConnectionError('transport aborted after cancellation')
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', runner=runner)
    try:
        job = q.add([{'url': URL}])[0]
        assert entered.wait(2)
        q.cancel(job['id'])
        wait_for(lambda: q.snapshot()['jobs'][0]['status'] in {'cancelled', 'failed'})
        assert q.snapshot()['jobs'][0]['status'] == 'cancelled'
    finally:
        q.close()


@pytest.fixture
def web(tmp_path):
    q = QueueManager(tmp_path / 'downloads', tmp_path / 'state', autostart=False)
    server = make_server('127.0.0.1', 0, TOKEN, q)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    yield base, q
    server.shutdown()
    server.server_close()
    q.close()


def request(base, path, data=None, **headers):
    raw = None if data is None else json.dumps(data).encode()
    req = Request(base + path, data=raw, headers={
        'Content-Type': 'application/json', **headers})
    try:
        response = urlopen(req, timeout=3)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def test_api_auth_origin_host_and_no_token_query(web):
    base, q = web
    assert request(base, '/api/queue')[0] == 401
    assert request(base, '/api/queue?token=' + TOKEN)[0] == 400
    auth = {'Authorization': 'Bearer ' + TOKEN}
    assert request(base, '/api/queue', **auth)[0] == 200
    assert request(base, '/api/queue', Origin='https://evil.invalid', **auth)[0] == 403
    assert request(base, '/api/queue', Host='evil.invalid', **auth)[0] == 403
    status, headers, body = request(base, '/api/queue', {'jobs': [{'url': URL}]}, **auth)
    assert status == 201
    assert len(q.snapshot()['jobs']) == 1
    assert 'Access-Control-Allow-Origin' not in headers
    assert TOKEN.encode() not in body


def test_ui_is_static_safe_and_no_secret_persistence(web):
    base, q = web
    status, headers, body = request(base, '/')
    assert status == 200 and b'UAV Downloader Web' in body
    assert headers['Content-Security-Policy'].startswith("default-src 'self'")
    js = request(base, '/app.js')[2]
    for forbidden in (b'localStorage', b'sessionStorage', b'innerHTML', b'eval('):
        assert forbidden not in js
    assert request(base, '/../../queue.json')[0] == 404
    assert request(base, '/api/queue', {'jobs': [], 'token': TOKEN},
                   Authorization='Bearer ' + TOKEN)[0] == 400


@pytest.mark.parametrize('raw', ['[' * 1100 + '0' + ']' * 1100, '{broken'])
def test_malformed_or_deep_json_is_rejected(web, raw):
    base, _ = web
    conn = HTTPConnection(base.removeprefix('http://'), timeout=3)
    conn.request('POST', '/api/queue', body=raw, headers={
        'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    response = conn.getresponse()
    assert response.status == 400
    response.read()
    conn.close()


def test_oversized_body_rejected_before_read(web):
    base, _ = web
    conn = HTTPConnection(base.removeprefix('http://'), timeout=3)
    conn.request('POST', '/api/queue', body=b'', headers={
        'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json',
        'Content-Length': str(256 * 1024 + 1)})
    response = conn.getresponse()
    assert response.status == 413
    response.read()
    conn.close()


def test_adapter_storage_error_not_hidden_and_cap_reset(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uav_downloader.core.storage import StorageWriteError
    from uav_downloader.sites import base
    error = StorageWriteError(tmp_path, OSError('failed storage'))
    monkeypatch.setattr('uav_downloader.web.queue.sites.CreateSite',
        lambda *args, **kwargs: SimpleNamespace(_last_error=error, is_url_vaildate=lambda: False))
    with pytest.raises(StorageWriteError):
        run_download(validate_job({'url': URL, 'speed_limit_mib': 1}),
                     tmp_path, threading.Event(), lambda *args: None)
    assert base.speed_limiter.limit_bps == 0


def test_constructor_exception_resets_cap(tmp_path, monkeypatch):
    from uav_downloader.sites import base
    def fail(*args, **kwargs):
        raise ValueError('bad provider')
    monkeypatch.setattr('uav_downloader.web.queue.sites.CreateSite', fail)
    with pytest.raises(ValueError):
        run_download(validate_job({'url': URL, 'speed_limit_mib': 1}),
                     tmp_path, threading.Event(), lambda *args: None)
    assert base.speed_limiter.limit_bps == 0


def test_adapter_output_path_must_stay_under_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    fake = SimpleNamespace(is_url_vaildate=lambda: True, _dest_folder=tmp_path,
                           _temp_folder=tmp_path.parent / 'escape')
    monkeypatch.setattr('uav_downloader.web.queue.sites.CreateSite', lambda *args, **kwargs: fake)
    with pytest.raises(ValueError, match='escaped'):
        run_download(validate_job({'url': URL}), tmp_path,
                     threading.Event(), lambda *args: None)


def test_entrypoint_binds_before_starting_downloads(tmp_path, monkeypatch):
    from uav_downloader.web import __main__ as entry
    created = []
    def queue_factory(*args, **kwargs):
        queue = QueueManager(*args, **kwargs)
        created.append(queue)
        return queue
    def bind_failure(*args, **kwargs):
        raise OSError('port already in use')
    monkeypatch.setenv('UAV_WEB_TOKEN', TOKEN)
    monkeypatch.setenv('DOWNLOAD_DIR', str(tmp_path / 'downloads'))
    monkeypatch.setenv('UAV_WEB_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(entry, 'QueueManager', queue_factory)
    monkeypatch.setattr(entry, 'make_server', bind_failure)
    with pytest.raises(OSError):
        entry.main()
    assert len(created) == 1 and created[0]._thread is None
