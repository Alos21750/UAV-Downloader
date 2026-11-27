# coding: utf-8
"""MissAV parsing tests: video-vs-category URL validation, the ReDoS-hardened code regex
(bounded time on a pathological input), the packer-unpacker guards, and pagination."""
import re
import sys
import time
import types
from types import SimpleNamespace

import pytest


def _stub(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


_stub('cloudscraper')
_stub('customtkinter')

from uav_downloader.sites.missav import SiteMissAV, MissAVBrowser, _unpack_js_eval, enrich_missav_title


def test_validate_accepts_video_pages():
    assert SiteMissAV.validate_url('https://missav.ai/sone-543')
    assert SiteMissAV.validate_url('https://missav.ai/cn/sone-543-chinese-subtitle')
    assert SiteMissAV.validate_url('https://missav.ai/dm1151/092014_887')
    assert SiteMissAV.validate_url('https://missav.ai/dm464/081012-097')
    # Current actress/search listings also use compact codes with no dash or
    # underscore before the numeric portion.
    assert SiteMissAV.validate_url('https://missav.ai/en/mdhg0020')
    assert SiteMissAV.validate_url('https://missav.ai/dm13/en/mcy0244')
    assert SiteMissAV.validate_url('https://missav.ai/dm13/en/mkyba008')


def test_validate_rejects_category_and_foreign_pages():
    assert not SiteMissAV.validate_url('https://missav.ai/dm278/chinese-subtitle')
    assert not SiteMissAV.validate_url('https://missav.ai/dm539/new')
    assert not SiteMissAV.validate_url('https://missav.ai/dm298/en/today-hot')
    assert not SiteMissAV.validate_url('https://jable.tv/videos/x/')


def test_code_regex_is_not_redos():
    # A ~40k-char string that reaches the code group but never satisfies the trailing
    # [-_]\d ran QUADRATICALLY under the old pattern (measured ~8s) — a GUI freeze via the
    # 800ms clipboard poller. The linear rewrite handles it instantly; a regression to the
    # old nested-quantifier pattern would blow this time budget.
    evil = 'https://missav.ai/a' + '_a' * 20000 + '!'
    t0 = time.time()
    re.match(SiteMissAV.website_dirname_pattern, evil, flags=re.I)
    assert time.time() - t0 < 0.5


def test_unpack_guards_bad_base():
    # base<=1 would make to_base loop forever; must return None instead.
    packed = "eval(function(p,a,c,k,e,d){}('x',1,1,'a'.split('|')"
    assert _unpack_js_eval(packed) is None


def test_unpack_guards_absurd_count():
    # a huge `c` would allocate an unbounded lookup dict; must bail fast.
    packed = "eval(function(p,a,c,k,e,d){}('x',36,999999999,'a'.split('|')"
    t0 = time.time()
    assert _unpack_js_eval(packed) is None
    assert time.time() - t0 < 0.5


def test_unpack_returns_none_on_non_packer():
    assert _unpack_js_eval('just some normal <script> here') is None


def test_unpack_base62_preserves_case_and_unknown_tokens():
    keys = [''] * 62
    keys[10], keys[36], keys[61] = 'lower', 'upper', 'last'
    program = "eval(function(p,a,c,k,e,d){}('a A Z stay',62,62,'%s'.split('|')" % '|'.join(keys)
    assert _unpack_js_eval(program) == 'lower upper last stay'


def test_unpack_rejects_unsupported_radix():
    assert _unpack_js_eval("eval(function(p,a,c,k,e,d){}('0',63,1,'x'.split('|')") is None


def test_page_url_pagination():
    assert MissAVBrowser.page_url('https://missav.ai/dm539/new', 1) == 'https://missav.ai/dm539/new'
    assert MissAVBrowser.page_url('https://missav.ai/dm539/new', 3) == 'https://missav.ai/dm539/new?page=3'
    assert MissAVBrowser.page_url('https://missav.ai/x?a=1', 2) == 'https://missav.ai/x?a=1&page=2'


def test_listing_fetch_rejects_404_grid_page(monkeypatch):
    response = SimpleNamespace(
        status_code=404,
        content=b'<div class="grid"><div><a href="/ad-123">ad</a></div></div>',
        url='https://missav.ai/genres/not-found',
    )

    def fake_fetch(_scraper, _url, _site, validator, **_kwargs):
        assert validator(response) is False
        return response, 'missav.ai', 'failed'

    monkeypatch.setattr(MissAVBrowser, '_get_scraper',
                        classmethod(lambda cls: object()))
    monkeypatch.setattr(
        'uav_downloader.sites.missav.fetch_with_mirrors', fake_fetch)

    with pytest.raises(RuntimeError, match='MissAV'):
        MissAVBrowser.fetch_page(response.url)


def test_enrich_missav_title_adds_uncensored_leak_suffix():
    assert enrich_missav_title('POW-093 Melcy-chan', 'https://missav.ai/en/pow-093-uncensored-leak') == 'POW-093-UNCENSORED-LEAK Melcy-chan'
    assert enrich_missav_title('POW-093 梅爾西醬', 'pow-093-uncensored-leak') == 'POW-093-UNCENSORED-LEAK 梅爾西醬'
    assert enrich_missav_title('POW-093', 'https://missav.ai/pow-093-uncensored-leak') == 'POW-093-UNCENSORED-LEAK'
    assert enrich_missav_title('POW-093 Melcy-chan', 'https://missav.ai/en/pow-093') == 'POW-093 Melcy-chan'
    assert enrich_missav_title('POW-075 Poppo', 'https://missav.ai/dm14/en/pow-075-uncensored-leak') == 'POW-075-UNCENSORED-LEAK Poppo'


def test_enrich_missav_title_avoids_duplicate_suffix():
    assert enrich_missav_title('POW-093-UNCENSORED-LEAK Melcy-chan', 'pow-093-uncensored-leak') == 'POW-093-UNCENSORED-LEAK Melcy-chan'
    assert enrich_missav_title('POW-093 梅爾西醬 [無碼流出]', 'pow-093-uncensored-leak') == 'POW-093-UNCENSORED-LEAK 梅爾西醬 [無碼流出]'


@pytest.mark.parametrize(('url', 'html'), [
    (
        'https://missav.ai/en/search/nonexistent',
        (
            b'<html><body>'
            b'<header>MissAV</header>'
            b'<h1>Search result of nonexistent</h1>'
            b'<a href="https://missav.ai/en/search/nonexistent?filters=individual">Single actress</a>'
            b'<div x-init="$nextTick(() => { window.recombeeClient.send('
            b'new recombee.SearchItems(window.user_uuid, window.searchQuery, perPage)) })">'
            b'<div class="grid">'
            b'<div class="thumbnail group" x-show="! recommendItems.length"><a href="#">&nbsp;</a></div>'
            b'<template x-for="item in recommendItems">'
            b'<div class="thumbnail group"><a href="#">x</a></div>'
            b'</template>'
            b'</div></div>'
            b'<footer>MissAV</footer>'
            b'</body></html>'
        ),
    ),
    (
        'https://missav.ai/en/search/nonexistent',
        b'<html><body><section class="no-results"><p>No videos found</p></section></body></html>',
    ),
    (
        'https://missav.ai/dm36/en/siro?filters=english-subtitle&page=999',
        b'<html><body><div class="text-center text-xl text-nord13 font-light">No records</div></body></html>',
    ),
])
def test_empty_search_page_returns_empty_list(monkeypatch, url, html):
    from uav_downloader.core import config
    from uav_downloader.sites import base

    response = SimpleNamespace(
        status_code=200,
        content=html,
        headers={},
        url=url,
    )
    monkeypatch.setattr(config, 'MIRRORS', {'missav': ['missav.ai', 'backup.invalid']})
    monkeypatch.setattr(config, 'RATE_LIMIT_WAITS', {})
    monkeypatch.setattr(config, 'get_cf_override', lambda _host: None)
    monkeypatch.setattr(config, 'proxy_request_kwargs', lambda: {})
    monkeypatch.setattr(base, '_active_host', {})

    def get(req_url, **_kwargs):
        if 'backup.invalid' in req_url:
            raise AssertionError('confirmed empty listing should not fail over to backup mirror')
        return response

    monkeypatch.setattr(MissAVBrowser, '_get_scraper', classmethod(lambda cls: SimpleNamespace(get=get)))
    assert MissAVBrowser.fetch_page(url) == []


def test_search_error_page_with_chrome_fails_over_to_working_mirror(monkeypatch):
    from uav_downloader.core import config
    from uav_downloader.sites import base

    error_html = (
        b'<html><header>MissAV</header><nav>Menu</nav>'
        b'<main><h1>502 Bad Gateway</h1><p>Upstream error</p></main>'
        b'<footer>MissAV</footer></html>'
    )
    valid_html = (
        b'<html><body><div class="thumbnail">'
        b'<a href="https://missav.ws/en/sone-543">'
        b'<img alt="SONE-543 Sample" data-src="https://img.example/sone-543.jpg"/></a>'
        b'<div class="my-2"><a href="https://missav.ws/en/sone-543">SONE-543 Sample</a></div>'
        b'</div></body></html>'
    )
    monkeypatch.setattr(config, 'MIRRORS', {'missav': ['missav.ai', 'missav.ws']})
    monkeypatch.setattr(config, 'RATE_LIMIT_WAITS', {})
    monkeypatch.setattr(config, 'get_cf_override', lambda _host: None)
    monkeypatch.setattr(config, 'proxy_request_kwargs', lambda: {})
    monkeypatch.setattr(base, '_active_host', {})

    seen_urls = []

    def get(url, **_kwargs):
        seen_urls.append(url)
        if 'missav.ai' in url:
            return SimpleNamespace(status_code=200, content=error_html, headers={}, url=url)
        return SimpleNamespace(status_code=200, content=valid_html, headers={}, url=url)

    monkeypatch.setattr(MissAVBrowser, '_get_scraper', classmethod(lambda cls: SimpleNamespace(get=get)))
    videos = MissAVBrowser.fetch_page('https://missav.ai/en/search/sone-543')
    assert len(seen_urls) == 2
    assert videos == [{
        'url': 'https://missav.ws/en/sone-543',
        'title': 'SONE-543 Sample',
        'thumbnail': 'https://img.example/sone-543.jpg',
        'duration': '',
    }]


def test_search_service_unavailable_grid_fails_instead_of_looking_empty(monkeypatch):
    from bs4 import BeautifulSoup
    from uav_downloader.apps.browse import fetch_page_data
    from uav_downloader.core import config
    from uav_downloader.sites import base

    url = 'https://missav.ai/en/search/sone-543'
    error_grid_html = (
        b'<main><h1>Service unavailable</h1>'
        b'<div class="grid"><div>Try later</div></div></main>'
    )
    soup = BeautifulSoup(error_grid_html, 'html.parser')
    assert MissAVBrowser._has_listing_cards(soup, url) is False
    assert MissAVBrowser._is_empty_search(soup, url) is False

    monkeypatch.setattr(config, 'MIRRORS', {'missav': ['missav.ai']})
    monkeypatch.setattr(config, 'RATE_LIMIT_WAITS', {})
    monkeypatch.setattr(config, 'get_cf_override', lambda _host: None)
    monkeypatch.setattr(config, 'proxy_request_kwargs', lambda: {})
    monkeypatch.setattr(base, '_active_host', {})
    monkeypatch.setattr(
        MissAVBrowser,
        '_get_scraper',
        classmethod(lambda cls: SimpleNamespace(
            get=lambda req_url, **_kw: SimpleNamespace(
                status_code=200, content=error_grid_html, headers={}, url=req_url
            )
        )),
    )

    with pytest.raises(RuntimeError, match='MissAV'):
        MissAVBrowser.fetch_page(url)

    result = fetch_page_data(MissAVBrowser, url)
    assert result['videos'] == []
    assert 'MissAV' in result['error']

