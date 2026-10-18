# coding: utf-8
"""MissAV parsing tests: video-vs-category URL validation, the ReDoS-hardened code regex
(bounded time on a pathological input), the packer-unpacker guards, and pagination."""
import re
import sys
import time
import types
from types import SimpleNamespace


def _stub(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


_stub('cloudscraper')
_stub('customtkinter')

from uav_downloader.sites.missav import SiteMissAV, MissAVBrowser, _unpack_js_eval


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

    assert MissAVBrowser.fetch_page(response.url) == []
