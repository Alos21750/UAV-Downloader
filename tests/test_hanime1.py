import sys
import types

import pytest
from requests.exceptions import Timeout


def _stub_runtime_dependency(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
    mod.loads = lambda *args, **kwargs: None
    return mod


_stub_runtime_dependency('m3u8', _m3u8_stub)

from bs4 import BeautifulSoup

from uav_downloader import sites as M3U8Sites
from uav_downloader.sites import hanime1 as hanime_mod
from uav_downloader.sites.hanime1 import (
    HANIME1_FILTER_DATES,
    HANIME1_FILTER_DURATIONS,
    HANIME1_FILTER_TAGS,
    Hanime1Browser,
    SiteHanime1,
    _extract_published_date,
    _extract_sources,
    _parse_filter_catalog,
    _parse_videos,
    _select_source,
)
from uav_downloader.core.video_identity import site_from_url, trusted_chinese_subtitle_evidence


WATCH_HTML = '''
<html>
  <head>
    <meta property="og:title" content="Fallback title - Hanime1.me">
    <meta property="og:image" content="https://vdownload.hembed.com/image/thumbnail/42l.jpg?secure=thumb">
  </head>
  <body>
    <h3 class="video-details-wrapper">A &amp; B</h3>
    <video poster="https://vdownload.hembed.com/image/thumbnail/42l.jpg?secure=poster">
      <source src="https://vdownload.hembed.com/video/42-480p.mp4?secure=low">
      <source src="https://vdownload.hembed.com/video/42-1080p.mp4?secure=high">
      <source src="https://vdownload.hembed.com/video/42-720p.mp4?secure=mid">
      <source src="javascript:alert(1)">
    </video>
  </body>
</html>
'''


def test_hanime1_validate_url_is_strict_and_anchored():
    assert SiteHanime1.validate_url('https://hanime1.me/watch?v=407751') == '407751'
    assert SiteHanime1.validate_url('https://www.hanime1.me/watch?v=42') == '42'
    assert SiteHanime1.validate_url('http://hanime1.me/watch?v=42') is None
    assert SiteHanime1.validate_url('https://hanime1.me/watch?v=42&next=1') is None
    assert SiteHanime1.validate_url('https://hanime1.me/watch?v=42#x') is None
    assert SiteHanime1.validate_url('https://hanime1.me/watch/v=42') is None
    assert SiteHanime1.validate_url('https://evil.test/watch?v=42') is None


def test_hanime1_extracts_https_mp4_sources_and_honours_resolution():
    sources = _extract_sources(BeautifulSoup(WATCH_HTML, 'html.parser'))
    assert [(item['height'], item['url']) for item in sources] == [
        (480, 'https://vdownload.hembed.com/video/42-480p.mp4?secure=low'),
        (1080, 'https://vdownload.hembed.com/video/42-1080p.mp4?secure=high'),
        (720, 'https://vdownload.hembed.com/video/42-720p.mp4?secure=mid'),
    ]
    assert _select_source(sources, 'highest')['height'] == 1080
    assert _select_source(sources, 'lowest')['height'] == 480
    assert _select_source(sources, '720')['height'] == 720
    assert _select_source(sources, '480')['height'] == 480
    assert _select_source(sources, '360')['height'] == 480


def test_hanime1_listing_parser_skips_ads_and_keeps_card_metadata():
    soup = BeautifulSoup('''
    <div class="video-item-container">
      <a class="video-link" href="https://ads.example/campaign">
        <div class="title">Sponsored</div>
      </a>
    </div>
    <div class="video-item-container">
      <div class="horizontal-card">
        <a class="video-link" href="https://hanime1.me/watch?v=407742">
          <div class="thumb-container">
            <img class="main-thumb" src="https://vdownload.hembed.com/image/thumbnail/407742l.jpg?secure=x">
            <div class="duration">04:09</div>
          </div>
          <div class="title">Alice &amp; Bob</div>
        </a>
        <div class="subtitle">
          <a href="https://hanime1.me/search?query=Studio">Studio</a>
          <span class="subtitle-time">&nbsp;• 16小時前</span>
        </div>
      </div>
    </div>
    <div class="video-item-container">
      <a class="video-link" href="https://hanime1.me/watch?v=407742">
        <div class="title">Duplicate</div>
      </a>
    </div>
    ''', 'html.parser')
    videos = _parse_videos(soup, 'https://hanime1.me/search?query=Alice')
    assert videos == [{
        'url': 'https://hanime1.me/watch?v=407742',
        'title': 'Alice & Bob',
        'thumbnail': 'https://vdownload.hembed.com/image/thumbnail/407742l.jpg?secure=x',
        'duration': '04:09',
        'date': '16小時前',
        'author': 'Studio',
        '_source_listing_url': 'https://hanime1.me/search?query=Alice',
    }]


def test_hanime1_genre_grid_parser_supports_ura_and_short_anime_layout():
    soup = BeautifulSoup('''
    <div class="home-rows-videos-wrapper">
      <a href="https://ads.example/campaign">
        <div class="home-rows-videos-div search-videos">
          <div class="video-card-inner"><div class="home-rows-videos-title">Ad</div></div>
        </div>
      </a>
      <a href="https://hanime1.me/watch?v=407591">
        <div class="home-rows-videos-div search-videos">
          <div class="video-card-inner">
            <img src="https://vdownload.hembed.com/image/cover/407591.jpg?secure=x">
            <div class="home-rows-videos-title">少女彈珠汽水 7</div>
          </div>
        </div>
      </a>
      <a href="/watch?v=407591">
        <div class="home-rows-videos-div search-videos">
          <div class="video-card-inner"><div class="home-rows-videos-title">Duplicate</div></div>
        </div>
      </a>
    </div>
    ''', 'html.parser')

    assert _parse_videos(
        soup, 'https://hanime1.me/search?genre=%E8%A3%8F%E7%95%AA') == [{
            'url': 'https://hanime1.me/watch?v=407591',
            'title': '少女彈珠汽水 7',
            'thumbnail': (
                'https://vdownload.hembed.com/image/cover/407591.jpg?secure=x'),
            'duration': '',
            'date': '',
            'author': '',
            '_source_listing_url': (
                'https://hanime1.me/search?genre=%E8%A3%8F%E7%95%AA'),
        }]


def test_hanime1_filter_catalog_and_combined_url_cover_live_filter_shapes():
    soup = BeautifulSoup('''
    <div id="genre-modal">
      <div class="genre-option" data-value="全部">全部</div>
      <div class="genre-option" data-value="裏番">裏番</div>
      <div class="genre-option" data-value="泡麵番">泡麵番</div>
    </div>
    <div id="sort-modal">
      <div class="hentai-sort-options-wrapper" data-value="最新上傳">最新上傳</div>
    </div>
    <div id="date-modal">
      <div class="hentai-date-options-wrapper" data-value="">全部</div>
      <div class="hentai-date-options-wrapper" data-value="過去 24 小時">過去 24 小時</div>
    </div>
    <div id="duration-modal">
      <div class="hentai-duration-options-wrapper" data-value="0 - 10 分鐘">0 - 10 分鐘</div>
    </div>
    <input type="checkbox" name="tags[]" value="中文字幕">
    <input type="checkbox" name="tags[]" value="1080p">
    ''', 'html.parser')

    assert _parse_filter_catalog(soup) == {
        'genres': ('裏番', '泡麵番'),
        'sorts': ('最新上傳',),
        'dates': ('過去 24 小時',),
        'durations': ('0 - 10 分鐘',),
        'tags': ('中文字幕', '1080p'),
    }
    assert Hanime1Browser.filter_url(
        query='作者 A', genre='泡麵番', sort='最新上傳',
        date='過去 24 小時', duration='0 - 10 分鐘',
        tags=('中文字幕', '1080p', '中文字幕'),
    ) == (
        'https://hanime1.me/search?query=%E4%BD%9C%E8%80%85+A&genre='
        '%E6%B3%A1%E9%BA%B5%E7%95%AA&sort=%E6%9C%80%E6%96%B0%E4%B8%8A%E5%82%B3'
        '&date=%E9%81%8E%E5%8E%BB+24+%E5%B0%8F%E6%99%82&duration=0+-+10+'
        '%E5%88%86%E9%90%98&tags%5B%5D=%E4%B8%AD%E6%96%87%E5%AD%97%E5%B9%95'
        '&tags%5B%5D=1080p')


def test_hanime1_filter_snapshot_is_complete_and_detail_date_is_extractable():
    assert len(HANIME1_FILTER_TAGS) >= 240
    assert {'無碼', '同人作品', 'ASMR', 'NTR', 'BDSM', 'POV'} <= set(
        HANIME1_FILTER_TAGS)
    assert HANIME1_FILTER_DATES == (
        '過去 24 小時', '過去 2 天', '過去 1 週',
        '過去 1 個月', '過去 3 個月', '過去 1 年')
    assert HANIME1_FILTER_DURATIONS[-2:] == ('0 - 10 分鐘', '0 - 20 分鐘')

    soup = BeautifulSoup('''
      <div class="video-details-wrapper hidden-sm">
        觀看次數：184.4萬次&nbsp;&nbsp;2026-08-08
      </div>
    ''', 'html.parser')
    assert _extract_published_date(soup) == '2026-08-08'


def test_hanime1_page_and_search_urls_preserve_filters_and_encode_values():
    base = 'https://hanime1.me/search?genre=Motion+Anime&sort=%E6%9C%80%E6%96%B0%E4%B8%8A%E5%82%B3'
    assert Hanime1Browser.page_url(base, 1) == base
    assert Hanime1Browser.page_url(base, 2) == base + '&page=2'
    assert Hanime1Browser.page_url(base + '&page=9', 3) == base + '&page=3'
    assert Hanime1Browser.search_url('A B&中文') == (
        'https://hanime1.me/search?query=A+B%26%E4%B8%AD%E6%96%87')


def test_hanime1_get_url_infos_uses_per_job_resolution(monkeypatch):
    calls = []

    class Response:
        def __init__(self, url, text, status_code=200):
            self.url = url
            self.text = text
            self.content = text.encode('utf-8')
            self.status_code = status_code
            self.headers = {}

    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if url == 'https://hanime1.me/':
                return Response(url, '<html>home</html>')
            assert url == 'https://hanime1.me/watch?v=42'
            return Response(url, WATCH_HTML)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(hanime_mod, '_make_scraper', lambda: Session())
    monkeypatch.setattr(
        hanime_mod.config, 'get_resolution_pref', lambda: 'highest')

    crawler = SiteHanime1(
        'https://hanime1.me/watch?v=42', silence=True, resolution='720')

    assert calls == ['https://hanime1.me/', 'https://hanime1.me/watch?v=42']
    assert crawler.is_url_vaildate()
    assert crawler.target_name() == 'A & B'
    assert crawler._direct_url.endswith('42-720p.mp4?secure=mid')
    assert crawler._direct_referer == 'https://hanime1.me/watch?v=42'
    assert crawler._imageUrl.endswith('42l.jpg?secure=thumb')

    second = SiteHanime1(
        'https://hanime1.me/watch?v=42', silence=True, resolution='480')
    assert second._direct_url.endswith('42-480p.mp4?secure=low')


def test_hanime1_is_registered_for_cli_and_gui_site_creation(monkeypatch):
    monkeypatch.setattr(SiteHanime1, 'get_url_infos', lambda self: (
        setattr(self, '_targetName', 'Hanime video'),
        setattr(self, '_direct_url', 'https://vdownload.hembed.com/video/42-720p.mp4'),
        setattr(self, '_direct_referer', self._url),
        setattr(self, '_m3u8url', None),
    ))
    assert M3U8Sites.VaildateUrl('https://hanime1.me/watch?v=42') is SiteHanime1
    created = M3U8Sites.CreateSite('https://hanime1.me/watch?v=42', silence=True)
    assert isinstance(created, SiteHanime1)
    assert created.is_url_vaildate()


@pytest.mark.parametrize('result', [
    'title', 'missing', 'blocked', 'timeout', 'cffi-timeout', 'http-error',
    'wrong-id', 'wrong-host', 'wrong-path', 'insecure', 'duplicate-id',
    'malformed-url', 'blank-duplicate-id', 'credentials', 'wrong-port',
])
def test_hanime1_missing_title_preserves_watch_source(tmp_path, monkeypatch, result):
    from contextlib import nullcontext

    watch_url = 'https://hanime1.me/watch?v=1243'
    download_url = 'https://hanime1.me/download?v=1243'
    watch_html = '''
        <meta property="og:title" content=" - Hanime1.me">
        <title>-&nbsp;H動漫/裏番/線上看&nbsp;-&nbsp;Hanime1.me</title>
        <h3 class="video-details-wrapper"></h3>
        <video poster="https://cdn.example.test/watch.jpg">
          <source src="https://cdn.example.test/1243-1080p.mp4">
        </video>
    '''
    download_html = '''
        <meta property="og:title" content="下載 [Studio] 完整標題 - Hanime1.me">
        <video><source src="https://cdn.example.test/1243-480p.mp4"></video>
    '''
    redirects = {
        'wrong-id': download_url.replace('1243', '999'),
        'wrong-host': 'https://other.example.test/download?v=1243',
        'wrong-path': 'https://hanime1.me/watch?v=1243',
        'insecure': 'http://hanime1.me/download?v=1243',
        'duplicate-id': download_url + '&v=999',
        'malformed-url': 'https://[invalid/download?v=1243',
        'blank-duplicate-id': download_url + '&v=',
        'credentials': 'https://user:password@hanime1.me/download?v=1243',
        'wrong-port': 'https://hanime1.me:8443/download?v=1243',
    }
    calls = []

    def request(_session, url):
        calls.append(url)
        if url == hanime_mod.HANIME1_HOME:
            return types.SimpleNamespace(status_code=200)
        if url == watch_url:
            return types.SimpleNamespace(status_code=200, content=watch_html.encode())
        assert url == download_url
        if result == 'blocked':
            raise hanime_mod.MirrorsBlockedError('controlled block')
        if result == 'timeout':
            raise Timeout('controlled timeout')
        if result == 'cffi-timeout':
            if not hanime_mod._use_cffi:
                pytest.skip('curl_cffi is optional')
            raise hanime_mod.cffi_requests.RequestsError('controlled timeout')
        content = '<title>下載 - Hanime1.me</title>' if result == 'missing' else download_html
        return types.SimpleNamespace(
            status_code=500 if result == 'http-error' else 200,
            content=content.encode(), url=redirects.get(result, download_url))

    monkeypatch.setattr(hanime_mod, '_make_scraper', nullcontext)
    monkeypatch.setattr(hanime_mod, '_request', request)
    monkeypatch.setattr(hanime_mod.config, 'get_filename_mode', lambda: 'full-title')
    crawler = SiteHanime1(watch_url, str(tmp_path), silence=True, filename_template='{title}')

    expected = '[Studio] 完整標題' if result == 'title' else 'Hanime1-1243'
    assert calls == [hanime_mod.HANIME1_HOME, watch_url, download_url]
    assert crawler.is_url_vaildate() and crawler.target_name() == expected
    assert crawler._get_video_savename() == str(tmp_path / (expected + '.mp4'))
    assert crawler._direct_url == 'https://cdn.example.test/1243-1080p.mp4'
    assert crawler._direct_referer == watch_url
    assert crawler._imageUrl == 'https://cdn.example.test/watch.jpg'


@pytest.mark.parametrize(('markup', 'download_page', 'expected'), [
    ('<title>Original title - Hanime1.me</title>', False, 'Original title'),
    ('<meta name="twitter:title" content="Twitter title - Hanime1.me">', False, 'Twitter title'),
    ('<meta property="og:title" content="Hanime1.me"><title>Real title - Hanime1.me</title>', False, 'Real title'),
    ('<title>下載 A &amp; B - Hanime1.me</title>', True, 'A & B'),
    ('<title>Download A - Hanime1.me</title>', True, 'A'),
    ('<title>下载 A - Hanime1.me</title>', True, 'A'),
    ('<title>下載 - Hanime1.me</title>', True, ''),
    ('<title>- H動漫/裏番/線上看 - Hanime1.me</title>', False, ''),
    ('<h3 class="video-details-wrapper">Download story</h3>', False, 'Download story'),
])
def test_hanime1_title_candidates(markup, download_page, expected):
    assert hanime_mod._extract_title(
        BeautifulSoup(markup, 'html.parser'), download_page=download_page) == expected


@pytest.mark.parametrize('available', [True, False])
def test_hanime1_download_page_source_fallback(monkeypatch, available):
    from contextlib import nullcontext

    watch_url = 'https://hanime1.me/watch?v=42'

    def request(_session, url):
        if url == hanime_mod.HANIME1_HOME:
            return types.SimpleNamespace(status_code=200)
        if url == watch_url:
            markup = '<h3 class="video-details-wrapper">Original title</h3>'
        else:
            assert url == 'https://hanime1.me/download?v=42'
            markup = '<title>下載 Other title - Hanime1.me</title>'
            if available:
                markup += '<source src="https://cdn.example.test/42-720p.mp4">'
        return types.SimpleNamespace(status_code=200, content=markup.encode(), url=url)

    monkeypatch.setattr(hanime_mod, '_make_scraper', nullcontext)
    monkeypatch.setattr(hanime_mod, '_request', request)
    crawler = SiteHanime1(watch_url, silence=True, filename_template='{title}')
    assert crawler.is_url_vaildate() is available
    if available:
        assert crawler.target_name() == 'Original title'
        assert crawler._direct_url == 'https://cdn.example.test/42-720p.mp4'
    else:
        assert 'MP4' in str(crawler._last_error)


def test_hanime1_metadata_request_propagates_unexpected_errors(monkeypatch):
    from contextlib import nullcontext

    class Interrupted(Exception):
        pass

    def request(_session, url):
        if url == hanime_mod.HANIME1_HOME:
            return types.SimpleNamespace(status_code=200)
        if '/watch?' in url:
            return types.SimpleNamespace(
                status_code=200,
                content=b'<source src="https://cdn.example.test/42.mp4">')
        raise Interrupted('must propagate, not become a metadata fallback')

    monkeypatch.setattr(hanime_mod, '_make_scraper', nullcontext)
    monkeypatch.setattr(hanime_mod, '_request', request)
    crawler = SiteHanime1.__new__(SiteHanime1)
    crawler._url = 'https://hanime1.me/watch?v=42'
    with pytest.raises(Interrupted):
        crawler.get_url_infos()


def test_hanime1_is_exposed_by_every_desktop_browser_and_listing_detection():
    from uav_downloader.legacy.browser import BrowsePanel
    from uav_downloader.apps.browse import ModernApp, SITES as MODERN_SITES
    from uav_downloader.apps.watch_categories import SITES as SMALLTOOL_SITES

    assert MODERN_SITES['Hanime1']['browser'] is Hanime1Browser
    assert SMALLTOOL_SITES['Hanime1']['browser'] is Hanime1Browser
    assert BrowsePanel.SITES['Hanime1']['browser'] is Hanime1Browser
    assert ModernApp._is_listing_url(
        None, 'https://hanime1.me/watch?v=407751') is False
    assert ModernApp._is_listing_url(
        None, 'https://hanime1.me/search?genre=3DCG') is True


def test_hanime1_identity_and_chinese_subtitle_listing_evidence_are_fail_closed():
    video_url = 'https://hanime1.me/watch?v=407751'
    trusted = {
        'url': video_url,
        '_site': 'Hanime1',
        '_source_listing_url': (
            'https://hanime1.me/search?tags%5B%5D=%E4%B8%AD%E6%96%87%E5%AD%97%E5%B9%95'),
    }
    assert site_from_url(video_url) == 'Hanime1'
    assert trusted_chinese_subtitle_evidence(trusted) == (
        'hanime1-tag-chinese-subtitle',)
    assert trusted_chinese_subtitle_evidence({
        **trusted,
        '_source_listing_url': (
            'https://attacker.invalid/search?tags%5B%5D='
            '%E4%B8%AD%E6%96%87%E5%AD%97%E5%B9%95'),
    }) == ()


def test_hanime1_all_browse_categories_have_four_language_labels():
    from uav_downloader.i18n import sites as site_i18n

    assert len(Hanime1Browser.CATEGORIES) == 24
    for _name, url in Hanime1Browser.CATEGORIES:
        labels = site_i18n.CATEGORY_I18N[url]
        assert set(labels) == {'en', 'zh', 'zh-Hans', 'ja'}
        assert all(labels.values())
