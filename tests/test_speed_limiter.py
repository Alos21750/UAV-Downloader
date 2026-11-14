"""Behavioral regressions for the shared, dynamically configurable byte budget."""
import threading
import time
from types import SimpleNamespace

import pytest

from uav_downloader.sites import base, supjav


MIB = 1024 ** 2


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail('worker did not reach the controlled wait')
        threading.Event().wait(0.002)


def test_unlimited_releases_all_existing_waiters_and_new_calls():
    limiter = base._SpeedLimiter()
    limiter.set_limit(1 / MIB)
    results = []
    workers = [threading.Thread(target=lambda: results.append(limiter.acquire(100))) for _ in range(4)]
    try:
        for worker in workers:
            worker.start()
        wait_for(lambda: limiter._tokens < 1 and all(t.is_alive() for t in workers))
        limiter.set_limit(0)
        for worker in workers:
            worker.join(1)
        assert not any(worker.is_alive() for worker in workers), 'unlimited did not release existing waits'
        assert results == [True] * 4
        assert limiter.acquire(1000000) is True
    finally:
        limiter.set_limit(100)
        for worker in workers:
            worker.join(2)
        assert not any(worker.is_alive() for worker in workers)


def test_stop_exits_wait_without_changing_other_download_limits(monkeypatch):
    limiter = base._SpeedLimiter()
    limiter.set_limit(1 / MIB)
    stopped, waiting = threading.Event(), threading.Event()
    real_wait = limiter._condition.wait
    def observe_wait(timeout=None):
        waiting.set()
        return real_wait(timeout)
    monkeypatch.setattr(limiter._condition, 'wait', observe_wait)
    results = []
    worker = threading.Thread(target=lambda: results.append(limiter.acquire(100, stopped.is_set)))
    try:
        worker.start()
        assert waiting.wait(2)
        stopped.set()
        worker.join(1)
        assert not worker.is_alive()
        assert results == [False]
        assert limiter.limit_bps == 1
    finally:
        stopped.set()
        limiter.set_limit(100)
        worker.join(2)


def test_already_stopped_unlimited_and_empty_calls_are_rejected():
    limiter = base._SpeedLimiter()
    assert limiter.acquire(100, lambda: True) is False
    assert limiter.acquire(0, lambda: True) is False
    assert limiter.acquire(0) is True


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


def virtual_limiter(monkeypatch, rate):
    clock = Clock()
    limiter = base._SpeedLimiter(clock=clock)
    waits = []
    def wait(timeout):
        waits.append(timeout)
        clock.advance(timeout)
    monkeypatch.setattr(limiter._condition, 'wait', wait)
    limiter.set_limit(rate / MIB)
    return limiter, clock, waits


def test_consecutive_downloads_share_one_global_budget(monkeypatch):
    limiter, clock, _ = virtual_limiter(monkeypatch, 10)
    assert limiter.acquire(10) is True  # One initial bucket for the whole limiter.
    assert limiter.acquire(10) is True  # Another download must spend a second.
    assert limiter.acquire(10) is True
    assert clock.now == pytest.approx(102, abs=0.06)


@pytest.mark.parametrize('next_rate,expected_wait', [(10, 1), (20, 0.5), (5, 2)])
def test_rate_changes_do_not_replenish_spent_budget(monkeypatch, next_rate, expected_wait):
    limiter, clock, _ = virtual_limiter(monkeypatch, 10)
    limiter.acquire(10)
    for _ in range(10):
        limiter.set_limit(next_rate / MIB)
    limiter.acquire(10)
    assert clock.now - 100 == pytest.approx(expected_wait, abs=0.06)


def test_lowering_limit_clamps_accrued_credit_to_one_bucket(monkeypatch):
    limiter, clock, _ = virtual_limiter(monkeypatch, 10)
    limiter.acquire(10)
    clock.advance(0.5)
    limiter.set_limit(2 / MIB)
    limiter.acquire(2)
    assert clock.now == 100.5
    limiter.acquire(2)
    assert clock.now == pytest.approx(101.5, abs=0.06)


def test_alternating_caps_share_spent_budget_without_new_bursts(monkeypatch):
    limiter, clock, _ = virtual_limiter(monkeypatch, 10)
    limiter.acquire(10)
    for rate in [20, 1, 30, 2, 10] * 5:
        limiter.set_limit(rate / MIB)
    limiter.acquire(10)
    assert clock.now == pytest.approx(101, abs=0.06)


def test_wall_clock_changes_do_not_affect_budget(monkeypatch):
    limiter, clock, _ = virtual_limiter(monkeypatch, 10)
    limiter.acquire(10)
    monkeypatch.setattr(base.time, 'time', lambda: -1000000)
    limiter.acquire(10)
    monkeypatch.setattr(base.time, 'time', lambda: 10000000000)
    limiter.acquire(10)
    assert clock.now == pytest.approx(102, abs=0.06)


