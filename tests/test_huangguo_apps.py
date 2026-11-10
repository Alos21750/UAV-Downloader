"""Offline Browser/Watcher integration for numbered public drama episodes."""
from types import SimpleNamespace
from concurrent.futures import CancelledError

import pytest

from uav_downloader.apps import browse, watch, watch_categories


def test_huangguo_registered_with_stable_public_category_targets():
    browser = browse.SITES['Huangguo']['browser']
    assert watch_categories.SITES['Huangguo']['browser'] is browser
    targets = list(watch_categories.iter_targets('Huangguo'))
    assert {target['id'] for target in targets} == {'category:ai-duanju', 'category:ai-manju'}
    assert {target['url'] for target in targets} == {
        'https://huangguoai.com/ai-duanju/', 'https://huangguoai.com/ai-manju/'}


@pytest.mark.parametrize('url,listing', [
    ('https://huangguoai.com/ai-duanju/', True),
    ('https://huangguoai.com/ai-manju/2/', True),
    ('https://huangguoai.com/video/123/', False),
    ('https://huangguoai.com/video/123/ep-3/', False),
    ('https://huangguoai.com.evil.example/ai-duanju/', False),
    ('https://user:password@huangguoai.com/ai-duanju/', False),
])
def test_huangguo_desktop_listing_gate(url, listing):
    assert browse.ModernApp._is_listing_url(None, url) is listing


def test_huangguo_search_uses_own_browser_not_missav():
    app = SimpleNamespace(
        _site_key='Huangguo', _search_var=SimpleNamespace(get=lambda: '公開短劇'),
        _selected_urls=set(), _selected_videos={}, _selected_source_subtitle_evidence={},
        _update_selection_count=lambda: None, _load_page=lambda: None)
    browse.ModernApp._on_search(app)
    assert app._current_base_url == browse.SITES['Huangguo']['browser'].search_url('公開短劇')
    assert app._page == 1


def test_huangguo_site_settings_inherit_and_allow_separate_overrides(tmp_path):
    cfg = {'output_folder': str(tmp_path), 'baseline_date': '2026-01-01',
           'site_settings': {}}
    assert watch._resolve_site_settings(cfg, 'Huangguo') == {
        'output_folder': str(tmp_path), 'baseline_date': '2026-01-01'}
    cfg['site_settings']['Huangguo'] = {
        'output_folder': str(tmp_path / 'drama'), 'baseline_date': '2026-10-01'}
    assert watch._resolve_site_settings(cfg, 'Huangguo') == cfg['site_settings']['Huangguo']
    assert watch._resolve_site_settings(cfg, 'MissAV')['output_folder'] == str(tmp_path)


def test_huangguo_listing_crawl_queues_each_numbered_episode(monkeypatch):
    browser = browse.SITES['Huangguo']['browser']
    added, enqueued, fetched = [], [], []

    def fetch(url, **kwargs):
        fetched.append(url)
        return _episodes('2026-10-10') if len(fetched) == 1 else []

    monkeypatch.setattr(browser, 'fetch_page', fetch)
    monkeypatch.setattr(browse.time, 'sleep', lambda _seconds: None)
    manager = SimpleNamespace(
        _cancel_epoch=0,
        add_item=lambda url, **kwargs: added.append((url, kwargs)),
        enqueue=lambda url, dest, **kwargs: enqueued.append((url, dest)))
    app = SimpleNamespace(
        _build_gen=1, _is_closing=False, _dlmgr=manager,
        _status_lbl=SimpleNamespace(configure=lambda **_kwargs: None),
        _ui=lambda callback, **_kwargs: callback())
    browse.ModernApp._crawl_listing(
        app, 'https://huangguoai.com/ai-duanju/', 'public-drama')
    assert len(fetched) == 2
    assert [url for url, _kwargs in added] == [item['url'] for item in _episodes('')]
    assert all(kwargs['site'] == 'Huangguo' for _url, kwargs in added)
    assert [kwargs['name'] for _url, kwargs in added] == ['Public drama - EP 1', 'Public drama - EP 3']
    assert len(enqueued) == 2


