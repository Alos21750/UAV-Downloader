import csv
import os
import sys
import threading
import types


def _stub_runtime_dependency(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


def _cloudscraper_stub():
    mod = types.ModuleType('cloudscraper')
    mod.create_scraper = lambda *args, **kwargs: None
    return mod


def _m3u8_stub():
    mod = types.ModuleType('m3u8')
    mod.load = lambda *args, **kwargs: None
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

from uav_downloader.core import config
from uav_downloader.apps import browse as gui_modern
from uav_downloader.apps.browse import (
    DownloadItem,
    DownloadManager,
    _download_row_action,
    _select_persist,
    _visible_window,
)


def _item(url, state):
    return DownloadItem(url, name=url, state=state)


def test_queue_csv_path_uses_appdata_download_queue(monkeypatch, tmp_path):
    monkeypatch.setenv('APPDATA', str(tmp_path))

    assert config.queue_csv_path() == os.path.join(
        str(tmp_path), 'UAV Downloader', 'download_queue.csv')


def test_download_queue_csv_round_trip_preserves_destination(tmp_path):
    path = tmp_path / 'download_queue.csv'
    mgr = DownloadManager()
    item = mgr.add_item(
        'https://supjav.com/12345.html',
        name='Example',
        state='未完成',
        dest=r'C:\Videos')
    item.progress = 42

    mgr.save_csv(str(path))

    loaded = DownloadManager()
    loaded.load_csv(str(path))
    loaded_items = loaded.get_items()

    assert len(loaded_items) == 1
    restored = loaded_items[0]
    assert restored.url == 'https://supjav.com/12345.html'
    assert restored.name == 'Example'
    assert restored.state == '未完成'
    assert restored.progress == 42
    assert restored.dest == r'C:\Videos'


def test_download_queue_csv_round_trip_preserves_allowlisted_source_evidence(
        tmp_path):
    path = tmp_path / 'download_queue.csv'
    url = 'https://jable.tv/videos/ipzz-905/'
    manager = DownloadManager()
    manager.add_item(
        url,
        state='未完成',
        source_subtitle_evidence=(
            'jable-category-chinese-subtitle',),
    )

    manager.save_csv(str(path))

    loaded = DownloadManager()
    loaded.load_csv(str(path))
    assert loaded.get_items()[0].source_subtitle_evidence == (
        'jable-category-chinese-subtitle',)


def test_download_queue_csv_rejects_unknown_mismatched_and_oversized_evidence(
        tmp_path):
    path = tmp_path / 'download_queue.csv'
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow([
            '狀態', '名稱', '進度', '速度', '網址', '目標',
            '字幕來源證據',
        ])
        writer.writerow([
            '未完成', 'Unknown', '0%', '',
            'https://example.test/one', '', 'missav-url-chinese-subtitle',
        ])
        writer.writerow([
            '未完成', 'Wrong site', '0%', '',
            'https://supjav.com/1.html', '',
            'jable-category-chinese-subtitle',
        ])
        writer.writerow([
            '未完成', 'Oversized', '0%', '',
            'https://jable.tv/videos/two/', '', 'x' * 513,
        ])

    manager = DownloadManager()
    manager.load_csv(str(path))

    assert all(
        item.source_subtitle_evidence == ()
        for item in manager.get_items())


def test_download_queue_csv_load_tolerates_missing_destination_column(tmp_path):
    path = tmp_path / 'old_queue.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['狀態', '名稱', '進度', '速度', '網址'])
        writer.writerow(['未完成', 'Old Example', '7%', '', 'https://jable.tv/videos/abc/'])

    mgr = DownloadManager()
    mgr.load_csv(str(path))
    restored = mgr.get_items()[0]

    assert restored.url == 'https://jable.tv/videos/abc/'
    assert restored.name == 'Old Example'
    assert restored.state == '未完成'
    assert restored.progress == 7
    assert restored.dest == ''


def test_download_queue_csv_load_normalizes_active_states(tmp_path):
    path = tmp_path / 'crashed_queue.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['狀態', '名稱', '進度', '速度', '網址', '目標'])
        writer.writerow(['下載中', 'Active Example', '33%', '1 MB/s',
                         'https://jable.tv/videos/active-001/', r'C:\Videos'])

    mgr = DownloadManager()
    mgr.load_csv(str(path))
    restored = mgr.get_items()[0]

    assert restored.state == '未完成'
    assert restored.progress == 33
    assert restored.dest == r'C:\Videos'


def test__select_persist_keeps_all_resumable_and_caps_completed():
    items = [
        _item('c0', '已下載'),
        _item('r0', '未完成'),
        _item('c1', '已下載'),
        _item('r1', '等待中'),
        _item('c2', '已下載'),
        _item('r2', '封鎖/解析失敗'),
        _item('c3', '已下載'),
        _item('c4', '已下載'),
    ]

    kept = _select_persist(items, 5)

    assert [i.url for i in kept] == ['r0', 'r1', 'r2', 'c3', 'c4']


def test__select_persist_never_drops_resumable_over_cap():
    items = [
        _item('r0', '未完成'),
        _item('r1', '等待中'),
        _item('r2', '封鎖/解析失敗'),
        _item('c0', '已下載'),
    ]

    kept = _select_persist(items, 2)

    assert [i.url for i in kept] == ['r0', 'r1', 'r2']


def test_save_csv_caps_with_monkeypatched_max(monkeypatch, tmp_path):
    monkeypatch.setattr(gui_modern, 'MAX_PERSIST_ROWS', 4)
    path = tmp_path / 'download_queue.csv'
    mgr = DownloadManager()
    for idx in range(2):
        mgr.add_item(f'https://example.test/r{idx}', state='未完成')
    for idx in range(5):
        mgr.add_item(f'https://example.test/c{idx}', state='已下載')

    mgr.save_csv(str(path))

    loaded = DownloadManager()
    loaded.load_csv(str(path))
    urls = [item.url for item in loaded.get_items()]

    assert len(urls) <= 4
    assert 'https://example.test/r0' in urls
    assert 'https://example.test/r1' in urls
    assert urls[-2:] == ['https://example.test/c3', 'https://example.test/c4']


def test_load_csv_caps_large_file(monkeypatch, tmp_path):
    monkeypatch.setattr(gui_modern, 'MAX_PERSIST_ROWS', 4)
    path = tmp_path / 'large_queue.csv'
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['狀態', '名稱', '進度', '速度', '網址', '目標'])
        for idx in range(2):
            writer.writerow(['未完成', f'Resumable {idx}', '0%', '',
                             f'https://example.test/r{idx}', ''])
        for idx in range(5):
            writer.writerow(['已下載', f'Completed {idx}', '100%', '',
                             f'https://example.test/c{idx}', ''])

    mgr = DownloadManager()
    mgr.load_csv(str(path))
    urls = [item.url for item in mgr.get_items()]

    assert len(urls) <= 4
    assert 'https://example.test/r0' in urls
    assert 'https://example.test/r1' in urls
    assert urls[-2:] == ['https://example.test/c3', 'https://example.test/c4']


