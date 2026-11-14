"""User-flow regressions for the v3.1.4 operations omitted by the Qt migration."""
from concurrent.futures import Future
from datetime import date
import io
import queue
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QCoreApplication, QUrl
from uav_downloader.core import config, watcher
from uav_downloader.core.console import ConsoleRelay
from uav_downloader.core.task_queue import DownloadManager
from uav_downloader.core.task_service import TaskService
from uav_downloader.core.watcher_service import WatcherService
from uav_downloader.core.watcher_store import WatcherStore
from uav_downloader.core.watcher_dates import preset_date, month_days
from uav_downloader.core.update_service import UpdateService
from uav_downloader.ui.qt.browse_controller import BrowseController, VideoModel
from uav_downloader.ui.qt.task_controller import TaskController
from uav_downloader.ui.qt.task_model import DownloadListModel
from uav_downloader.ui.qt.watcher_controller import WatcherController
from uav_downloader.ui.qt.settings_controller import SettingsController
from uav_downloader.ui.qt.update_controller import UpdateController

VIDEO = 'https://jable.tv/videos/test-001/'
OTHER = 'https://jable.tv/videos/test-002/'


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(tmp_path / 'prefs.json'))
    monkeypatch.setattr(config, '_cf_store_path', lambda: str(tmp_path / 'cookies.json'))


def test_bulk_download_consumes_typed_input_once_and_dispatches_existing_queue(tmp_path):
    manager = DownloadManager()
    manager.add_item(OTHER, state='等待中', dest=str(tmp_path))
    calls = []
    manager.enqueue = lambda *args, **kwargs: calls.append(args)
    service = TaskService(str(tmp_path / 'queue.csv'), manager)
    try:
        service.ready.result(2)
        service.submit('download_all', str(tmp_path), VIDEO + ' ' + VIDEO).result(2)
        assert [url for url, _ in calls] == [VIDEO, OTHER]
    finally:
        service.close().result(2)
        service.abandon()


def test_invalid_input_preserves_text_and_creates_no_partial_queue(tmp_path):
    service = TaskService(str(tmp_path / 'queue.csv'))
    controller = TaskController(str(tmp_path / 'queue.csv'), service=service)
    controller.timer.stop()
    errors, consumed = [], []
    controller.errorOccurred.connect(errors.append)
    controller.inputConsumed.connect(consumed.append)
    try:
        service.ready.result(2)
        controller.poll()
        controller.queueUrls(VIDEO + ' https://example.test/unsupported', str(tmp_path))
        with pytest.raises(ValueError):
            controller._pending[-1].result(2)
        controller.poll()
        assert errors and not consumed and manager_urls(service) == []
    finally:
        service.close().result(2)
        service.abandon()


def manager_urls(service):
    return [item.url for item in service.manager.get_items()]


def test_queue_only_and_file_import_dedupe_without_starting_downloads(tmp_path):
    manager = DownloadManager()
    manager.enqueue = lambda *args: pytest.fail('queue-only import must not download')
    service = TaskService(str(tmp_path / 'queue.csv'), manager)
    file = tmp_path / '中文 网址.csv'
    file.write_text('\ufeffurl\n' + VIDEO + '\n' + VIDEO + '\ninvalid\n' + OTHER + '\n', encoding='utf-8')
    try:
        service.ready.result(2)
        assert service.submit('queue_urls', VIDEO, str(tmp_path)).result(2) == 1
        request = service.submit('import_files', [str(file)], str(tmp_path)).result(2)
        assert request.done.result(2) == 1
        assert manager_urls(service) == [VIDEO, OTHER]
        assert all(item.state == '等待中' for item in manager.get_items())
    finally:
        service.close().result(2)
        service.abandon()


def test_clear_during_file_read_prevents_late_batch_resurrection(tmp_path):
    entered, release = threading.Event(), threading.Event()
    service = TaskService(str(tmp_path / 'queue.csv'))
    def reader(paths, stopped):
        entered.set()
        assert release.wait(3)
        yield VIDEO
    service.files.reader = reader
    try:
        service.ready.result(2)
        request = service.submit('import_files', ['unused'], str(tmp_path)).result(2)
        assert entered.wait(2)
        service.submit('clear_all').result(2)
        release.set()
        assert request.done.result(2) == 0
        assert manager_urls(service) == []
    finally:
        release.set()
        service.close().result(3)
        service.abandon()


def test_close_waits_for_file_reader_and_reports_file_error_without_gui_io(tmp_path):
    service = TaskService(str(tmp_path / 'queue.csv'))
    try:
        service.ready.result(2)
        request = service.submit('import_files', [str(tmp_path / 'missing.csv')], str(tmp_path)).result(2)
        with pytest.raises(FileNotFoundError):
            request.done.result(2)
        # The command actor forwards the async reader error.
        service.submit('save').result(2)
        assert isinstance(service.errors.get(timeout=2), str)
        assert service.close().result(2)
    finally:
        service.abandon()


