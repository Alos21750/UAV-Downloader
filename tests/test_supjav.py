from urllib.parse import urljoin
import sys
import threading
import types

import pytest


def _stub_runtime_dependency(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


def _cloudscraper_stub():
    mod = types.ModuleType('cloudscraper')

    def create_scraper(*args, **kwargs):
        raise AssertionError('cloudscraper should not be used by offline tests')

    mod.create_scraper = create_scraper
    return mod


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
    mod.loads = lambda *a, **k: types.SimpleNamespace(playlists=[], segments=[], keys=[])
    return mod


def _customtkinter_stub():
    mod = types.ModuleType('customtkinter')

    class CTk:
        pass

    mod.CTk = CTk
    mod.CTkLabel = CTk
    return mod


_stub_runtime_dependency('cloudscraper', _cloudscraper_stub)
_stub_runtime_dependency('m3u8', _m3u8_stub)
_stub_runtime_dependency('customtkinter', _customtkinter_stub)

from uav_downloader.sites import base as crawler_mod
from uav_downloader.sites import supjav as supjav_mod
from bs4 import BeautifulSoup
from uav_downloader.sites.base import M3U8Crawler
from uav_downloader.sites.supjav import (
    SiteSupJav,
    SupJavBrowser,
    _extract_m3u8,
    _extract_packed_m3u8,
    _extract_title,
    _extract_tv_link,
    _parse_videos,
    _server_links,
    _streamtape_direct_url,
    _strip_fake_header,
)


class _FakeResp:
    def __init__(self, status_code=200, headers=None, content=b'', text=None, url='https://example.test/master.m3u8'):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = text if text is not None else content.decode('utf-8', errors='ignore')
        self.url = url


def test_supjav_validate_url_is_anchored():
    assert SiteSupJav.validate_url('https://supjav.com/433866.html') == '433866'
    assert SiteSupJav.validate_url('https://supjav.com/zh/12345.html') == '12345'
    assert SiteSupJav.validate_url('https://supjav.com/ja/12345.html') == '12345'
    assert SiteSupJav.validate_url('https://supjav.com/433866.html/x') is None
    assert SiteSupJav.validate_url('https://jable.tv/videos/x/') is None


def test_supjav_video_urls_are_not_listing_urls():
    from uav_downloader.apps.browse import ModernApp

    assert ModernApp._is_listing_url(None, 'https://supjav.com/12345.html') is False
    assert ModernApp._is_listing_url(None, 'https://supjav.com/zh/12345.html') is False
    assert ModernApp._is_listing_url(None, 'https://supjav.com/ja/12345.html') is False
    assert ModernApp._is_listing_url(None, 'https://supjav.com/zh/popular') is True


def test_supjav_page_url():
    assert SupJavBrowser.page_url('https://supjav.com/category/uncensored-jav', 2) == 'https://supjav.com/category/uncensored-jav/page/2'
    assert SupJavBrowser.page_url('https://supjav.com/popular?sort=week', 2) == 'https://supjav.com/popular?sort=week&page=2'
    assert SupJavBrowser.page_url('https://supjav.com/', 2) == 'https://supjav.com/page/2'
    assert SupJavBrowser.page_url('https://supjav.com/?s=fc2', 2) == 'https://supjav.com/page/2/?s=fc2'
    base = 'https://supjav.com/category/uncensored-jav'
    assert SupJavBrowser.page_url(base, 1) == base


def test_extract_m3u8_from_urlplay():
    body = r"var urlPlay = 'https:\/\/cdn1.turboviplay.com\/data1\/abc\/abc.m3u8';"
    assert _extract_m3u8(body) == 'https://cdn1.turboviplay.com/data1/abc/abc.m3u8'


def test_extract_tv_link_selects_tv_button():
    html = '''
    <a data-link="tsf">FST</a>
    <a data-link="321cba">TV</a>
    <a data-link="ts">ST</a>
    <a data-link="eov">VOE</a>
    '''
    tv_link = _extract_tv_link(html)
    assert tv_link == '321cba'
    assert tv_link[::-1] == 'abc123'


def test_extract_title_from_h1_without_og_title():
    soup = BeautifulSoup('''
    <html>
      <head><title>FC2PPV 4916515 [Limited To 200 Copies...]</title></head>
      <body><h1>FC2PPV 4916515 [Limited To 200 Copies...]</h1></body>
    </html>
    ''', 'html.parser')
    assert _extract_title(soup) == 'FC2PPV 4916515 [Limited To 200 Copies...]'


def test_parse_videos_uses_real_thumbnail_src_and_ignores_base64_placeholder():
    soup = BeautifulSoup('''
    <div class="post">
      <a href="https://supjav.com/1.html" title="Home"></a>
      <img class="thumb" src="https://img.supjav.com/home.jpg">
      <div class="meta">2026/07/12 <span>100 Views</span></div>
    </div>
    <div class="post">
      <a href="https://supjav.com/2.html" title="Category"></a>
      <img class="thumb" data-original="https://img.supjav.com/category.jpg" src="data:image/png;base64,placeholder">
    </div>
    <div class="post">
      <a href="https://supjav.com/3.html" title="Placeholder"></a>
      <img class="thumb" src="data:image/png;base64,placeholder">
    </div>
    ''', 'html.parser')
    videos = _parse_videos(soup)
    assert videos[0]['thumbnail'] == 'https://img.supjav.com/home.jpg'
    assert videos[0]['date'] == '2026/07/12'
    assert videos[1]['thumbnail'] == 'https://img.supjav.com/category.jpg'
    assert videos[1]['date'] == ''
    assert videos[2]['thumbnail'] == ''


def test_strip_fake_header_removes_png_prefix_from_ts():
    wrapped = b'\x89PNG\r\n\x1a\n' + b'\x00' * 180 + (b'\x47' + b'\x11' * 187) * 6
    stripped = _strip_fake_header(wrapped)
    assert stripped[:1] == b'\x47'
    assert len(stripped) == 188 * 6


def test_strip_fake_header_leaves_plain_ts_unchanged():
    plain = b'\x47' + b'\x00' * 187 * 5
    assert _strip_fake_header(plain) == plain


def test_strip_fake_header_falls_back_when_no_valid_sync_run():
    data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 300
    assert _strip_fake_header(data) == b''


def test_strip_fake_header_does_not_raise_on_short_input():
    assert _strip_fake_header(b'\x89PNG') == b''


def test_ts_segment_validation_rejects_empty_short_and_non_ts():
    assert crawler_mod._is_valid_ts_segment(b'\x47' + b'\x00' * 187)
    assert not crawler_mod._is_valid_ts_segment(b'')
    assert not crawler_mod._is_valid_ts_segment(b'\x47' + b'\x00' * 186)
    assert not crawler_mod._is_valid_ts_segment(b'<html>' + b'\x00' * 183)


def test_getm3u8_playlist_handles_absolute_and_relative_variants(monkeypatch):
    assert urljoin(
        'https://cdn1.turboviplay.com/data1/x/x.m3u8',
        'https://hls4.turbosplayer.com/file/u/master.m3u8',
    ) == 'https://hls4.turbosplayer.com/file/u/master.m3u8'

    class DummyCrawler(M3U8Crawler):
        def __init__(self):
            self._m3u8url = 'https://cdn1.turboviplay.com/data1/x/x.m3u8'
            self._extra_headers = {}

    loaded = []

    def fake_http_get(url, headers=None, timeout=20):
        loaded.append(url)
        return _FakeResp(
            status_code=200,
            headers={},
            content=b'#EXTM3U\n',
            text='#EXTM3U\n',
            url=url,
        )

    monkeypatch.setattr(crawler_mod, '_http_get', fake_http_get)
    monkeypatch.setattr(crawler_mod.m3u8, 'loads', lambda *a, **k: object())

    dummy = DummyCrawler()
    _, variant_base = dummy._getm3u8PlayList('https://hls4.turbosplayer.com/file/u/master.m3u8')
    assert loaded[-1] == 'https://hls4.turbosplayer.com/file/u/master.m3u8'
    assert variant_base == 'https://hls4.turbosplayer.com/file/u/'

    _, variant_base = dummy._getm3u8PlayList('variant/master.m3u8')
    assert loaded[-1] == 'https://cdn1.turboviplay.com/data1/x/variant/master.m3u8'
    assert variant_base == 'https://cdn1.turboviplay.com/data1/x/variant/'


def test_is_cf_block_resp_detects_only_cloudflare_blocks():
    assert crawler_mod._is_cf_block_resp(_FakeResp(403, headers={'Server': 'cloudflare'}))
    assert crawler_mod._is_cf_block_resp(_FakeResp(403, content=b'... Attention Required ...'))
    assert not crawler_mod._is_cf_block_resp(_FakeResp(404))
    assert not crawler_mod._is_cf_block_resp(_FakeResp(200, headers={'Server': 'cloudflare'}))


def test_load_m3u8_raises_blocked_on_cloudflare_resp(monkeypatch):
    class DummyCrawler(M3U8Crawler):
        def __init__(self):
            self._extra_headers = {}

    monkeypatch.setattr(
        crawler_mod,
        '_http_get',
        lambda *a, **k: _FakeResp(403, headers={'Server': 'cloudflare'}, url=a[0]),
    )

    with pytest.raises(crawler_mod.MirrorsBlockedError):
        DummyCrawler()._load_m3u8('https://example.test/master.m3u8')


def test_load_m3u8_raises_generic_on_non_cf_404(monkeypatch):
    class DummyCrawler(M3U8Crawler):
        def __init__(self):
            self._extra_headers = {}

    monkeypatch.setattr(
        crawler_mod,
        '_http_get',
        lambda *a, **k: _FakeResp(404, url=a[0]),
    )

    with pytest.raises(Exception) as exc:
        DummyCrawler()._load_m3u8('https://example.test/master.m3u8')
    assert not isinstance(exc.value, crawler_mod.MirrorsBlockedError)


def test_load_m3u8_raises_generic_when_content_is_not_playlist(monkeypatch):
    class DummyCrawler(M3U8Crawler):
        def __init__(self):
            self._extra_headers = {}

    monkeypatch.setattr(
        crawler_mod,
        '_http_get',
        lambda *a, **k: _FakeResp(200, content=b'not a playlist', text='not a playlist', url=a[0]),
    )

    with pytest.raises(Exception) as exc:
        DummyCrawler()._load_m3u8('https://example.test/master.m3u8')
    assert not isinstance(exc.value, crawler_mod.MirrorsBlockedError)


def test_server_links_maps_all_btn_servers():
    html = (
        '<a href="javascript:;" class="btn-server active" data-link="AAA">TV</a>'
        '<a href="javascript:;" class="btn-server" data-link="BBB">FST</a>'
        '<a href="javascript:;" class="btn-server" data-link="CCC">ST</a>'
        '<a href="javascript:;" class="btn-server" data-link="DDD">VOE</a>'
    )
    assert _server_links(html) == {'TV': 'AAA', 'FST': 'BBB', 'ST': 'CCC', 'VOE': 'DDD'}
    assert _server_links('<div>no servers here</div>') == {}


def test_streamtape_direct_url_evaluates_js_substring_and_ignores_decoy():
    # Streamtape overwrites #robotlink via JS; the static div text carries a DECOY token,
    # the real token only appears in the JS-computed value.
    html = (
        '<div id="ideoolink" style="display:none;">/streamtape.com/get_video?id=ABC'
        '&expires=123&ip=YYY&token=DECOY_2cde</div>'
        '<div id="robotlink" style="display:none;">/streamtape.com/get_video?id=ABC'
        '&expires=123&ip=YYY&token=DECOY_2cde</div>'
        "<script>document.getElementById('robotlink').innerHTML = '//streamtape.com/get_'"
        "+ ('xcdvideo?id=ABC&expires=123&ip=YYY&token=REAL_2-E6').substring(2).substring(1);</script>"
    )
    url = _streamtape_direct_url(html)
    assert url == 'https://streamtape.com/get_video?id=ABC&expires=123&ip=YYY&token=REAL_2-E6'
    assert 'DECOY' not in url            # never return the static-div decoy token
    assert _streamtape_direct_url('<html>no robotlink</html>') is None


def test_extract_packed_m3u8_keeps_signed_query(monkeypatch):
    monkeypatch.setattr(
        supjav_mod,
        '_unpack_js_eval',
        lambda _script: "var source='https://cdn.example/video/master.m3u8?t=abc&s=1';",
    )
    html = "<script>eval(function(p,a,c,k,e,d){/* m3u8 */})</script>"
    assert _extract_packed_m3u8(html) == (
        'https://cdn.example/video/master.m3u8?t=abc&s=1')
    assert _extract_packed_m3u8('<script>plain</script>') is None


def test_is_url_vaildate_accepts_direct_url_source():
    # Regression: the base gate is `True if self._m3u8url`; a Streamtape source has no
    # m3u8, only _direct_url. Without the override the caller silently skips the URL.
    s = SiteSupJav.__new__(SiteSupJav)
    s._m3u8url = None
    s._direct_url = None
    assert s.is_url_vaildate() is False
    s._direct_url = 'https://streamtape.com/get_video?id=x&token=y'
    assert s.is_url_vaildate() is True
    s._direct_url = None
    s._m3u8url = 'https://cdn.turboviplay.com/data3/x/x.m3u8'
    assert s.is_url_vaildate() is True


def _source_scraper(monkeypatch, name, body, embed_url):
    page = _FakeResp(text=f'<h1>Test video</h1><a class="btn-server" data-link="abc">{name}</a>')

    class Scraper:
        calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _FakeResp(text=body, url=embed_url)

    scraper = Scraper()
    monkeypatch.setattr(supjav_mod, '_make_scraper', lambda: scraper)
    monkeypatch.setattr(supjav_mod, 'fetch_with_mirrors', lambda *a, **k: (page, 'supjav.com', 'ok'))
    return scraper


@pytest.mark.parametrize('name,embed_url', [
    ('EVS', 'https://evsishere.xyz/embed/video'),
    ('LUC', 'https://luminscdn.xyz/e/video'),
])
def test_new_packed_sources_work_without_legacy_servers(monkeypatch, name, embed_url):
    assert SiteSupJav.validate_url('https://supjav.com/462685.html') == '462685'
    _source_scraper(monkeypatch, name, '<script>eval(function /* m3u8 */)</script>', embed_url)
    monkeypatch.setattr(supjav_mod, '_unpack_js_eval', lambda _: 'sources:[{file:"https://cdn.example/master.m3u8?token=signed&x=2"}]')
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._url = 'https://supjav.com/462685.html'
    crawler.get_url_infos()
    assert crawler.is_url_vaildate()
    assert crawler._m3u8url == 'https://cdn.example/master.m3u8?token=signed&x=2'
    assert crawler._extra_headers == {'Referer': embed_url, 'Origin': 'https://' + embed_url.split('/')[2]}


def test_vas_uses_same_origin_stream_api_and_preserves_signed_url(monkeypatch):
    scraper = _source_scraper(monkeypatch, 'VAS', 'let apiURL="/api/stream";', 'https://vvaassjjav.xyz/e/abc123?lang=en#supjav')
    posted = []

    def post(url, **kwargs):
        posted.append((url, kwargs))
        return _FakeResp(text='{"streaming_url":"https://cdn.example/master.m3u8?token=x&expires=123"}')

    scraper.post = post
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._url = 'https://supjav.com/462685.html'
    crawler.get_url_infos()
    assert crawler._m3u8url.endswith('?token=x&expires=123')
    assert posted[0][0] == 'https://vvaassjjav.xyz/api/stream?lang=en'
    assert posted[0][1]['json'] == {'filecode': 'abc123', 'device': 'web', 'codecs': []}
    assert posted[0][1]['headers']['Origin'] == 'https://vvaassjjav.xyz'
    assert posted[0][1]['allow_redirects'] is False


def test_voe_config_decoder_ignores_decoy_video_url():
    import base64, codecs, json
    expected = 'https://cdn.example/hls/master.m3u8?token=abc&x=1'
    config = json.dumps({'source': expected})
    reversed_b64 = base64.b64encode(config.encode()).decode()[::-1]
    shifted = ''.join(chr(ord(c) + 3) for c in reversed_b64)
    encoded = codecs.encode(base64.b64encode(shifted.encode()).decode(), 'rot13')
    noisy = '@$'.join(encoded[i:i+4] for i in range(0, len(encoded), 4))
    body = '<script>var source="https://test-videos.co.uk/decoy.mp4";</script>'
    body += '<script type="application/json">' + json.dumps([noisy]) + '</script>'
    assert supjav_mod._extract_voe_m3u8(body) == expected
    assert supjav_mod._extract_voe_m3u8('<script type="application/json">["invalid"]</script>') is None


@pytest.mark.parametrize('url', [
    'file:///video/master.m3u8', 'http://cdn.example/master.m3u8',
    'https://user:secret@cdn.example/master.m3u8',
    'https://localhost/master.m3u8', 'https://sub.localhost/master.m3u8',
    'https://device.local/master.m3u8', 'https://nas.internal/master.m3u8',
    'https://127.0.0.1/master.m3u8', 'https://127.0.0.1./master.m3u8',
    'https://10.1.2.3/master.m3u8', 'https://192.168.0.1/master.m3u8',
    'https://169.254.169.254/master.m3u8', 'https://[::1]/master.m3u8',
    'https://[fc00::1]/master.m3u8', 'https://2130706433/master.m3u8',
    'https://127.1/master.m3u8', 'https://0x7f.0.0.1/master.m3u8',
    'https://ｌｏｃａｌｈｏｓｔ/master.m3u8',
])
def test_supjav_hls_rejects_credentials_and_local_destinations(url):
    assert supjav_mod._valid_hls_url(url) is None


def test_vas_rejects_private_embed_before_stream_api_post(monkeypatch):
    scraper = _source_scraper(monkeypatch, 'VAS', 'let apiURL="/api/stream";', 'https://127.0.0.1/e/abc123')
    scraper.post = lambda *a, **k: pytest.fail('must not POST to local API')
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._url = 'https://supjav.com/462685.html'
    with pytest.raises(Exception, match='無可用下載來源'):
        crawler.get_url_infos()


def test_voe_does_not_follow_private_literal_redirect(monkeypatch):
    body = "<script>window.location.href='https://127.0.0.1/e/video';</script>"
    scraper = _source_scraper(monkeypatch, 'VOE', body, 'https://voe.sx/e/video')
    crawler = SiteSupJav.__new__(SiteSupJav)
    assert crawler._resolve_server(scraper, 'VOE', 'abc')[0] is None
    assert len(scraper.calls) == 1


def test_supjav_http_redirect_rejects_local_location_before_fetch(monkeypatch):
    scraper = _source_scraper(monkeypatch, 'EVS', '', 'https://lk1.supremejav.com/supjav.php?c=cba')
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResp(status_code=302, headers={'Location':'https://127.0.0.1/e/video'}, url=url)

    scraper.get = get
    crawler = SiteSupJav.__new__(SiteSupJav)
    with pytest.raises(ValueError):
        crawler._resolve_server(scraper, 'EVS', 'abc')
    assert len(calls) == 1
    assert calls[0][1]['allow_redirects'] is False


def test_supjav_embed_http_redirect_loop_is_bounded(monkeypatch):
    scraper = _source_scraper(monkeypatch, 'EVS', '', 'https://embed.example/e/video')
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return _FakeResp(status_code=302, headers={'Location':'/e/video'}, url=url)

    scraper.get = get
    with pytest.raises(ValueError, match='Too many'):
        SiteSupJav.__new__(SiteSupJav)._resolve_server(scraper, 'EVS', 'abc')
    assert len(calls) == 6


def test_new_hls_sources_fallback_without_streamtape(monkeypatch):
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._m3u8url = 'https://evs.example/master.m3u8'
    crawler._extra_headers = {'Referer': 'https://evs.example/embed/video'}
    crawler._hls_sources = [
        ('EVS', crawler._m3u8url, crawler._extra_headers),
        ('VAS', 'https://vas.example/master.m3u8', {'Referer': 'https://vas.example/e/video'}),
    ]
    crawler._direct_url = None
    crawler._cancel_job = False
    attempts = []
    cleaned = []

    def download(self):
        attempts.append((self._m3u8url, self._extra_headers))
        if len(attempts) == 1:
            raise RuntimeError('dead CDN')
        return True

    monkeypatch.setattr(M3U8Crawler, 'start_download', download)
    crawler.cleanup_temp = lambda: cleaned.append(True)
    assert crawler.start_download() is True
    assert len(attempts) == 2
    assert attempts[1][1] == {'Referer': 'https://vas.example/e/video'}
    assert cleaned == [True]


def test_blocked_source_resolution_does_not_mask_new_fallback(monkeypatch):
    _source_scraper(monkeypatch, 'EVS', '', 'https://evs.example/embed/video')
    page = _FakeResp(text='<h1>Test</h1><a class="btn-server" data-link="a">EVS</a><a class="btn-server" data-link="b">VAS</a>')
    monkeypatch.setattr(supjav_mod, 'fetch_with_mirrors', lambda *a, **k: (page, 'supjav.com', 'ok'))
    attempted = []

    def resolve(self, scraper, name, link):
        attempted.append(name)
        if name == 'EVS':
            raise crawler_mod.MirrorsBlockedError('upstream blocked')
        return 'https://cdn.example/master.m3u8', {'Referer': 'https://vas.example/e/video'}

    monkeypatch.setattr(SiteSupJav, '_resolve_server', resolve)
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._url = 'https://supjav.com/462685.html'
    crawler.get_url_infos()
    assert attempted == ['EVS', 'VAS']
    assert crawler._hls_sources[0][0] == 'VAS'


def test_voe_literal_redirect_is_bounded_and_uses_new_embed_referer(monkeypatch):
    scraper = _source_scraper(monkeypatch, 'VOE', '', 'https://voe.sx/e/video')
    calls = []
    redirect_page = "<script>window.location.href='https://voe-alternate.example/e/video';</script>"

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return _FakeResp(text=redirect_page, url='https://voe.sx/e/video')
        return _FakeResp(text='encoded-player', url='https://voe-alternate.example/e/video')

    scraper.get = get
    monkeypatch.setattr(supjav_mod, '_extract_voe_m3u8', lambda body: 'https://cdn.example/master.m3u8' if body == 'encoded-player' else None)
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._url = 'https://supjav.com/462685.html'
    crawler.get_url_infos()
    assert len(calls) == 2
    assert calls[1][1]['headers']['Referer'] == 'https://voe.sx/e/video'
    assert crawler._extra_headers['Referer'] == 'https://voe-alternate.example/e/video'


def test_cancelled_hls_does_not_try_another_provider(monkeypatch):
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._m3u8url = 'https://cdn.example/master.m3u8'
    crawler._hls_sources = [('EVS', crawler._m3u8url, {}), ('VAS', 'https://other.example/master.m3u8', {})]
    crawler._cancel_job = True
    attempts = []
    monkeypatch.setattr(M3U8Crawler, 'start_download', lambda self: attempts.append(True) or False)
    assert crawler.start_download() is False
    assert attempts == [True]


def test_supjav_luc_encrypted_segment_decrypts_before_ts_transform(monkeypatch, tmp_path):
    import time
    from Crypto.Cipher import AES
    key = b'0123456789abcdef'
    plain = (b'\x47' + b'\x11' * 187) * 4
    encrypted = AES.new(key, AES.MODE_CBC, bytes(16)).encrypt(plain)
    assert encrypted[:1] != b'\x47'
    url = 'https://cdn.example/luc/segment.ts'
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._cancel_job = False
    crawler._storage_error = None
    crawler._temp_folder = str(tmp_path)
    crawler._extra_headers = {}
    crawler._key_content = key
    crawler._key_iv = None
    crawler._media_sequence = 0
    crawler._speed_lock = threading.Lock()
    crawler._pending_set = {(0, url)}
    crawler._bytes_downloaded = 0
    crawler._speed_start = time.time()
    crawler._job_total = 1
    crawler._progress_callback = None
    monkeypatch.setattr(crawler_mod, '_http_get', lambda *a, **k: _FakeResp(content=encrypted))
    monkeypatch.setattr(crawler_mod.speed_limiter, 'acquire', lambda _: None)
    assert crawler._scrape((0, url)) is True
    assert (tmp_path / '000000.mp4').read_bytes() == plain
    assert not crawler._pending_set


def test_supjav_source_fallback_stops_for_storage_failure(monkeypatch):
    from uav_downloader.core.storage import StorageWriteError
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._m3u8url = 'https://cdn.example/master.m3u8'
    crawler._extra_headers = {}
    crawler._hls_sources = [('EVS', crawler._m3u8url, {}), ('LUC', 'https://other.example/master.m3u8', {})]
    crawler._direct_url = 'https://streamtape.com/get_video?id=test'
    crawler._cancel_job = False
    attempts = []

    def fail(self):
        attempts.append(True)
        raise StorageWriteError('video.mp4', OSError(28, 'disk full'))

    monkeypatch.setattr(M3U8Crawler, 'start_download', fail)
    with pytest.raises(StorageWriteError):
        crawler.start_download()
    assert attempts == [True]


def test_direct_download_uses_parallel_ranges_and_assembles_file(monkeypatch, tmp_path):
    payload = bytes(range(256)) * 1024
    calls = []
    calls_lock = threading.Lock()

    class RangeResponse:
        def __init__(self, start, end):
            self.status_code = 206
            self.headers = {
                'content-range': f'bytes {start}-{end}/{len(payload)}',
                'content-length': str(end - start + 1),
            }
            self._data = payload[start:end + 1]

        def iter_content(self, chunk_size):
            for offset in range(0, len(self._data), chunk_size):
                yield self._data[offset:offset + chunk_size]

        def close(self):
            pass

    class RangeSession:
        def get(self, url, headers=None, **kwargs):
            range_header = (headers or {}).get('Range')
            with calls_lock:
                calls.append(range_header)
            assert range_header and range_header.startswith('bytes=')
            start, end = (int(value) for value in range_header[6:].split('-', 1))
            return RangeResponse(start, end)

    monkeypatch.setattr(supjav_mod, '_get_session', lambda: RangeSession(), raising=False)
    monkeypatch.setattr(
        supjav_mod,
        '_make_scraper',
        lambda: (_ for _ in ()).throw(AssertionError('range-capable downloads must not use the serial path')),
    )
    monkeypatch.setattr(supjav_mod.speed_limiter, 'acquire', lambda _size: None)

    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._cancel_job = False
    crawler._dest_folder = str(tmp_path)
    crawler._targetName = 'parallel'
    crawler._direct_url = 'https://streamtape.example/get_video?id=test'
    crawler._direct_referer = 'https://streamtape.example/e/test'
    crawler._progress_callback = None
    crawler._t2_executor = None
    crawler._max_workers = 2

    assert crawler._download_direct() is True
    assert (tmp_path / 'parallel.mp4').read_bytes() == payload
    assert not (tmp_path / 'parallel.mp4.part').exists()

    range_calls = [value for value in calls if value != 'bytes=0-0']
    assert len(range_calls) == 2


def test_direct_parallel_range_resumes_after_connection_reset(monkeypatch, tmp_path):
    payload = bytes(range(256)) * 1024
    calls = []
    calls_lock = threading.Lock()
    failed_once = False

    class RangeResponse:
        def __init__(self, start, end, fail=False):
            self.status_code = 206
            self.headers = {'content-range': f'bytes {start}-{end}/{len(payload)}'}
            self._data = payload[start:end + 1]
            self._fail = fail

        def iter_content(self, chunk_size):
            if self._fail:
                cut = max(1, len(self._data) // 2)
                yield self._data[:cut]
                raise ConnectionResetError('reset by peer')
            yield self._data

        def close(self):
            pass

    class RangeSession:
        def get(self, url, headers=None, **kwargs):
            nonlocal failed_once
            range_header = (headers or {}).get('Range')
            start, end = (int(value) for value in range_header[6:].split('-', 1))
            with calls_lock:
                calls.append(range_header)
                fail = start == 0 and end > 0 and not failed_once
                if fail:
                    failed_once = True
            return RangeResponse(start, end, fail=fail)

    monkeypatch.setattr(supjav_mod, '_get_session', lambda: RangeSession())
    monkeypatch.setattr(supjav_mod, '_DIRECT_RETRY_BASE_DELAY', 0)
    monkeypatch.setattr(supjav_mod.speed_limiter, 'acquire', lambda _size: None)

    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._cancel_job = False
    crawler._dest_folder = str(tmp_path)
    crawler._targetName = 'resumed'
    crawler._direct_url = 'https://streamtape.example/get_video?id=test'
    crawler._direct_referer = 'https://streamtape.example/e/test'
    crawler._progress_callback = None
    crawler._t2_executor = None

    assert crawler._download_direct() is True
    assert (tmp_path / 'resumed.mp4').read_bytes() == payload
    initial_starts = {start for start, _end in supjav_mod._split_byte_ranges(len(payload))}
    resumed_starts = {
        int(value[6:].split('-', 1)[0])
        for value in calls
        if value != 'bytes=0-0'
    } - initial_starts
    assert resumed_starts


def test_fst_hls_is_preferred_and_streamtape_is_fallback(monkeypatch):
    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._m3u8url = 'https://fst.example/master.m3u8'
    crawler._direct_url = 'https://streamtape.example/video.mp4'
    crawler._cancel_job = False
    crawler.cleanup_temp = lambda: None
    direct_calls = []
    crawler._download_direct = lambda: direct_calls.append(True) or True

    monkeypatch.setattr(crawler_mod.M3U8Crawler, 'start_download', lambda self: True)
    assert crawler.start_download() is True
    assert direct_calls == []

    def fail_hls(self):
        raise ConnectionError('FST unavailable')

    monkeypatch.setattr(crawler_mod.M3U8Crawler, 'start_download', fail_hls)
    assert crawler.start_download() is True
    assert direct_calls == [True]


def test_direct_download_keeps_serial_fallback_without_range_support(monkeypatch, tmp_path):
    payload = b'serial-fallback-data'

    class ProbeResponse:
        status_code = 200
        headers = {'content-length': str(len(payload))}

        def close(self):
            pass

    class ProbeSession:
        def get(self, url, headers=None, **kwargs):
            if (headers or {}).get('Range') == 'bytes=0-0':
                return ProbeResponse()
            assert 'Range' not in (headers or {})
            return SerialResponse()

    class SerialResponse:
        status_code = 200
        headers = {'content-length': str(len(payload))}

        def iter_content(self, chunk_size):
            yield payload

    monkeypatch.setattr(supjav_mod, '_get_session', lambda: ProbeSession())
    monkeypatch.setattr(supjav_mod.speed_limiter, 'acquire', lambda _size: None)

    crawler = SiteSupJav.__new__(SiteSupJav)
    crawler._cancel_job = False
    crawler._dest_folder = str(tmp_path)
    crawler._targetName = 'serial'
    crawler._direct_url = 'https://example.test/video.mp4'
    crawler._direct_referer = 'https://example.test/embed'
    crawler._progress_callback = None
    crawler._t2_executor = None

    assert crawler._download_direct() is True
    assert (tmp_path / 'serial.mp4').read_bytes() == payload


def test_create_m3u8_raises_on_zero_segments(monkeypatch):
    class DummyCrawler(M3U8Crawler):
        def __init__(self):
            self._m3u8url = 'https://example.test/master.m3u8'
            self._extra_headers = {}

    monkeypatch.setattr(
        DummyCrawler,
        '_load_m3u8',
        lambda self, url: types.SimpleNamespace(playlists=[], segments=[], keys=[]),
    )

    with pytest.raises(Exception):
        DummyCrawler()._create_m3u8()