def test_load_csv_handles_corrupt_file(tmp_path):
    path = tmp_path / 'bad_queue.csv'
    path.write_bytes(b'\xff\xfe\x00not utf-8')

    mgr = DownloadManager()
    mgr.load_csv(str(path))

    assert len(mgr.get_items()) == 0
    assert os.path.exists(str(path) + '.bak')


def test_clear_then_save_writes_header_only(tmp_path):
    path = tmp_path / 'download_queue.csv'
    mgr = DownloadManager()
    mgr.add_item('https://example.test/r0', state='未完成')
    mgr.add_item('https://example.test/c0', state='已下載')

    mgr.clear_all()
    mgr.save_csv(str(path))

    with open(path, 'r', encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f))
    assert rows == [[
        '狀態', '名稱', '進度', '速度', '網址', '目標',
        '字幕來源證據', '工作 ID', '網站', '解析度',
        '檔名範本', '縮圖', '錯誤', '錯誤代碼',
        '重試次數', '排程時間', '建立時間', '更新時間',
    ]]


def test_queue_round_trip_preserves_job_metadata_and_error(tmp_path):
    path = tmp_path / 'download_queue.csv'
    manager = DownloadManager(auto_retry_count_getter=lambda: 0)
    item = manager.add_item(
        'https://missav.ai/en/mdhg0020',
        name='MDHG0020 Full title', state='未完成', dest='downloads',
        resolution='720', filename_template='{site} {code}',
        thumbnail='https://cdn.example/thumb.jpg',
        job_id='stable-job-id', site='MissAV', created_at=123.0)
    item.error = 'connection timed out'
    item.error_code = 'network'
    item.retry_count = 2
    item.updated_at = 456.0

    manager.save_csv(str(path))
    loaded = DownloadManager()
    loaded.load_csv(str(path))
    restored = loaded.get_items()[0]

    assert restored.job_id == 'stable-job-id'
    assert restored.site == 'MissAV'
    assert restored.resolution == '720'
    assert restored.filename_template == '{site} {code}'
    assert restored.thumbnail == 'https://cdn.example/thumb.jpg'
    assert restored.error_code == 'network'
    assert restored.retry_count == 2
    assert restored.created_at == 123.0
    assert restored.updated_at == 456.0


