"""Offline protocol fixtures for the public short-drama adapter."""

import json
import socket

import pytest
from bs4 import BeautifulSoup

from uav_downloader.sites import huangguo as hg
from uav_downloader.sites.huangguo import HuangguoBrowser, SiteHuangguo, canonical_url


@pytest.mark.parametrize('url, identity, canonical', [
    ('https://huangguoai.com/video/42/', 'huangguo-42-ep-1',
     'https://huangguoai.com/video/42/'),
    ('https://www.huangguoai.com/video/42/ep-3/',
     'huangguo-42-ep-3', 'https://huangguoai.com/video/42/ep-3/'),
    ('https://huangguoai.com/video/42/ep-1/', 'huangguo-42-ep-1',
     'https://huangguoai.com/video/42/'),
])
def test_url_gate_preserves_episode_identity(url, identity, canonical):
    assert SiteHuangguo.validate_url(url) == identity
    assert canonical_url(url) == canonical


@pytest.mark.parametrize('url', [
    'http://huangguoai.com/video/42/',
    'https://huangguoai.com.evil.test/video/42/',
    'https://evil.test/huangguoai.com/video/42/',
    'https://user:secret@huangguoai.com/video/42/',
    'https://@huangguoai.com/video/42/',
    'https://huangguoai.com:444/video/42/',
    'https://huangguoai.com/video/42/ep-0/',
    'https://huangguoai.com/video/42/ep-03/',
    'https://huangguoai.com/video/42/ep-3/extra',
    'https://huangguoai.com/video/42/?ep=3',
    'https://huangguoai.com/video/42/?utm_source=test',
    'https://huangguoai.com/video/42/#player',
    'https://huangguoai.com/video/42/?',
    'https://huangguoai.com/video/42/#',
    'https://huangguoai.com/video/42/\n',
    'https://huangguoai.com/video/42/%2e%2e/',
    'https://huangguoai.com/video/42\\ep-3/',
    'https://huangguoai.com/newest',
])
def test_url_gate_rejects_unrelated_or_ambiguous_inputs(url):
    assert SiteHuangguo.validate_url(url) is None
    assert canonical_url(url) == ''


def payload(**changes):
    return {'id': '42', 'ep': 1, 'title': 'Sample Drama',
            'time': '2020-01-01 01:01:01',
            'videoSrc': 'https://cdn.example.test/show/index.m3u8?sig=fresh',
            'cdnM3u8Enabled': True, 'cdnOtherEnabled': False,
            'cdnAuthKey': 'x-cdn-auth', 'cdnAuthValue': 'fixture-auth', 'cdnApp': '150',
            **changes}


def detail(data=None, modified='2026-11-03T01:02:03+08:00'):
    data = payload() if data is None else data
    return BeautifulSoup(
        '<script id="videoInitialData" type="application/json">' + json.dumps(data) +
        '</script><script type="application/ld+json">' +
        json.dumps({'@graph': [{'@type': 'WebPage', 'dateModified': modified,
                               'datePublished': '2020-01-01T01:01:01+08:00'}]}) +
        '</script><div data-web-play><div class="hg-web-play__ep-grid">'
        '<a class="hg-web-play__ep" data-ep-id="1" href="/video/42/">1</a>'
        '<span class="hg-web-play__ep" data-ep-id="2">Unavailable</span>'
        '<a class="hg-web-play__ep" data-ep-id="3" href="/video/42/ep-3/">3</a>'
        '<a class="hg-web-play__ep" data-ep-id="3" href="/video/42/ep-3/">Duplicate</a>'
        '<a class="hg-web-play__ep" data-ep-id="4" href="/video/42/ep-9/">Wrong</a>'
        '<a class="hg-web-play__ep" data-ep-id="1" href="/video/999/">Recommendation</a>'
        '</div></div>', 'html.parser')


def test_numbered_episodes_are_actual_links_not_preload_keys(monkeypatch):
    monkeypatch.setattr(hg, '_page_soup', lambda url: detail(payload(
        epPlaySrcs={'1': 'unused', '2': 'not-playable', '8': 'not-a-page'})))
    items = HuangguoBrowser.fetch_episodes('https://huangguoai.com/video/42/')
    assert [i['episode'] for i in items] == [1, 3]
    assert [i['url'] for i in items] == [
        'https://huangguoai.com/video/42/', 'https://huangguoai.com/video/42/ep-3/']
    assert all(i['date'] == '2026-11-03' and i['_date_kind'] == 'series_updated'
               for i in items)
    assert all(i['thumbnail'] == '' for i in items)