def test_download_view_uses_upstream_order_cap_and_history_filter(tmp_path):
    manager = DownloadManager()
    service = TaskService(str(tmp_path / 'queue.csv'), manager)
    controller = TaskController(str(tmp_path / 'queue.csv'), service=service)
    controller.timer.stop()
    try:
        service.ready.result(2)
        for index in range(240):
            manager.add_item(f'https://jable.tv/videos/history-{index}/', state='已下載')
        manager.add_item(VIDEO, state='下載中')
        controller.poll()
        assert controller.model.rowCount() == 200
        assert controller.model._rows[0].url == VIDEO
        assert controller.hiddenTaskCount == 41
        controller.setFilter('done')
        assert controller.model.rowCount() == 200
        assert all(row.state == '已下載' for row in controller.model._rows)
        assert controller.hiddenTaskCount == 40
        assert len(manager.snapshot_items()) == 241
    finally:
        service.close().result(3)
        service.abandon()


def test_selection_survives_progress_updates_and_select_all_notifies_once():
    manager, model = DownloadManager(), DownloadListModel()
    for n in range(1000):
        manager.add_item(f'https://jable.tv/videos/test-{n}/')
    model.refresh(manager.snapshot_items())
    signals = []
    model.selectionChanged.connect(lambda: signals.append(True))
    model.select_all(True)
    assert len(signals) == 1 and len(model.selected) == 1000
    manager.get_items()[0].progress = 25
    model.refresh(manager.snapshot_items())
    assert len(model.selected) == 1000


def test_theme_cycle_persists_three_modes_and_write_failure_keeps_previous_mode(tmp_path, monkeypatch):
    config._save_prefs(dict(theme='dark'))
    service = TaskService(str(tmp_path / 'queue.csv'))
    controller = SettingsController(service)
    controller.timer.stop()
    try:
        service.ready.result(2)
        for _ in range(3):
            controller._pending[1].result(2)
            controller.poll()
        for expected in ['system', 'light', 'dark']:
            controller.cycleTheme()
            controller._pending[1].result(2)
            controller.poll()
            assert config.get_theme() == controller.values['theme'] == expected
        monkeypatch.setattr(config, '_save_prefs', lambda value: (_ for _ in ()).throw(OSError('full')))
        controller.cycleTheme()
        with pytest.raises(OSError):
            controller._pending[1].result(2)
        controller.poll()
        assert controller.values['theme'] == config.get_theme() == 'dark'
    finally:
        controller.abandon()
        service.abandon()


def test_page_jump_empty_last_page_and_fetch_failure_preserve_last_good_cards():
    requests = []
    def fetch(site, base, query, page, language):
        requests.append((query, page))
        if page == 3:
            raise OSError('unavailable')
        return dict(base=base, categories=[], videos=[dict(url=VIDEO, title='card')] if page < 4 else [])
    browser = BrowseController(SimpleNamespace(), fetcher=fetch)
    browser.timer.stop()
    try:
        browser.load('JableTV', '', 'query', 1)
        browser._future.result(2); browser.poll()
        browser.jumpPage(3)
        with pytest.raises(OSError): browser._future.result(2)
        browser.poll()
        assert browser.page == 1 and browser.model.rowCount() == 1
        browser.jumpPage(4)
        browser._future.result(2); browser.poll()
        assert browser.page == 1 and browser.model.rowCount() == 1 and not browser.hasNext
        browser.turnPage(1)
        assert requests == [('query', 1), ('query', 3), ('query', 4)]
    finally:
        browser.close()


def test_version_badges_are_display_only_and_do_not_create_subtitle_evidence():
    model = VideoModel()
    model.replace([dict(url=VIDEO, title='中文字幕'), dict(url=OTHER, title='uncensored')], 'JableTV')
    assert [row['versionBadge'] for row in model.rows] == ['chinese-subtitle', 'uncensored']
    assert all('_source_subtitle_evidence' not in row for row in model.rows)


def test_restored_watcher_configuration_autostarts_once_after_startup_gate(tmp_path):
    starts = []
    class Worker(watcher.SmallToolWorker):
        def start_monitoring(self):
            starts.append(True)
            return True
    WatcherStore(tmp_path).update_config(dict(output_folder=str(tmp_path / 'media'),
        selected_targets=[dict(site='JableTV', id='feed:latest')]))
    service = WatcherService(tmp_path, worker_factory=Worker)
    controller = WatcherController(tmp_path, service=service, startup_ready=False)
    controller.timer.stop()
    try:
        service.ready.result(2); controller.poll()
        assert not starts
        controller.setStartupReady(True)
        controller._pending[0][1].result(2); controller.poll()
        controller.setStartupReady(True); controller.poll()
        assert len(starts) == 1
    finally:
        controller.close().result(2)
        controller.abandon()