def test__visible_window_prioritizes_active():
    items = [
        _item('done', '已下載'),
        _item('queued', '等待中'),
        _item('cancelled', '已取消'),
        _item('incomplete', '未完成'),
        _item('active', '下載中'),
    ]

    visible = _visible_window(items, 3)

    assert [item.state for item in visible] == ['下載中', '等待中', '未完成']


def test_row_retry_resets_transient_fields_and_requeues():
    item = DownloadItem(
        'https://supjav.com/12345.html', state='未完成', dest=r'C:\Videos')
    item.progress = 44
    item.speed = '2 MB/s'
    item.error = 'reset by peer'

    class FakeManager:
        def __init__(self):
            self.calls = []

        def get_items(self):
            return [item]

        def enqueue(self, url, dest):
            self.calls.append((url, dest))

    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._dlmgr = FakeManager()
    app._dest_var = types.SimpleNamespace(get=lambda: r'C:\Fallback')

    app._retry_download(item.url)

    assert (item.progress, item.speed, item.error) == (0, '', '')
    assert app._dlmgr.calls == [(item.url, r'C:\Videos')]


def test_restart_active_download_cancels_then_requeues_same_item_first(
        monkeypatch, tmp_path):
    url = 'https://supjav.com/12345.html'
    other_url = 'https://supjav.com/67890.html'
    manager = DownloadManager(max_concurrent=1)
    item = manager.add_item(
        url, name='Slow download', state='下載中', dest=str(tmp_path))
    item.progress = 47
    item.speed = '81 KB/s'
    item.error = 'old warning'

    class FakeJob:
        def __init__(self):
            self.cancel_calls = []

        def cancel_download(self, cleanup=True):
            self.cancel_calls.append(cleanup)

    old_task = gui_modern._DownloadTask(
        url, str(tmp_path), manager._cancel_epoch)
    old_task.job = FakeJob()
    queued_task = gui_modern._DownloadTask(
        other_url, str(tmp_path), manager._cancel_epoch)
    with manager._lock:
        manager._active[url] = old_task
        manager._pending.append(queued_task)

    started = []
    monkeypatch.setattr(
        manager, '_start_download_thread', lambda task: started.append(task))

    assert manager.restart(url, str(tmp_path)) is True
    assert old_task.cancelled.is_set()
    assert old_task.job.cancel_calls == [True]
    assert (item.state, item.progress, item.speed, item.error) == (
        '等待中', 0, '', '')

    # The replacement must only start after the old worker reaches its safe
    # completion point, and it must be placed ahead of older queued work.
    assert started == []
    manager._complete_download(old_task, '已取消')

    assert len(started) == 1
    replacement = started[0]
    assert replacement is manager._active[url]
    assert replacement is not old_task
    assert replacement.url == url
    assert replacement.dest == str(tmp_path)
    assert manager._pending == [queued_task]


