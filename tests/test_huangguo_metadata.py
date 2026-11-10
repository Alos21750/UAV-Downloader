"""Independent offline regression coverage for UAV metadata request control."""

import json
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from bs4 import BeautifulSoup

from uav_downloader.sites import huangguo as hg


URL = 'https://huangguoai.com/video/42/'


def detail():
    data = {'id': '42', 'ep': 1, 'title': 'Sample',
            'videoSrc': 'https://media.example/index.m3u8?secret=not-for-cache',
            'cdnAuthValue': 'not-for-cache'}
    return BeautifulSoup(
        '<script id="videoInitialData" type="application/json">' + json.dumps(data) +
        '</script><script type="application/ld+json">'
        '{"@type":"WebPage","dateModified":"2026-11-03T19:00:00Z"}</script>'
        '<div data-web-play><a class="hg-web-play__ep" data-ep-id="1" '
        'href="/video/42/">1</a></div>', 'html.parser')


def clear_cache():
    clear = getattr(hg.HuangguoBrowser, 'clear_metadata_cache', None)
    if clear:
        clear()


@pytest.fixture(autouse=True)
def isolate_metadata():
    clear_cache()
    yield
    clear_cache()


def test_metadata_cache_copies_results_and_shares_series_date(monkeypatch):
    calls = []
    monkeypatch.setattr(hg, '_page_soup', lambda url: (calls.append(url), detail())[1])
    first = hg.HuangguoBrowser.fetch_episodes(URL)
    first[0]['title'] = 'Caller edit'
    second = hg.HuangguoBrowser.fetch_episodes(URL)
    assert second[0]['title'] == 'Sample - EP 1'
    assert hg.HuangguoBrowser.fetch_video_date(URL) == '2026-11-04'
    assert calls == [URL]
    assert 'not-for-cache' not in repr(hg._metadata_cache)


def test_metadata_revision_invalidates_even_within_same_day(monkeypatch):
    calls = []
    monkeypatch.setattr(hg, '_page_soup', lambda url: (calls.append(url), detail())[1])
    for revision in ['1793620000', '1793620000', '1793620001']:
        hg.HuangguoBrowser.fetch_episodes(URL, revision=revision)
    assert len(calls) == 2


def test_metadata_cache_is_proxy_scoped(monkeypatch):
    calls, proxy = [], {'value': 'http://proxy-one.example:8080'}
    monkeypatch.setattr(hg.config, 'proxy_request_kwargs', lambda: {'proxies': {'https': proxy['value']}})
    monkeypatch.setattr(hg, '_page_soup', lambda url: (calls.append(url), detail())[1])
    hg.HuangguoBrowser.fetch_episodes(URL)
    proxy['value'] = 'http://proxy-two.example:8080'
    hg.HuangguoBrowser.fetch_episodes(URL)
    assert len(calls) == 2