def test_stop_and_close_suppress_delayed_autostart():
    ready = Future()
    calls = []
    def submit(action, *args):
        calls.append(action)
        result = Future(); result.set_result(True); return result
    controller = WatcherController('', service=SimpleNamespace(ready=ready, worker=None, events=queue.SimpleQueue(), submit=submit))
    controller.timer.stop()
    controller.stop()
    ready.set_result(dict(output_folder='unused', selected_targets=[dict(site='JableTV', id='feed:latest')]))
    controller.poll(); controller.setStartupReady(True)
    assert calls == ['stop']


def test_legacy_category_names_restore_to_stable_ids_and_ignore_unknown_targets():
    ready = Future()
    ready.set_result(dict(selected_targets=[dict(site='JableTV', category='最近更新'),
                                             dict(site='MissAV', id='feed:latest'), dict(site='Unknown', category='bad')]))
    controller = WatcherController('', service=SimpleNamespace(ready=ready, worker=None, events=queue.SimpleQueue()), auto_start=False)
    controller.timer.stop(); controller.poll()
    assert {(row['site'], row['id']) for row in controller.model.selection()} == {
        ('JableTV', 'feed:latest'), ('MissAV', 'feed:latest')}


def test_group_selection_and_collapse_preserve_other_sites_and_stable_ids():
    ready = Future(); ready.set_result(dict(selected_targets=[]))
    controller = WatcherController('', service=SimpleNamespace(ready=ready, worker=None, events=queue.SimpleQueue()), auto_start=False)
    controller.timer.stop(); controller.poll()
    controller.selectGroup('feeds', True)
    before = set((r['site'], r['id']) for r in controller.model.selection())
    controller.setGroup('feeds'); assert controller.targets.rowCount() == 6
    controller.toggleGroup('feeds'); assert controller.targets.rowCount() == 0
    controller.toggleGroup('feeds'); assert controller.targets.rowCount() == 6
    controller.setFilter('MissAV', '')
    controller.selectGroup('feeds', True)
    assert before.issubset(set((r['site'], r['id']) for r in controller.model.selection()))


def test_calendar_shortcuts_clamp_month_end_and_include_leap_day():
    assert preset_date(0, date(2024, 3, 1)) == '2024-02-29'
    assert preset_date(1, date(2024, 3, 31)) == '2024-02-29'
    assert preset_date(4, date(2024, 3, 31)) == '2023-09-30'
    values = month_days(2024, 2)
    assert len(values['days']) == 42
    assert values['days'][31]['value'] == '2024-02-29'


def test_schedule_snapshot_reflects_worker_saved_checkpoint_without_gui_disk_read(tmp_path):
    service = WatcherService(tmp_path)
    try:
        service.ready.result(2)
        service.store.update_config(dict(scan_schedule=dict(mode='interval', interval_hours=4, daily_time='18:00'),
                                         last_check_iso='2026-10-14T04:00:00+00:00'))
        assert service.schedule_snapshot()['last_check_iso'] == '2026-10-14T04:00:00+00:00'
        assert service.schedule_snapshot()['scan_schedule']['interval_hours'] == 4
    finally:
        service.close().result(2); service.abandon()


def test_close_flushes_staged_watcher_fields_and_selection_before_stop(tmp_path):
    service = WatcherService(tmp_path)
    controller = WatcherController(tmp_path, service=service, auto_start=False)
    controller.timer.stop()
    try:
        service.ready.result(2); controller.poll()
        controller.selectGroup('feeds', True)
        controller.prepareClose(dict(output_folder=str(tmp_path / 'media'), baseline_date='2024-02-29'))
        controller.close().result(2)
        cfg = service.store.load_config()
        assert cfg['baseline_date'] == '2024-02-29' and len(cfg['selected_targets']) == 6
    finally:
        controller.abandon()


def test_console_relay_is_bounded_clearable_and_keeps_original_output():
    stream = io.StringIO(); relay = ConsoleRelay(stream)
    relay.write('first'); relay.write(' line\n')
    assert relay.drain() == 'first line' and stream.getvalue() == 'first line\n'
    relay.write('pending'); relay.clear(); relay.write('new\n')
    assert relay.drain() == 'new'
    for n in range(1000): relay.write(str(n) + '\n')
    assert len(relay.drain().splitlines()) == 256


def test_update_prompt_once_per_version_and_none_after_close():
    release = dict(version='9.0.0', html_url='https://example.test/release', notes='notes', assets={})
    controller = UpdateController(service=UpdateService(checker=lambda: release))
    controller.timer.stop(); prompts = []
    controller.promptRequested.connect(lambda: prompts.append(controller.notes))
    try:
        for _ in range(2):
            controller.check(); controller._future.result(2); controller.poll()
        assert prompts == ['notes']
        controller.check(); controller._future.result(2); controller.close(); controller.poll()
        assert prompts == ['notes']
    finally:
        controller.close()