@pytest.fixture
def huangguo_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    logs, downloaded = [], []
    worker = watch.SmallToolWorker(logs.append)
    worker._download_one = lambda item, dest: downloaded.append((item, dest))
    target = watch_categories.find_target('Huangguo', 'category:ai-duanju')
    cfg = {'output_folder': str(tmp_path / 'global'), 'baseline_date': '2026-01-01',
           'site_settings': {'Huangguo': {
               'output_folder': str(tmp_path / 'drama'), 'baseline_date': '2026-10-01'}},
           'first_run_done': False, 'version_preference': 'standard',
           'selected_targets': [{'site': 'Huangguo', 'id': target['id']}]}
    browser = watch.SITES['Huangguo']['browser']
    return worker, cfg, browser, logs, downloaded


def _episodes(date):
    return [dict(url='https://huangguoai.com/video/123/' + (f'ep-{ep}/' if ep > 1 else ''),
                 title=f'Public drama - EP {ep}', date=date, episode=ep, series_id='123')
            for ep in (1, 3)]


def test_huangguo_watcher_keeps_numbered_episodes_and_destination(huangguo_scan, monkeypatch):
    worker, cfg, browser, _logs, downloaded = huangguo_scan
    monkeypatch.setattr(browser, 'fetch_page', lambda url, **kw: [] if url.rstrip('/').endswith('/2') else _episodes('2026-10-10'))
    assert worker._scan_and_download(cfg) is True
    assert [video['episode'] for video, _dest in downloaded] == [1, 3]
    assert all(dest == cfg['site_settings']['Huangguo']['output_folder'] for _video, dest in downloaded)


@pytest.mark.parametrize('date,expected', [('2026-09-30', 0), ('2026-10-01', 2), ('', 0)])
def test_huangguo_watcher_baseline_and_unknown_date_are_conservative(
        huangguo_scan, monkeypatch, date, expected):
    worker, cfg, browser, logs, downloaded = huangguo_scan
    monkeypatch.setattr(browser, 'fetch_page', lambda url, **kw: [] if url.rstrip('/').endswith('/2') else _episodes(date))
    monkeypatch.setattr(browser, 'fetch_video_date', lambda _url, **kw: '')
    worker._scan_and_download(cfg)
    assert len(downloaded) == expected
    if not date:
        assert any('no confirmed date' in line for line in logs)
        assert worker._seen == {}


def test_huangguo_watcher_confirms_missing_listing_date_from_detail(huangguo_scan, monkeypatch):
    worker, cfg, browser, _logs, downloaded = huangguo_scan
    monkeypatch.setattr(browser, 'fetch_page', lambda url, **kw: [] if url.rstrip('/').endswith('/2') else _episodes(''))
    monkeypatch.setattr(browser, 'fetch_video_date', lambda _url, **kw: '2026-10-11')
    assert worker._scan_and_download(cfg) is True
    assert len(downloaded) == 2


def test_huangguo_detail_date_failure_does_not_complete_first_scan(huangguo_scan, monkeypatch):
    worker, cfg, browser, _logs, downloaded = huangguo_scan
    monkeypatch.setattr(browser, 'fetch_page', lambda url, **kw: [] if url.rstrip('/').endswith('/2') else _episodes(''))

    def failed(_url, **kw):
        raise RuntimeError('Temporary unavailable detail')

    monkeypatch.setattr(browser, 'fetch_video_date', failed)
    assert worker._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is False
    assert downloaded == []


@pytest.mark.parametrize('detail', [False, True])
def test_huangguo_watcher_cancels_metadata_without_completing_scan(
        huangguo_scan, monkeypatch, detail):
    worker, cfg, browser, logs, downloaded = huangguo_scan
    reached = []

    def cancelled(url, *, cancel_check):
        assert not cancel_check()
        reached.append(url)
        worker._stop.set()
        assert cancel_check()
        raise CancelledError()

    if detail:
        monkeypatch.setattr(browser, 'fetch_page', lambda url, **kw: _episodes(''))
        monkeypatch.setattr(browser, 'fetch_video_date', cancelled)
    else:
        monkeypatch.setattr(browser, 'fetch_page', cancelled)
    assert worker._scan_and_download(cfg) is False
    assert reached
    assert cfg['first_run_done'] is False
    assert downloaded == []
    assert worker._seen == {}
    assert not any('[ERR]' in line or '[BLOCKED]' in line for line in logs)
