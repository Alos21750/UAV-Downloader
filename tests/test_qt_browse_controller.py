import threading
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication
from uav_downloader.ui.qt.browse_controller import BrowseController, VideoModel


def test_new_listing_replaces_pending_request_and_ignores_old_result():
    app = QCoreApplication.instance() or QCoreApplication([])
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(site, base, query, page, language):
        calls.append(query)
        if query == 'old':
            entered.set()
            assert release.wait(3)
        return dict(base=base, categories=[dict(name=query, url=base)],
                    videos=[dict(url='https://example.test/' + query, title=query)])
    browser = BrowseController(SimpleNamespace(), fetcher=fetch)
    browser.timer.stop()
    try:
        browser.load('JableTV', '', 'old', 1)
        assert entered.wait(2)
        browser.load('JableTV', '', 'discarded', 1)
        skipped = browser._future
        browser.load('MissAV', '', 'latest', 2)
        assert browser.loading and browser.model.rowCount() == 0
        assert skipped.cancelled()
        release.set()
        browser._future.result(3)
        browser.poll()
        assert calls == ['old', 'latest']
        assert not browser.loading and browser.page == 2
        assert browser.model.rows[0]['title'] == 'latest'
    finally:
        release.set()
        browser.close()


def test_selected_download_keeps_source_metadata_and_close_ignores_results():
    app = QCoreApplication.instance() or QCoreApplication([])
    enqueued = []
    video = dict(url='https://jable.tv/videos/example/', title='Test', duration=None,
                 _site='JableTV', _source_listing_url='https://jable.tv/categories/chinese-subtitle/')
    browser = BrowseController(SimpleNamespace(enqueueVideo=lambda *a: enqueued.append(a)),
        fetcher=lambda *a: dict(base='base', categories=[], videos=[video]))
    browser.timer.stop()
    try:
        browser.load('JableTV', '', '', 1)
        browser._future.result(3)
        browser.poll()
        browser.select(0, True)
        browser.downloadSelected('destination')
        assert enqueued[0][0]['_source_listing_url'] == video['_source_listing_url']
        assert enqueued[0][1] == 'destination'
        assert browser.model.rows[0]['duration'] == ''
        browser.load('JableTV', '', '', 2)
        browser.close()
        browser.poll()
        browser.downloadSelected('ignored')
        assert browser.model.rowCount() == 0 and len(enqueued) == 1
    finally:
        browser.close()


def test_site_switch_clears_categories_and_failure_ends_loading():
    app = QCoreApplication.instance() or QCoreApplication([])
    def fail(*args):
        raise OSError('listing unavailable')
    browser = BrowseController(SimpleNamespace(), fetcher=fail)
    browser.timer.stop()
    errors = []
    browser.errorOccurred.connect(errors.append)
    try:
        browser._categories = [dict(name='old', url='https://jable.tv/')]
        browser.load('MissAV', '', '', 1)
        assert browser.categories == []
        with pytest.raises(OSError):
            browser._future.result(3)
        browser.poll()
        assert errors == ['listing unavailable'] and not browser.loading
    finally:
        browser.close()


def test_advanced_filter_retains_hidden_tags_and_search_does_not_drop_filters():
    from urllib.parse import parse_qs, urlsplit
    from uav_downloader.sites.hanime1 import Hanime1Browser
    app = QCoreApplication.instance() or QCoreApplication([])
    requests = []
    def fetch(site, base, query, page, language):
        requests.append((site, base, page))
        return dict(base=base, categories=[], videos=[dict(url='https://hanime1.me/watch?v=123', title='fixture')])
    browser = BrowseController(SimpleNamespace(), fetcher=fetch, catalog_fetcher=Hanime1Browser.filter_catalog)
    browser.timer.stop()
    try:
        browser.openFilters()
        browser.filterHanimeTags('中文字幕')
        assert browser.hanimeTags.rowCount() == 1
        browser.selectTag(0, True)
        browser.filterHanimeTags('無碼')
        assert browser.hanimeTags.rowCount() == 1
        browser.selectTag(0, True)
        browser.applyFilters(dict(query='initial', genre='裏番', sort='觀看次數', date='過去 1 週'))
        browser._future.result(3)
        browser.poll()
        browser.turnPage(1)
        browser._future.result(3)
        browser.poll()
        assert requests[-1][2] == 2
        browser.search('new query')
        browser._future.result(3)
        browser.poll()
        query = parse_qs(urlsplit(requests[-1][1]).query)
        assert query['genre'] == ['裏番'] and query['query'] == ['new query']
        assert set(query['tags[]']) == {'中文字幕', '無碼'}
        assert browser.selectedTags == 2 and browser.filtersActive
        browser.openFilters()
        browser.resetDraftTags()
        browser.cancelFilters()
        assert browser.selectedTags == 2 and len(browser.filters['tags']) == 2
    finally:
        browser.close()