def test_remove_after_restart_request_never_resurrects_item(monkeypatch, tmp_path):
    url = 'https://supjav.com/12345.html'
    manager = DownloadManager(max_concurrent=1)
    manager.add_item(url, state='下載中', dest=str(tmp_path))
    task = gui_modern._DownloadTask(url, str(tmp_path), manager._cancel_epoch)
    with manager._lock:
        manager._active[url] = task
    monkeypatch.setattr(manager, '_start_download_thread', lambda _task: None)

    assert manager.restart(url, str(tmp_path)) is True
    manager.remove_item(url)
    manager._complete_download(task, '已取消')

    assert manager.get_items() == []
    assert manager.active_count == 0
    assert manager.pending_count == 0


def test_restart_waits_for_old_cleanup_before_starting_replacement(
        monkeypatch, tmp_path):
    url = 'https://supjav.com/12345.html'
    manager = DownloadManager(max_concurrent=1)
    manager.add_item(url, state='下載中', dest=str(tmp_path))
    cleanup_started = threading.Event()
    allow_cleanup_finish = threading.Event()

    class SlowCleanupJob:
        def cancel_download(self, cleanup=True):
            assert cleanup is True
            cleanup_started.set()
            assert allow_cleanup_finish.wait(2)

    task = gui_modern._DownloadTask(url, str(tmp_path), manager._cancel_epoch)
    task.job = SlowCleanupJob()
    with manager._lock:
        manager._active[url] = task
    started = []
    monkeypatch.setattr(
        manager, '_start_download_thread', lambda next_task: started.append(next_task))

    restart_thread = threading.Thread(
        target=manager.restart, args=(url, str(tmp_path)))
    restart_thread.start()
    assert cleanup_started.wait(1)

    completion_thread = threading.Thread(
        target=manager._complete_download, args=(task, '已取消'))
    completion_thread.start()
    completion_thread.join(0.05)
    assert completion_thread.is_alive()
    assert started == []

    allow_cleanup_finish.set()
    restart_thread.join(1)
    completion_thread.join(1)
    assert not restart_thread.is_alive()
    assert not completion_thread.is_alive()
    assert len(started) == 1
    assert started[0] is manager._active[url]


def test_cancel_all_after_restart_request_suppresses_replacement(
        monkeypatch, tmp_path):
    url = 'https://supjav.com/12345.html'
    manager = DownloadManager(max_concurrent=1)
    item = manager.add_item(url, state='下載中', dest=str(tmp_path))
    task = gui_modern._DownloadTask(url, str(tmp_path), manager._cancel_epoch)
    with manager._lock:
        manager._active[url] = task
    started = []
    monkeypatch.setattr(
        manager, '_start_download_thread', lambda next_task: started.append(next_task))

    assert manager.restart(url, str(tmp_path)) is True
    manager.cancel_all(cleanup=False)
    manager._complete_download(task, '已取消')

    assert started == []
    assert manager.active_count == 0
    assert manager.pending_count == 0
    assert item.state == '已取消'


def test_download_row_action_exposes_requeue_only_for_active_video_states():
    assert _download_row_action(_item('preparing', '準備中')) == 'pause'
    assert _download_row_action(_item('downloading', '下載中')) == 'pause'
    assert _download_row_action(_item('failed', '未完成')) == 'retry'
    assert _download_row_action(_item('blocked', '封鎖/解析失敗')) == 'retry'
    assert _download_row_action(_item('cancelled', '已取消')) == 'retry'
    assert _download_row_action(_item('queued', '等待中')) == 'pause'
    assert _download_row_action(_item('paused', '已暫停')) == 'resume'
    assert _download_row_action(_item('done', '已下載')) == ''
