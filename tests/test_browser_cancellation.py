"""Cancellation reaches metadata work, not just the final UI callback."""
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from uav_downloader.core import task_queue
from uav_downloader.apps import browse


def test_huangguo_page_helper_forwards_cancel_and_does_not_swallow_it(monkeypatch):
    def cancelled():
        return True

    def fetch(url, *, cancel_check):
        assert cancel_check is cancelled
        raise CancelledError()

    monkeypatch.setattr(browse.HuangguoBrowser, 'fetch_page', fetch)
    with pytest.raises(CancelledError):
        browse.fetch_page_data(browse.HuangguoBrowser, 'https://huangguoai.com/ai-duanju/',
                               cancel_check=cancelled)


def test_page_helper_preserves_other_site_fetch_signature():
    browser = SimpleNamespace(fetch_page=lambda url: [{'url': url}])
    assert browse.fetch_page_data(browser, 'fixture', cancel_check=lambda: False) == {
        'videos': [{'url': 'fixture'}]}


def test_superseded_huangguo_page_stops_without_ui_publication(monkeypatch):
    callbacks = []
    app = SimpleNamespace(
        _current_base_url='https://huangguoai.com/ai-duanju/', _page=1,
        _page_req=1, _build_gen=1, _site_key='Huangguo', _is_closing=False,
        _status_lbl=SimpleNamespace(configure=lambda **kw: None),
        _ui=lambda *a, **kw: callbacks.append(a),
    )

    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    def fetch(url, *, cancel_check):
        assert not cancel_check()
        app._page_req += 1
        assert cancel_check()
        raise CancelledError()

    monkeypatch.setattr(task_queue.threading, 'Thread', Thread)
    monkeypatch.setattr(browse.HuangguoBrowser, 'fetch_page', fetch)
    browse.ModernApp._load_page(app)
    assert callbacks == []


def test_select_all_huangguo_cancellation_reaches_request(monkeypatch):
    callbacks, reached = [], []
    app = SimpleNamespace(
        _select_all_running=False, _current_base_url='https://huangguoai.com/ai-duanju/',
        _site_key='Huangguo', _build_gen=1, _is_closing=False,
        _ui=lambda *a, **kw: callbacks.append(a))

    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    def fetch(url, *, cancel_check):
        reached.append(url)
        assert not cancel_check()
        app._selection_scan_cancel.set()
        assert cancel_check()
        raise CancelledError()

    monkeypatch.setattr(task_queue.threading, 'Thread', Thread)
    monkeypatch.setattr(browse.HuangguoBrowser, 'fetch_page', fetch)
    browse.ModernApp._select_all_results(app)
    assert len(reached) == 1
    assert not app._select_all_running
    assert len(callbacks) == 1  # Final generation-checked UI update only.


def test_huangguo_listing_crawl_cancel_epoch_reaches_request(monkeypatch):
    reached = []
    manager = SimpleNamespace(cancellation_epoch=0,
        add_item=lambda *a, **kw: pytest.fail('Cancelled request must not queue'),
        enqueue=lambda *a, **kw: pytest.fail('Cancelled request must not download'))
    app = SimpleNamespace(_build_gen=1, _is_closing=False, _dlmgr=manager,
                          _ui=lambda *a, **kw: None)

    def fetch(url, *, cancel_check):
        reached.append(url)
        assert not cancel_check()
        manager.cancellation_epoch += 1
        assert cancel_check()
        raise CancelledError()

    monkeypatch.setattr(browse.HuangguoBrowser, 'fetch_page', fetch)
    browse.ModernApp._crawl_listing(app, 'https://huangguoai.com/ai-duanju/', 'fixture')
    assert len(reached) == 1