def test_live_catalog_refresh_preserves_selection_and_does_not_block_gui():
    from uav_downloader.sites.hanime1 import Hanime1Browser
    app = QCoreApplication.instance() or QCoreApplication([])
    entered, release = threading.Event(), threading.Event()
    def catalog():
        entered.set()
        assert release.wait(3)
        values = Hanime1Browser.filter_catalog()
        return dict(values, tags=(*values['tags'], 'new live tag'))
    browser = BrowseController(SimpleNamespace(), catalog_fetcher=catalog)
    browser.timer.stop()
    notifications = []
    browser.catalogChanged.connect(lambda: notifications.append(True))
    try:
        browser.openFilters()
        assert entered.wait(2) and browser.catalogLoading
        browser.filterHanimeTags('中文字幕')
        browser.selectTag(0, True)
        assert notifications == []  # Choosing tags must not reset form menus.
        release.set()
        browser._catalog_future.result(3)
        browser.poll()
        assert browser.selectedTags == 1 and not browser.catalogLoading
        browser.filterHanimeTags('new live tag')
        assert browser.hanimeTags.rowCount() == 1 and notifications == [True]
        browser.filterJableTags('black-pantyhose')
        assert browser.jableTags.rowCount() == 1
    finally:
        release.set()
        browser.close()


@pytest.mark.parametrize('action', ['replace', 'close', 'scan'])
def test_huangguo_qt_operations_cancel_metadata_before_publication(monkeypatch, action):
    from concurrent.futures import CancelledError
    from uav_downloader.sites.huangguo import HuangguoBrowser
    app = QCoreApplication.instance() or QCoreApplication([])
    entered, cancelled = threading.Event(), threading.Event()
    calls = []
    base = 'https://huangguoai.com/ai-duanju/'
    monkeypatch.setattr(HuangguoBrowser, 'fetch_categories',
                        lambda: [dict(name='fixture', url=base)])

    def fetch(url, *, cancel_check):
        calls.append(url)
        if len(calls) == 1:
            entered.set()
            deadline = __import__('time').monotonic() + 3
            while not cancel_check():
                assert __import__('time').monotonic() < deadline, 'Metadata was not cancelled'
                cancelled.wait(0.01)
            cancelled.set()
            raise CancelledError()
        return [dict(url='https://huangguoai.com/video/123/', title='latest')]

    monkeypatch.setattr(HuangguoBrowser, 'fetch_page', fetch)
    browser = BrowseController(SimpleNamespace())
    browser.timer.stop()
    errors = []
    browser.errorOccurred.connect(errors.append)
    try:
        if action == 'scan':
            browser._site, browser._base = 'Huangguo', base
            browser.selectAllResults()
            old = browser._scan_future
        else:
            browser.load('Huangguo', base, '', 1)
            old = browser._future
        assert entered.wait(2)
        if action == 'replace':
            browser.load('Huangguo', base, '', 2)
        elif action == 'close':
            browser.close()
        else:
            browser.cancelSelectionScan()
        assert cancelled.wait(2)
        if action == 'scan':
            rows, reason = old.result(2)
            assert rows == [] and reason
        else:
            with pytest.raises(CancelledError):
                old.result(2)
        if action == 'replace':
            browser._future.result(2)
        browser.poll()
        assert not errors
        assert browser.model.rowCount() == (1 if action == 'replace' else 0)
        if action == 'replace':
            assert browser.page == 2 and browser.model.rows[0]['title'] == 'latest'
        assert browser.selectedCount == 0
    finally:
        browser.close()
