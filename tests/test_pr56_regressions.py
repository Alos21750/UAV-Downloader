"""Regression cases from the independent PR #56 review; no live networking."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from uav_downloader.apps import browse
from uav_downloader.core import config
from uav_downloader.core.storage import StorageWriteError
from uav_downloader.i18n import locales
from uav_downloader.sites import base
from uav_downloader.sites.missav import SiteMissAV, enrich_missav_title


def test_legacy_resolution_default_is_preserved_for_cli_and_watcher(monkeypatch):
    monkeypatch.setattr(base, '_resolution_pref', '720')
    monkeypatch.setattr(config, 'get_resolution_pref', lambda: 'highest')
    crawler = base.M3U8Crawler('invalid', silence=True)
    assert crawler._resolution_pref == '720'
    assert base.M3U8Crawler('invalid', silence=True, resolution='480')._resolution_pref == '480'


@pytest.mark.parametrize('template', [
    '{title[99]} {title}', '{title.missing} {title}',
    '{title.__class__} {title}', '{title!r}', '{title:>20}',
    '{title:{code}}', '{} {title}', '{0} {title}',
    '{site}', 'video {site} {site}',
])
def test_filename_template_only_accepts_literal_named_fields(template):
    assert config.normalize_filename_template(template) == '{title}'


def test_missav_url_validation_is_bounded_before_regex(monkeypatch):
    import re
    original = re.match
    seen = []
    def match(pattern, value, *args, **kwargs):
        seen.append(len(value))
        if len(value) > 2048:
            return None  # Do not spend minutes exercising the vulnerable regex.
        return original(pattern, value, *args, **kwargs)
    monkeypatch.setattr(re, 'match', match)
    assert not SiteMissAV.validate_url('https://missav.ai/' + '1' * 100000 + '!')
    assert not seen or max(seen) <= 2048


@pytest.mark.parametrize('title,suffix,tag', [
    ('POW-093 名稱 [無碼流出]', 'uncensored-leak', 'UNCENSORED-LEAK'),
    ('POW-093 名稱 中文字幕', 'chinese-subtitle', 'CHINESE-SUBTITLE'),
    ('POW-093 名稱 英文字幕', 'english-subtitle', 'ENGLISH-SUBTITLE'),
    ('POW-093 名稱 破壞版', 'reducing-mosaic', 'REDUCING-MOSAIC'),
])
def test_missav_variant_survives_code_only_and_code_template(title, suffix, tag):
    value = enrich_missav_title(title, f'https://missav.ai/en/pow-093-{suffix}')
    expected = f'POW-093-{tag}'
    assert base._apply_filename_mode(value, 'code-only') == expected
    assert base._apply_filename_template(value, 'pow-093', 'https://missav.ai/en/pow-093', '{code}') == expected


def test_constructor_timeout_enters_bounded_retry(monkeypatch):
    manager = browse.DownloadManager(auto_retry_count_getter=lambda: 2)
    item = manager.add_item('https://missav.ai/abc-123', state='下載中')
    task = browse._DownloadTask(item.url, 'download', manager._cancel_epoch)
    manager._active[item.url] = task
    monkeypatch.setattr(manager, '_arm_schedule_locked', lambda: None)
    fake = SimpleNamespace(_last_error=ConnectionError('connection timed out'), is_url_vaildate=lambda: False)
    monkeypatch.setattr(browse.M3U8Sites, 'CreateSite', lambda *a, **kw: fake)
    manager._run_download(task)
    assert item.state == '等待重試'
    assert item.retry_count == 1
    assert manager.pending_count == 1


def test_constructor_storage_error_stops_entire_queue(monkeypatch):
    manager = browse.DownloadManager(auto_retry_count_getter=lambda: 2)
    item = manager.add_item('https://missav.ai/abc-123', state='下載中')
    task = browse._DownloadTask(item.url, 'download', manager._cancel_epoch)
    manager._active[item.url] = task
    second = manager.add_item('https://missav.ai/abc-124')
    manager._pending.append(browse._DownloadTask(second.url, 'download', manager._cancel_epoch))
    fake = SimpleNamespace(_last_error=StorageWriteError('output', OSError('device offline'), operation='write'), is_url_vaildate=lambda: False)
    monkeypatch.setattr(browse.M3U8Sites, 'CreateSite', lambda *a, **kw: fake)
    monkeypatch.setattr(manager, '_start_download_thread', lambda task: None)
    manager._run_download(task)
    assert manager.pending_count == 0
    assert second.state == '已取消'
    assert item.state == '未完成'


def _selection_app(monkeypatch, fetch):
    app = SimpleNamespace(
        _select_all_running=False, _current_base_url='https://missav.ai/en/new',
        _site_key='MissAV', _build_gen=1, _is_closing=False,
        _selected_urls=set(), _selected_videos={},
        _selected_source_subtitle_evidence={}, _status_lbl=SimpleNamespace(configure=lambda **kw: messages.append(kw['text'])),
        _ui=lambda callback, **kw: callback(), _set_card_selected=lambda *a: None,
        _update_selection_count=lambda: None,
    )
    messages = []
    class Thread:
        def __init__(self, target, **kw): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(browse.threading, 'Thread', Thread)
    monkeypatch.setattr(browse.time, 'sleep', lambda seconds: None)
    monkeypatch.setitem(browse.SITES, 'MissAV', {'browser': SimpleNamespace(
        fetch_page=fetch, page_url=lambda base_url, page: str(page))})
    return app, messages


def test_select_every_result_goes_beyond_fifty_pages(monkeypatch):
    calls = []
    def fetch(page):
        page = int(page)
        calls.append(page)
        return [{'url': f'https://missav.ai/en/abc-{page}'}] if page <= 53 else []
    app, messages = _selection_app(monkeypatch, fetch)
    browse.ModernApp._select_all_results(app)
    assert len(app._selected_urls) == 53
    assert calls[-1] == 54
    assert not app._select_all_running


def test_select_results_reports_partial_network_failure(monkeypatch):
    def fetch(page):
        if int(page) == 2: raise ConnectionError('test network failure')
        return [{'url': 'https://missav.ai/en/abc-1'}]
    app, messages = _selection_app(monkeypatch, fetch)
    browse.ModernApp._select_all_results(app)
    assert len(app._selected_urls) == 1
    assert messages[-1] != locales.T('selection_all_done', count=1)


def test_thai_manual_does_not_claim_unimplemented_thai_output():
    manual = (Path(__file__).parents[1] / 'MANUAL.th.md').read_text(encoding='utf-8')
    assert 'สั่งให้แปลตรงออกมาเป็นภาษาไทยได้ทันที' not in manual
    assert 'ความปลอดภัย 100%' not in manual


def test_listing_download_collects_beyond_fifty_pages(monkeypatch):
    calls, enqueued = [], []
    def fetch(page):
        page = int(page)
        calls.append(page)
        return [{'url': f'https://missav.ai/en/abc-{page}'}] if page <= 53 else []
    monkeypatch.setattr(browse.MissAVBrowser, 'page_url', lambda url, page: str(page))
    monkeypatch.setattr(browse.MissAVBrowser, 'fetch_page', fetch)
    monkeypatch.setattr(browse.time, 'sleep', lambda seconds: None)
    app = SimpleNamespace(
        _is_closing=False, _build_gen=1,
        _ui=lambda callback, **kw: callback(),
        _status_lbl=SimpleNamespace(configure=lambda **kw: None),
        _dlmgr=SimpleNamespace(_cancel_epoch=0, add_item=lambda *a, **kw: None,
                               enqueue=lambda url, *a, **kw: enqueued.append(url)),
    )
    browse.ModernApp._crawl_listing(app, 'https://missav.ai/en/new', 'download')
    assert len(enqueued) == 53
    assert calls[-1] == 54


def test_clear_selection_cancels_scan_without_readding_results(monkeypatch):
    holder = {}
    def fetch(page):
        if int(page) == 2:
            browse.ModernApp._clear_selection_in_place(holder['app'])
        return [{'url': f'https://missav.ai/en/abc-{page}'}]
    app, messages = _selection_app(monkeypatch, fetch)
    holder['app'] = app
    browse.ModernApp._select_all_results(app)
    assert app._selected_urls == set()
    assert not app._select_all_running


def test_select_results_stops_repeated_page_and_reports_incomplete(monkeypatch):
    calls = []
    def fetch(page):
        calls.append(page)
        return [{'url': 'https://missav.ai/en/abc-1'}]
    app, messages = _selection_app(monkeypatch, fetch)
    browse.ModernApp._select_all_results(app)
    assert len(calls) == 2
    assert len(app._selected_urls) == 1
    assert messages[-1] != locales.T('selection_all_done', count=1)


def test_cancel_all_during_listing_fetch_does_not_enqueue_more(monkeypatch):
    enqueued = []
    manager = SimpleNamespace(_cancel_epoch=0, add_item=lambda *a, **kw: None,
                              enqueue=lambda url, *a, **kw: enqueued.append(url))
    def fetch(page):
        manager._cancel_epoch += 1
        return [{'url': 'https://missav.ai/en/abc-1'}]
    monkeypatch.setattr(browse.MissAVBrowser, 'page_url', lambda url, page: str(page))
    monkeypatch.setattr(browse.MissAVBrowser, 'fetch_page', fetch)
    app = SimpleNamespace(
        _is_closing=False, _build_gen=1, _dlmgr=manager,
        _ui=lambda callback, **kw: callback(),
        _status_lbl=SimpleNamespace(configure=lambda **kw: None),
    )
    browse.ModernApp._crawl_listing(app, 'https://missav.ai/en/new', 'download')
    assert enqueued == []