def test_unknown_episode_date_never_falls_back_to_series_creation(monkeypatch):
    monkeypatch.setattr(hg, '_page_soup', lambda url: detail(modified=''))
    items = HuangguoBrowser.fetch_episodes('https://huangguoai.com/video/42/')
    assert all(i['date'] == '' and i['_date_kind'] == 'unknown' for i in items)
    assert HuangguoBrowser.fetch_video_date('https://huangguoai.com/video/42/') == ''


@pytest.mark.parametrize('changes', [{'id': '43'}, {'ep': 2}, {'ep': True}, {'ep': 1.5}])
def test_page_payload_must_match_requested_episode(changes):
    with pytest.raises(ValueError, match='metadata'):
        hg._initial_data(detail(payload(**changes)), 'https://huangguoai.com/video/42/')


def test_inline_player_javascript_is_never_evaluated():
    soup = BeautifulSoup('<script id="videoInitialData" type="application/json">'
                         'window.attack()</script>', 'html.parser')
    with pytest.raises(ValueError, match='metadata'):
        hg._initial_data(soup, 'https://huangguoai.com/video/42/')


def test_listing_ignores_search_suggestions_and_nonactive_recommendations(monkeypatch):
    def card(id):
        return f'<div class="hg-drama-card"><a class="hg-drama-card__cover-link" href="/video/{id}/"></a><h3 class="hg-drama-card__title"><a>Sample</a></h3><span data-ep-at="1793629019"></span></div>'
    soup = BeautifulSoup('<div class="hg-search-suggest">' + card(99) +
                         '</div><main><section class="hg-list-page">'
                         '<div class="hg-card-grid" data-channel-panel="latest">' +
                         card(42) + card(42) + '</div>'
                         '<div class="hg-card-grid" data-channel-panel="hot">' +
                         card(98) + '</div></section></main>', 'html.parser')
    url = 'https://huangguoai.com/ai-duanju/'
    assert [v['url'] for v in hg._parse_videos(soup, url)] == [
        'https://huangguoai.com/video/42/']
    monkeypatch.setattr(hg, '_page_soup', lambda request: soup if request == url else detail())
    items = HuangguoBrowser.fetch_page(url)
    assert len(items) == 2
    assert all(i['_source_listing_url'] == url for i in items)
    assert [i['episode'] for i in items] == [1, 3]


def test_listing_episode_failure_is_not_silently_reported_as_complete(monkeypatch):
    monkeypatch.setattr(hg, '_page_soup', lambda url: BeautifulSoup('', 'html.parser'))
    monkeypatch.setattr(hg, '_parse_videos', lambda *_: [{'url': 'https://huangguoai.com/video/42/'}])
    with pytest.raises(ValueError):
        HuangguoBrowser.fetch_page('https://huangguoai.com/ai-duanju/')


def test_categories_and_listing_urls_are_conservative():
    assert len(HuangguoBrowser.fetch_categories()) == 2
    assert all('/ai-duanju/' in x['url'] or '/ai-manju/' in x['url']
               for x in HuangguoBrowser.fetch_categories())
    for path in ['/newest', '/ai-huanlian/', '/ai-mogai/', '/chigua/', '/video/42/']:
        assert not HuangguoBrowser.is_listing_url(hg.HUANGGUO_ROOT + path)
    assert HuangguoBrowser.fetch_page('https://evil.test/ai-duanju/') == []
    assert HuangguoBrowser.page_url('https://huangguoai.com/ai-duanju/8/', 2) == 'https://huangguoai.com/ai-duanju/2/'
    assert HuangguoBrowser.page_url('https://huangguoai.com/search/video/123/', 2) == 'https://huangguoai.com/search/video/123/2/'
    assert HuangguoBrowser.page_url('https://huangguoai.com/search/video/123/7/', 1) == 'https://huangguoai.com/search/video/123/'
    assert HuangguoBrowser.search_url('A B/中文') == 'https://huangguoai.com/search/video/A%20B%2F%E4%B8%AD%E6%96%87/'


