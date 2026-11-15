"""Anonymous playback metadata, without credentials or media requests."""
import json
import time
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
import requests
from bs4 import BeautifulSoup

from uav_downloader.sites import huangguo as hg


PAGE = 'https://huangguoai.com/video/42/ep-3/'
API = 'https://huangguoai.com/api/videos/42/episodes/3/playback'


def playback(**changes):
    return {'id': 42, 'ep': 3, 'manifest_method': 'GET',
            'streams': {'main': 'https://cdn.example.test/index.m3u8?sig=PRIVATE'},
            'cdn': {'m3u8Enabled': True, 'otherEnabled': False,
                    'authKey': 'x-cdn-auth', 'authValue': 'PRIVATE', 'app': '150'},
            **changes}


class Response:
    def __init__(self, body, status=200, headers=None):
        self.body = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, _size):
        yield self.body

    def close(self):
        self.closed = True


@pytest.fixture
def source(monkeypatch):
    data = {'id': '42', 'ep': 3, 'title': 'Fixture drama', 'videoSrc': '',
            'videoApi': '', 'videoKey': '', 'videoIv': ''}
    soup = BeautifulSoup('<script id="videoInitialData" type="application/json">'
                         + json.dumps(data) + '</script>', 'html.parser')
    monkeypatch.setattr(hg, '_page_soup', lambda _url: soup)
    monkeypatch.setattr(hg, '_verify_public_destination', lambda _url: None)
    calls, responses = [], []
    session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar())
    session.cookies.set('old-session', 'PRIVATE')

    def post(url, **kwargs):
        assert not session.cookies
        calls.append((url, kwargs))
        return responses.pop(0)

    session.post = post
    monkeypatch.setattr(hg, '_session', lambda kind: session)
    return calls, responses


def test_empty_ssr_source_resolves_from_anonymous_playback(source, capsys):
    calls, responses = source
    response = Response({'status': 1, 'data': playback()})
    responses.append(response)
    crawler = hg.SiteHuangguo(PAGE, silence=True)
    assert crawler.is_url_vaildate()
    assert crawler._m3u8url.endswith('sig=PRIVATE')
    assert crawler._media_headers(crawler._m3u8url)['x-cdn-auth'] == 'PRIVATE'
    assert crawler._cdn['other'] is False
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == API
    assert kwargs['json'] == {}
    assert kwargs['allow_redirects'] is False
    assert kwargs['stream'] is True
    assert 'Authorization' not in kwargs['headers']
    assert 'Cookie' not in kwargs['headers']
    assert response.closed
    assert 'PRIVATE' not in capsys.readouterr().out


@pytest.mark.parametrize('changes', [
    {'id': 43}, {'ep': 1}, {'ep': True}, {'manifest_method': 'POST'},
    {'streams': {'main': 'http://cdn.example.test/index.m3u8'}},
    {'streams': {'main': 'https://127.0.0.1/index.m3u8'}},
    {'streams': {'main': 'https://cdn.example.test/video.mp4'}},
    {'streams': None}, {'cdn': None},
    {'cdn': {'m3u8Enabled': True, 'authKey': 'Authorization', 'authValue': 'PRIVATE', 'app': '150'}},
    {'cdn': {'m3u8Enabled': True, 'authKey': 'x-cdn-auth', 'authValue': 'PRIVATE\r\nCookie: bad', 'app': '150'}},
])
def test_invalid_playback_payload_is_rejected(source, changes, capsys):
    calls, responses = source
    response = Response({'status': 1, 'data': playback(**changes)})
    responses.append(response)
    crawler = hg.SiteHuangguo(PAGE, silence=True)
    assert not crawler.is_url_vaildate()
    assert len(calls) == 1
    assert response.closed
    assert 'PRIVATE' not in capsys.readouterr().out


@pytest.mark.parametrize('status,headers,body', [
    (401, {}, {'msg': 'PRIVATE'}),
    (403, {}, {'msg': 'PRIVATE'}),
    (403, {'cf-mitigated': 'challenge'}, b'challenge'),
    (302, {'Location': 'https://elsewhere.example/?token=PRIVATE'}, b''),
    (429, {'Retry-After': '60'}, {'msg': 'PRIVATE'}),
    (503, {}, b'PRIVATE'),
    (200, {}, {'code': 403, 'msg': 'PRIVATE'}),
    (200, {}, {'status': 0, 'data': playback()}),
    (200, {}, b'PRIVATE not json'),
    (200, {}, b'X' * (256 * 1024 + 1)),
], ids=['unauthorized', 'forbidden', 'challenge', 'redirect', 'rate-limit',
        'server-error', 'json-auth-error', 'json-failure', 'invalid-json', 'oversized'])
def test_failure_never_follows_redirects_or_retries_auth(source, status, headers, body, capsys):
    calls, responses = source
    response = Response(body, status, headers)
    responses.append(response)
    crawler = hg.SiteHuangguo(PAGE, silence=True)
    assert not crawler.is_url_vaildate()
    assert len(calls) == 1
    assert response.closed
    assert 'PRIVATE' not in capsys.readouterr().out


def test_each_job_gets_fresh_playback_metadata(source):
    calls, responses = source
    for number in (1, 2):
        responses.append(Response({'code': 200, 'data': playback(
            streams={'main': f'https://cdn.example.test/index.m3u8?sig={number}'})}))
    first = hg.SiteHuangguo(PAGE, silence=True)
    second = hg.SiteHuangguo(PAGE, silence=True)
    assert first.is_url_vaildate() and second.is_url_vaildate()
    assert first._m3u8url != second._m3u8url
    assert len(calls) == 2


def test_playback_respects_proxy_snapshot_and_parent_deadline(source, monkeypatch):
    calls, responses = source
    responses.append(Response({'status': 1, 'data': playback()}))
    proxy = {'proxies': {'https': 'http://127.0.0.1:8765'}}
    monkeypatch.setattr(hg.config, 'proxy_request_kwargs', lambda: proxy)
    with hg._operation(deadline=time.monotonic() + 2):
        hg._playback_data(PAGE)
    assert calls[0][1]['proxies'] == proxy['proxies']
    assert 0 < calls[0][1]['timeout'] <= 2


def test_cancelled_playback_does_not_contact_endpoint(source):
    calls, _responses = source
    cancelled = [False]
    with hg._operation(cancel_check=lambda: cancelled[0]):
        cancelled[0] = True
        with pytest.raises(CancelledError):
            hg._playback_data(PAGE)
    assert calls == []


@pytest.mark.parametrize('error', [requests.Timeout, requests.ConnectionError,
                                  requests.exceptions.SSLError])
def test_transport_error_is_sanitized(source, monkeypatch, error, capsys):
    session = hg._session('playback')
    def fail(*_args, **_kwargs):
        raise error('https://upstream.test/?token=PRIVATE')
    session.post = fail
    crawler = hg.SiteHuangguo(PAGE, silence=True)
    assert not crawler.is_url_vaildate()
    assert not session.cookies
    assert 'PRIVATE' not in capsys.readouterr().out
