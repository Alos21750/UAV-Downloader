import concurrent.futures
import threading
import time

from uav_downloader import sites as M3U8Sites
from uav_downloader.core import config
from uav_downloader.sites import base as crawler_mod
from uav_downloader.sites.missav import SiteMissAV


class _DummyCrawler(crawler_mod.M3U8Crawler):
    website_dirname_pattern = r'https://example\.test/(.+)$'

    def get_url_infos(self):
        self._targetName = 'example'
        self._m3u8url = 'https://cdn.example.test/master.m3u8'


class _DummyMissAV(SiteMissAV):
    website_dirname_pattern = r'https://example\.test/(.+)$'

    def get_url_infos(self):
        self._targetName = 'MISS-001 example'
        self._m3u8url = 'https://surrit.example.test/master.m3u8'


def test_crawler_accepts_a_clamped_per_video_worker_limit(tmp_path):
    low = _DummyCrawler(
        'https://example.test/video', str(tmp_path), silence=True,
        max_workers=0)
    high = _DummyCrawler(
        'https://example.test/video', str(tmp_path), silence=True,
        max_workers=999)

    assert low._max_workers == 1
    assert high._max_workers == config.MAX_WORKERS_PER_VIDEO == 16


def test_crawler_uses_persisted_worker_limit_when_not_explicit(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_max_workers_per_video', lambda: 3)

    crawler = _DummyCrawler(
        'https://example.test/video', str(tmp_path), silence=True)

    assert crawler._max_workers == 3


def test_missav_caps_each_video_below_the_shared_user_limit(tmp_path):
    crawler = _DummyMissAV(
        'https://example.test/video', str(tmp_path), silence=True,
        max_workers=config.MAX_WORKERS_PER_VIDEO)

    assert crawler._max_workers == 4


def test_missav_limits_segment_requests_across_parallel_videos(monkeypatch):
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_scrape(_self, _task):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return True

    monkeypatch.setattr(crawler_mod.M3U8Crawler, '_scrape', fake_scrape)
    crawlers = [object.__new__(SiteMissAV) for _ in range(3)]
    jobs = [(crawlers[index % len(crawlers)], index) for index in range(24)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
        list(executor.map(lambda pair: pair[0]._scrape(pair[1]), jobs))

    assert 1 < peak <= 8


def test_create_site_forwards_explicit_worker_limit(monkeypatch, tmp_path):
    captured = {}

    class _FakeSite:
        @classmethod
        def validate_url(cls, url):
            return 'video'

        def __init__(self, url, savepath='', silence=False, max_workers=None):
            captured.update(
                url=url, savepath=savepath, silence=silence,
                max_workers=max_workers)

    monkeypatch.setattr(M3U8Sites, 'siteList', (_FakeSite,))

    result = M3U8Sites.CreateSite(
        'https://example.test/video', str(tmp_path), silence=True,
        max_workers=4)

    assert isinstance(result, _FakeSite)
    assert captured['max_workers'] == 4


def test_segment_executor_receives_the_job_worker_limit(monkeypatch):
    seen = []

    class _ImmediateExecutor:
        def __init__(self, max_workers):
            seen.append(max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def map(self, fn, tasks, timeout=None):
            return [fn(task) for task in tasks]

    crawler = _DummyCrawler.__new__(_DummyCrawler)
    crawler._max_workers = 2
    crawler._tsList = ['https://cdn.example.test/0.ts']
    crawler._pending_set = {(0, 'https://cdn.example.test/0.ts')}
    crawler._cancel_job = False
    crawler._speed_start = 0
    crawler._bytes_downloaded = 0
    crawler._t2_executor = None
    crawler._scrape = lambda task: crawler._pending_set.discard(task) or True

    monkeypatch.setattr(
        concurrent.futures, 'ThreadPoolExecutor', _ImmediateExecutor)

    crawler._startCrawl()

    assert seen == [2]


def test_site_specific_retry_rounds_are_honored(monkeypatch):
    rounds = []

    class _ImmediateExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def __enter__(self):
            rounds.append(self.max_workers)
            return self

        def __exit__(self, *_args):
            return False

        def map(self, fn, tasks, timeout=None):
            return [fn(task) for task in tasks]

    crawler = _DummyCrawler.__new__(_DummyCrawler)
    crawler._max_workers = 2
    crawler._segment_retry_rounds = 10
    crawler._segment_retry_base_delay = 0
    crawler._segment_retry_max_delay = 0
    crawler._tsList = ['https://cdn.example.test/0.ts']
    crawler._pending_set = {(0, crawler._tsList[0])}
    crawler._cancel_job = False
    crawler._speed_start = 0
    crawler._bytes_downloaded = 0
    crawler._t2_executor = None
    crawler._scrape = lambda _task: False

    monkeypatch.setattr(
        concurrent.futures, 'ThreadPoolExecutor', _ImmediateExecutor)
    monkeypatch.setattr(time, 'sleep', lambda _seconds: None)

    crawler._startCrawl()

    assert len(rounds) == 10