def hls_job(tmp_path, monkeypatch):
    job = base.M3U8Crawler.__new__(base.M3U8Crawler)
    job._cancel_job = False
    job._storage_error = None
    job._temp_folder = str(tmp_path)
    job._m3u8_headers = lambda: {}
    job._key_content = None
    job._transform_segment = lambda data: data
    job._speed_lock = threading.Lock()
    job._pending_set = {(0, 'https://example.test/segment')}
    job._bytes_downloaded = 0
    job._speed_start = time.time()
    job._job_total = 1
    job._progress_callback = None
    monkeypatch.setattr(base, '_http_get', lambda *a, **kw: SimpleNamespace(status_code=200, content=b'\x47' + bytes(187)))
    return job


def test_hls_cancel_while_throttled_does_not_write_or_publish(tmp_path, monkeypatch):
    job = hls_job(tmp_path, monkeypatch)
    limiter = base._SpeedLimiter()
    limiter.set_limit(1 / MIB)
    monkeypatch.setattr(base, 'speed_limiter', limiter)
    results = []
    worker = threading.Thread(target=lambda: results.append(job._scrape((0, 'https://example.test/segment'))))
    try:
        worker.start()
        wait_for(lambda: limiter._tokens < 1 and worker.is_alive())
        job._cancel_job = True
        worker.join(1)
        assert not worker.is_alive()
        assert results == [False]
        assert list(tmp_path.iterdir()) == []
        assert job._bytes_downloaded == 0 and job._pending_set
    finally:
        job._cancel_job = True
        limiter.set_limit(100)
        worker.join(2)


@pytest.mark.parametrize('parallel', [False, True])
def test_direct_cancel_while_throttled_does_not_write_chunk(tmp_path, monkeypatch, parallel):
    payload = b'a' * 262144
    closed = []
    class Response:
        def __init__(self, start=None, end=None):
            self.status_code = 200 if start is None else 206
            self.headers = {'content-length': str(len(payload))} if start is None else {
                'content-range': f'bytes {start}-{end}/{len(payload)}'}
            self.data = payload if start is None else payload[start:end+1]
        def iter_content(self, chunk_size):
            yield self.data
        def close(self):
            closed.append(True)
    def get(url, headers, **kwargs):
        bounds = headers.get('Range')
        if not bounds:
            return Response()
        start, end = map(int, bounds.removeprefix('bytes=').split('-'))
        return Response(start, end)
    monkeypatch.setattr(supjav, '_get_session', lambda: SimpleNamespace(get=get))
    limiter = base._SpeedLimiter()
    limiter.set_limit(1 / MIB)
    monkeypatch.setattr(supjav, 'speed_limiter', limiter)
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._cancel_job = False
    job._direct_url = 'https://example.test/video'
    job._progress_callback = None
    job._max_workers = 2
    part = tmp_path / 'video.part'
    method = job._download_direct_ranges if parallel else job._download_direct_serial
    results, errors = [], []
    def run():
        try:
            results.append(method(str(part), '', time.time()))
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    try:
        worker.start()
        wait_for(lambda: limiter._tokens < 1 and worker.is_alive())
        job._cancel_job = True
        worker.join(1)
        assert not worker.is_alive() and not errors
        assert results[0][0] == 0
        assert part.read_bytes() == (bytes(len(payload)) if parallel else b'')
        assert closed
    finally:
        job._cancel_job = True
        limiter.set_limit(100)
        worker.join(2)


def test_parallel_peer_failure_interrupts_other_throttled_range(tmp_path, monkeypatch):
    limiter = base._SpeedLimiter()
    limiter.set_limit(1 / MIB)
    waiting = threading.Event()
    real_wait = limiter._condition.wait
    def observe_wait(timeout=None):
        waiting.set()
        return real_wait(timeout)
    monkeypatch.setattr(limiter._condition, 'wait', observe_wait)
    monkeypatch.setattr(supjav, 'speed_limiter', limiter)
    monkeypatch.setattr(supjav, '_DIRECT_RANGE_RETRIES', 0)
    total = 262144
    class Response:
        status_code = 206
        def __init__(self, start, end):
            self.headers = {'content-range': f'bytes {start}-{end}/{total}'}
            self.data = b'x' * (end-start+1)
        def iter_content(self, chunk_size):
            yield self.data
        def close(self):
            pass
    def get(url, headers, **kwargs):
        start, end = map(int, headers['Range'].removeprefix('bytes=').split('-'))
        if start == 0 and end > 0:
            assert waiting.wait(2), 'peer must already be throttled before failure'
            raise ConnectionError('synthetic failed range')
        return Response(start, end)
    monkeypatch.setattr(supjav, '_get_session', lambda: SimpleNamespace(get=get))
    job = supjav.SiteSupJav.__new__(supjav.SiteSupJav)
    job._cancel_job = False
    job._direct_url = 'https://example.test/video'
    job._progress_callback = None
    job._max_workers = 2
    errors = []
    part = tmp_path / 'peer-failure.part'
    def run():
        try:
            job._download_direct_ranges(str(part), '', time.time())
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    try:
        worker.start()
        worker.join(3)
        assert not worker.is_alive(), 'peer failure left another range blocked in throttle'
        assert len(errors) == 1 and isinstance(errors[0], ConnectionError)
        assert not job._cancel_job  # The range stop event, not user cancel, released the peer.
        assert part.read_bytes() == bytes(total)
    finally:
        job._cancel_job = True
        limiter.set_limit(100)
        worker.join(2)