def test_concurrent_metadata_readers_share_one_fetch(monkeypatch):
    calls, entered, release = [], threading.Event(), threading.Event()
    def load(url):
        calls.append(url)
        entered.set()
        assert release.wait(3)
        return detail()
    monkeypatch.setattr(hg, '_page_soup', load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(hg.HuangguoBrowser.fetch_episodes, URL)
        assert entered.wait(3)
        two = pool.submit(hg.HuangguoBrowser.fetch_episodes, URL)
        release.set()
        assert one.result() == two.result()
    assert calls == [URL]


def test_cancelled_metadata_lookup_does_not_start_request(monkeypatch):
    monkeypatch.setattr(hg, '_page_soup', lambda _: pytest.fail('must not request'))
    with pytest.raises(CancelledError):
        hg.HuangguoBrowser.fetch_episodes(URL, cancel_check=lambda: True)


def test_malformed_jsonld_type_does_not_crash_or_replace_real_date():
    soup = BeautifulSoup('<script type="application/ld+json">'
                         '[{"@type":[{}],"dateModified":"2020-01-01"},'
                         '{"@type":"WebPage","dateModified":"2026-11-03"}]'
                         '</script>', 'html.parser')
    assert hg._detail_date(soup, {}) == '2026-11-03'


def test_expired_metadata_must_reload(monkeypatch):
    clock, calls = {'now': 0}, []
    monkeypatch.setattr(hg.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(hg, '_page_soup', lambda url: (calls.append(url), detail())[1])
    hg.HuangguoBrowser.fetch_episodes(URL)
    clock['now'] = 10000
    hg.HuangguoBrowser.fetch_episodes(URL)
    assert len(calls) == 2


def test_cache_eviction_obeys_total_episode_and_byte_budgets(monkeypatch):
    calls = []
    monkeypatch.setattr(hg, '_METADATA_MAX_EPISODES', 1)
    def load(value):
        calls.append(value)
        return [{'title': value}]
    with hg._operation():
        hg._metadata('one', None, lambda: load('one'))
        hg._metadata('two', None, lambda: load('two'))
        hg._metadata('one', None, lambda: load('one'))
    assert calls == ['one', 'two', 'one']
    clear_cache()
    monkeypatch.setattr(hg, '_METADATA_MAX_BYTES', 2)
    with hg._operation():
        hg._metadata('oversized', None, lambda: load('oversized'))
    assert not hg._metadata_cache


def test_failed_metadata_load_is_not_cached(monkeypatch):
    calls = []
    def load(url):
        calls.append(url)
        if len(calls) == 1:
            raise ValueError('fixture failure')
        return detail()
    monkeypatch.setattr(hg, '_page_soup', load)
    with pytest.raises(ValueError, match='fixture failure'):
        hg.HuangguoBrowser.fetch_episodes(URL)
    assert hg.HuangguoBrowser.fetch_episodes(URL)
    assert len(calls) == 2 and not hg._metadata_inflight


def test_cancelled_load_never_publishes_cache(monkeypatch):
    stop = threading.Event()
    def load(url):
        stop.set()
        return detail()
    monkeypatch.setattr(hg, '_page_soup', load)
    with pytest.raises(CancelledError):
        hg.HuangguoBrowser.fetch_episodes(URL, cancel_check=stop.is_set)
    assert not hg._metadata_cache and not hg._metadata_inflight


def test_waiter_cancel_does_not_cancel_shared_producer(monkeypatch):
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def load(url):
        calls.append(url)
        entered.set()
        assert release.wait(3)
        return detail()
    monkeypatch.setattr(hg, '_page_soup', load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(hg.HuangguoBrowser.fetch_episodes, URL)
        assert entered.wait(3)
        waiter = pool.submit(hg.HuangguoBrowser.fetch_episodes, URL, cancel_check=stop.is_set)
        stop.set()
        with pytest.raises(CancelledError):
            waiter.result(timeout=1)
        release.set()
        assert owner.result(timeout=2)
    assert calls == [URL] and hg.HuangguoBrowser.fetch_episodes(URL)


class Response:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.headers = {} if retry_after is None else {'Retry-After': retry_after}
        self.closed = False

    def close(self):
        self.closed = True


def fake_transport(monkeypatch, responses):
    calls, waits = [], []
    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
    monkeypatch.setattr(hg, '_session', lambda kind: Session())
    monkeypatch.setattr(hg, '_verify_public_destination', lambda url: None)
    monkeypatch.setattr(hg, '_wait_controlled', lambda seconds: waits.append(seconds))
    return calls, waits


def test_403_is_terminal_but_429_cooldown_can_recover(monkeypatch):
    calls, waits = fake_transport(monkeypatch, [Response(403)])
    with pytest.raises(RuntimeError, match='403'):
        hg._get(URL, page=True)
    assert len(calls) == 1 and not waits
    transient = Response(429, '2')
    calls, waits = fake_transport(monkeypatch, [transient, Response(200)])
    assert hg._get(URL, page=True).status_code == 200
    assert len(calls) == 2 and waits == [2] and transient.closed


def test_server_long_cooldown_is_not_shortened(monkeypatch):
    calls, waits = fake_transport(monkeypatch, [Response(429, '120')])
    with pytest.raises(hg.HuangguoTransientError) as caught:
        hg._get(URL, page=True)
    assert caught.value.retry_after == 120
    assert len(calls) == 1 and not waits
    assert not isinstance(caught.value, hg.MirrorsBlockedError)


def test_503_retry_is_bounded_and_404_not_retried(monkeypatch):
    calls, waits = fake_transport(monkeypatch, [Response(503), Response(503), Response(503)])
    with pytest.raises(hg.HuangguoTransientError) as caught:
        hg._get(URL, page=True)
    assert caught.value.status_code == 503
    assert len(calls) == 3 and waits == [1, 2]
    calls, waits = fake_transport(monkeypatch, [Response(404)])
    assert hg._get(URL, page=True).status_code == 404
    assert len(calls) == 1 and not waits


def test_transient_network_error_does_not_echo_credentials(monkeypatch):
    errors = [hg.requests.Timeout('https://media.example/?secret=do-not-log')] * 3
    calls, _ = fake_transport(monkeypatch, errors)
    with pytest.raises(hg.HuangguoTransientError) as caught:
        hg._get(URL, page=True)
    assert 'secret' not in str(caught.value) and len(calls) == 3


def test_deadline_is_clamped_into_transport_timeout(monkeypatch):
    calls, _ = fake_transport(monkeypatch, [Response(200)])
    monkeypatch.setattr(hg.time, 'monotonic', lambda: 100)
    with hg._operation(deadline=101):
        hg._get(URL, page=True)
    assert calls[0][1]['timeout'] == 1


def test_proxy_snapshot_cannot_switch_halfway_through_operation(monkeypatch):
    value = {'proxy': 'http://first.example:80'}
    monkeypatch.setattr(hg.config, 'proxy_request_kwargs', lambda: {'proxies': {'https': value['proxy']}})
    calls, _ = fake_transport(monkeypatch, [Response(200)])
    with hg._operation():
        value['proxy'] = 'http://second.example:80'
        hg._get(URL, page=True)
    assert calls[0][1]['proxies']['https'] == 'http://first.example:80'


def test_producer_deadline_expiry_does_not_poison_healthy_waiter(monkeypatch):
    clock = {'now': 0}
    monkeypatch.setattr(hg.time, 'monotonic', lambda: clock['now'])
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def load(url):
        calls.append(url)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return detail()
    monkeypatch.setattr(hg, '_page_soup', load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(hg.HuangguoBrowser.fetch_episodes, URL, deadline=1)
        assert entered.wait(3)
        with hg._metadata_lock:
            flight = next(iter(hg._metadata_inflight.values()))
        original_wait = flight['event'].wait
        def observed_wait(timeout):
            waiting.set()
            return original_wait(timeout)
        monkeypatch.setattr(flight['event'], 'wait', observed_wait)
        waiter = pool.submit(hg.HuangguoBrowser.fetch_episodes, URL, deadline=100)
        assert waiting.wait(3)
        clock['now'] = 2
        release.set()
        with pytest.raises(TimeoutError):
            owner.result(timeout=2)
        assert waiter.result(timeout=2)
    assert len(calls) == 2


def test_plain_forbidden_is_not_reported_as_cloudflare(monkeypatch):
    calls, waits = fake_transport(monkeypatch, [Response(403)])
    with pytest.raises(RuntimeError, match='403') as caught:
        hg._get(URL, page=True)
    assert not isinstance(caught.value, hg.MirrorsBlockedError)
    assert len(calls) == 1 and not waits


@pytest.mark.parametrize('status', [403, 503])
def test_explicit_challenge_is_terminal_even_for_transient_http_status(monkeypatch, status):
    response = Response(status)
    response.headers['cf-mitigated'] = 'challenge'
    calls, waits = fake_transport(monkeypatch, [response])
    with pytest.raises(hg.MirrorsBlockedError):
        hg._get(URL, page=True)
    assert len(calls) == 1 and not waits


def test_metadata_titles_and_revisions_cannot_amplify_cache_memory():
    assert len(hg._clean('x' * 100000)) == 1000
    assert hg._revision('9' * 100000) is None
    assert hg._revision({'untrusted': []}) is None
    assert hg._revision('1793620001') == '1793620001'


def test_retry_after_supports_http_date_and_ignores_malformed_values():
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    assert 110 < hg._retry_after(value) <= 120
    for value in ['nonsense', '-4', 'NaN', '']:
        assert hg._retry_after(value) is None


def test_cancel_during_http_read_closes_response(monkeypatch):
    stop = threading.Event()
    class Streaming(Response):
        def iter_content(self, size):
            stop.set()
            yield b'<html></html>'
    response = Streaming(200)
    monkeypatch.setattr(hg, '_get', lambda *_args, **_kwargs: response)
    with hg._operation(cancel_check=stop.is_set):
        with pytest.raises(CancelledError):
            hg._page_soup(URL)
    assert response.closed


def test_actual_series_count_budget_evicts(monkeypatch):
    monkeypatch.setattr(hg, '_METADATA_MAX_SERIES', 1)
    with hg._operation():
        hg._metadata('first', None, lambda: [{'title': 'first'}])
        hg._metadata('second', None, lambda: [{'title': 'second'}])
    assert len(hg._metadata_cache) == 1
    assert next(iter(hg._metadata_cache.values()))['items'][0]['title'] == 'second'


def test_terminal_transport_timeout_keeps_expired_caller_deadline(monkeypatch):
    clock, calls = {'now': 0}, []
    monkeypatch.setattr(hg.time, 'monotonic', lambda: clock['now'])
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 3:
                clock['now'] = 2
            raise hg.requests.Timeout('fixture transport timeout')
    monkeypatch.setattr(hg, '_session', lambda kind: Session())
    monkeypatch.setattr(hg, '_verify_public_destination', lambda url: None)
    monkeypatch.setattr(hg, '_wait_controlled', lambda seconds: None)
    with hg._operation(deadline=1):
        with pytest.raises(hg._MetadataDeadline):
            hg._get(URL, page=True)
    assert len(calls) == 3


def test_partial_read_timeout_keeps_expired_caller_deadline(monkeypatch):
    clock = {'now': 0}
    monkeypatch.setattr(hg.time, 'monotonic', lambda: clock['now'])
    class Streaming(Response):
        def iter_content(self, size):
            yield b'<html>'
            clock['now'] = 2
            raise hg.requests.Timeout('fixture body timeout')
    response = Streaming(200)
    monkeypatch.setattr(hg, '_get', lambda *_args, **_kwargs: response)
    with hg._operation(deadline=1):
        with pytest.raises(hg._MetadataDeadline):
            hg._page_soup(URL)
    assert response.closed