def test_signed_source_resolves_fresh_and_filename_contains_episode_id(monkeypatch):
    calls = []
    def page(url):
        calls.append(url)
        return detail(payload(videoSrc=f'https://cdn.example.test/index.m3u8?sig={len(calls)}'))
    monkeypatch.setattr(hg, '_page_soup', page)
    first = SiteHuangguo('https://huangguoai.com/video/42/', silence=True)
    second = SiteHuangguo('https://huangguoai.com/video/42/', silence=True)
    assert first.is_url_vaildate() and second.is_url_vaildate()
    assert first._m3u8url != second._m3u8url
    assert first.target_name().startswith('huangguo-42-ep-1')
    assert first._imageUrl is None
    assert first._extra_headers == {}


@pytest.mark.parametrize('changes', [
    {'videoSrc': 'http://cdn.example.test/index.m3u8'},
    {'videoSrc': 'https://127.0.0.1/index.m3u8'},
    {'videoSrc': 'https://10.0.0.1/index.m3u8'},
    {'videoSrc': 'https://cdn.example.test/video.mp4'},
    {'videoApi': '/private/player'},
    {'cdnAuthKey': 'Authorization'},
    {'cdnAuthValue': 'token\r\nCookie: secret'},
])
def test_invalid_or_private_playback_metadata_is_fail_closed(monkeypatch, changes, capsys):
    monkeypatch.setattr(hg, '_page_soup', lambda url: detail(payload(**changes)))
    crawler = SiteHuangguo('https://huangguoai.com/video/42/', silence=True)
    assert not crawler.is_url_vaildate()
    assert 'fixture-auth' not in capsys.readouterr().out


def make_crawler(monkeypatch, **changes):
    monkeypatch.setattr(hg, '_page_soup', lambda url: detail(payload(**changes)))
    return SiteHuangguo('https://huangguoai.com/video/42/', silence=True)


def test_cdn_headers_are_origin_and_resource_scoped(monkeypatch):
    crawler = make_crawler(monkeypatch)
    assert crawler._media_headers('https://cdn.example.test/index.m3u8')['x-cdn-auth'] == 'fixture-auth'
    assert 'x-cdn-auth' not in crawler._media_headers('https://other.example.test/index.m3u8')
    for suffix in ['ts', 'key', 'm4s', 'jpg']:
        assert 'x-cdn-auth' not in crawler._media_headers('https://cdn.example.test/a.' + suffix)
    crawler = make_crawler(monkeypatch, cdnOtherEnabled=True)
    for suffix in ['ts', 'key', 'm4s']:
        assert 'x-cdn-auth' in crawler._media_headers('https://cdn.example.test/a.' + suffix)
    assert 'x-cdn-auth' not in crawler._media_headers('https://cdn.example.test/a.jpg')


class Response:
    def __init__(self, status=200, location='', body=b'#EXTM3U\n'):
        self.status_code = status
        self.headers = {'Location': location} if location else {}
        self.content = body
        self.text = body.decode('utf-8')
        self.closed = False

    def close(self):
        self.closed = True

    def iter_content(self, size):
        yield self.content


def test_redirect_headers_are_recomputed_and_private_redirect_never_requested(monkeypatch):
    crawler = make_crawler(monkeypatch)
    calls = []
    responses = [Response(302, 'https://other.example.test/new.m3u8'), Response()]
    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)
    monkeypatch.setattr(hg, '_session', lambda kind: Session())
    monkeypatch.setattr(hg, '_verify_public_destination', lambda url: None)
    crawler._media_get(crawler._m3u8url)
    assert calls[0][1]['allow_redirects'] is False
    assert calls[0][1]['headers']['x-cdn-auth'] == 'fixture-auth'
    assert 'x-cdn-auth' not in calls[1][1]['headers']
    responses.extend([Response(302, 'https://127.0.0.1/private')])
    def public(url):
        if not hg._public_https_url(url):
            raise ValueError('non-public')
    monkeypatch.setattr(hg, '_verify_public_destination', public)
    with pytest.raises(ValueError, match='non-public'):
        crawler._media_get(crawler._m3u8url)
    assert len(calls) == 3


def test_page_redirect_cannot_leave_site_even_to_public_host(monkeypatch):
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            return Response(302, 'https://other.example.test/video/42/')
    monkeypatch.setattr(hg, '_session', lambda kind: Session())
    monkeypatch.setattr(hg, '_verify_public_destination', lambda url: None)
    with pytest.raises(ValueError, match='redirect'):
        hg._get('https://huangguoai.com/video/42/', page=True)
    assert len(calls) == 1


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.4', '169.254.169.254', '::1', 'fc00::1'])
def test_dns_resolution_to_private_destination_is_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    with pytest.raises(ValueError, match='verified'):
        hg._verify_public_destination('https://cdn.example.test/index.m3u8')


def test_bad_manifest_error_does_not_disclose_signed_url(monkeypatch):
    crawler = make_crawler(monkeypatch)
    monkeypatch.setattr(crawler, '_media_get', lambda *_args, **_kwargs: Response(body=b'not a manifest'))
    with pytest.raises(ValueError) as caught:
        crawler._load_m3u8(crawler._m3u8url)
    assert 'sig=' not in str(caught.value)
    assert 'fixture-auth' not in str(caught.value)


def test_unknown_listing_is_error_but_recognized_empty_listing_is_empty():
    with pytest.raises(ValueError, match='unrecognized'):
        hg._parse_videos(BeautifulSoup('<main>Changed layout</main>', 'html.parser'),
                         'https://huangguoai.com/ai-duanju/')
    assert hg._parse_videos(BeautifulSoup('<main><section class="hg-list-page">'
                                         '</section></main>', 'html.parser'),
                            'https://huangguoai.com/ai-duanju/') == []


def test_episode_expansion_limits_fail_explicitly(monkeypatch):
    monkeypatch.setattr(hg, '_page_soup', lambda url: detail())
    monkeypatch.setattr(hg, '_MAX_EPISODES_PER_SERIES', 1)
    with pytest.raises(ValueError, match='safety limit'):
        HuangguoBrowser.fetch_episodes('https://huangguoai.com/video/42/')
    monkeypatch.setattr(hg, '_MAX_EPISODES_PER_SERIES', 1000)
    monkeypatch.setattr(hg, '_MAX_EPISODES_PER_PAGE', 1)
    monkeypatch.setattr(hg, '_parse_videos', lambda *_: [
        {'url': 'https://huangguoai.com/video/42/', 'date': '', '_date_kind': 'unknown'}])
    with pytest.raises(ValueError, match='safety limit'):
        HuangguoBrowser.fetch_page('https://huangguoai.com/ai-duanju/')


def test_series_limit_is_explicit_not_partial(monkeypatch):
    monkeypatch.setattr(hg, '_MAX_SERIES_PER_PAGE', 1)
    soup = BeautifulSoup('<main><section class="hg-list-page"><div class="hg-card-grid">'
                         '<div class="hg-drama-card"><a class="hg-drama-card__cover-link" href="/video/42/"></a></div>'
                         '<div class="hg-drama-card"><a class="hg-drama-card__cover-link" href="/video/43/"></a></div>'
                         '</div></section></main>', 'html.parser')
    with pytest.raises(ValueError, match='safety limit'):
        hg._parse_videos(soup, 'https://huangguoai.com/ai-duanju/')


def test_html_metadata_download_is_size_bounded_and_closed(monkeypatch):
    response = Response(body=b'x' * (4 * 1024 * 1024 + 1))
    monkeypatch.setattr(hg, '_get', lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError, match='metadata limit'):
        hg._page_soup('https://huangguoai.com/video/42/')
    assert response.closed


@pytest.mark.parametrize('value', [True, False, 0, '0', -1])
def test_placeholder_dates_are_unknown_not_epoch(value):
    assert hg._date(value) == ''


def test_unrelated_site_date_is_not_used_for_episode_fallback():
    soup = BeautifulSoup('<script type="application/ld+json">'
                         '{"@type":"WebSite","dateModified":"2026-11-03"}'
                         '</script>', 'html.parser')
    assert hg._detail_date(soup, payload()) == ''


def test_fragmented_mp4_manifest_fails_early_not_as_repeated_bad_ts(monkeypatch):
    crawler = make_crawler(monkeypatch)
    body = (b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n'
            b'#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\nsegment.m4s\n#EXT-X-ENDLIST\n')
    monkeypatch.setattr(crawler, '_media_get', lambda *_args, **_kwargs: Response(body=body))
    with pytest.raises(ValueError, match='fragmented MP4'):
        crawler._load_m3u8(crawler._m3u8url)


def test_expansion_time_budget_never_returns_partial_success(monkeypatch):
    monkeypatch.setattr(hg, '_page_soup', lambda url: detail())
    monkeypatch.setattr(hg, '_parse_videos', lambda *_: [
        {'url': 'https://huangguoai.com/video/42/', 'date': '', '_date_kind': 'unknown'}])
    ticks = iter([0, 121])
    monkeypatch.setattr(hg.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(ValueError, match='timed out'):
        HuangguoBrowser.fetch_page('https://huangguoai.com/ai-duanju/')
