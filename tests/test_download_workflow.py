import json
from types import SimpleNamespace

import pytest

from uav_downloader.apps.downloads import (
    QueueAddSummary,
    classify_download_problem,
    export_items,
    extract_urls,
    filter_items,
    format_eta,
    load_url_file,
    next_schedule_epoch,
    site_name,
    storage_preflight,
    video_code,
)
from uav_downloader.apps import browse
from uav_downloader.apps.browse import DownloadManager, _DownloadTask
from uav_downloader.core import config


def test_extract_urls_deduplicates_and_strips_message_punctuation():
    text = (
        'one https://missav.ai/en/mdhg0020, '
        'again https://missav.ai/en/mdhg0020 and '
        'https://jable.tv/videos/abc-123/).'
    )

    assert extract_urls(text) == [
        'https://missav.ai/en/mdhg0020',
        'https://jable.tv/videos/abc-123/',
    ]


def test_load_url_file_supports_text_csv_and_json(tmp_path):
    text_path = tmp_path / 'urls.txt'
    text_path.write_text('https://missav.ai/en/mdhg0020\n', encoding='utf-8')
    csv_path = tmp_path / 'urls.csv'
    csv_path.write_text(
        'name,url\nOne,https://jable.tv/videos/abc-123/\n',
        encoding='utf-8')
    json_path = tmp_path / 'urls.json'
    json_path.write_text(json.dumps({
        'items': [{'url': 'https://supjav.com/12345.html'}],
    }), encoding='utf-8')

    assert load_url_file(str(text_path)) == [
        'https://missav.ai/en/mdhg0020']
    assert load_url_file(str(csv_path)) == [
        'https://jable.tv/videos/abc-123/']
    assert load_url_file(str(json_path)) == [
        'https://supjav.com/12345.html']


def test_load_url_file_csv_honours_limit(tmp_path):
    csv_path = tmp_path / 'urls.csv'
    csv_path.write_text(
        'url\nhttps://missav.ws/a\nhttps://missav.ws/b\n'
        'https://missav.ws/c\n',
        encoding='utf-8',
    )

    assert load_url_file(str(csv_path), max_urls=2) == [
        'https://missav.ws/a', 'https://missav.ws/b',
    ]


def test_filters_cover_active_queue_failure_history_and_paused():
    items = [SimpleNamespace(state=state) for state in (
        '下載中', '等待中', '未完成', '已下載', '已暫停')]

    assert [item.state for item in filter_items(items, 'active')] == ['下載中']
    assert [item.state for item in filter_items(items, 'queued')] == ['等待中']
    assert [item.state for item in filter_items(items, 'failed')] == ['未完成']
    assert [item.state for item in filter_items(items, 'done')] == ['已下載']
    assert [item.state for item in filter_items(items, 'paused')] == ['已暫停']
    assert filter_items(items, 'all') == items


@pytest.mark.parametrize(
    ('error', 'state', 'code', 'retryable'),
    [
        ('No space left on device', '未完成', 'disk_full', False),
        ('Cloudflare 403', '封鎖/解析失敗', 'blocked', True),
        ('connection timed out', '未完成', 'network', True),
        ('HTTP 404 video not found', '未完成', 'video_missing', False),
        ('ffmpeg not found', '未完成', 'ffmpeg_missing', False),
    ],
)
def test_problem_classification(error, state, code, retryable):
    problem = classify_download_problem(error, state)
    assert (problem.code, problem.retryable) == (code, retryable)


def test_storage_preflight_creates_and_checks_writable_destination(tmp_path):
    result = storage_preflight(str(tmp_path / 'downloads'), minimum_free_bytes=0)
    assert result.ok
    assert result.free_bytes > 0
    assert not (tmp_path / 'downloads' / '.uav-write-test').exists()


def test_schedule_and_eta_helpers_are_deterministic():
    now = 1_767_225_600.0
    scheduled = next_schedule_epoch('23:59', now=now)
    assert scheduled > now
    assert next_schedule_epoch('', now=now) == 0
    with pytest.raises(ValueError):
        next_schedule_epoch('25:00', now=now)
    assert format_eta(65) == '1m 5s'
    assert format_eta(3660) == '1h 1m'


def test_site_code_and_export_include_diagnostics(tmp_path):
    item = SimpleNamespace(
        job_id='job-1', state='未完成', name='MDHG0020 Full title',
        progress=42, speed='1 MB/s', eta='2m',
        url='https://missav.ai/en/mdhg0020', dest='downloads',
        resolution='720', filename_template='{code} {title}',
        error_code='network', error='timeout', created_at=1, updated_at=2,
        site='MissAV',
    )
    target = tmp_path / 'queue.json'

    export_items([item], str(target))
    record = json.loads(target.read_text(encoding='utf-8'))['items'][0]

    assert site_name(item.url) == 'MissAV'
    assert video_code(item.url, item.name) == 'MDHG0020'
    assert record['job_id'] == 'job-1'
    assert record['error_code'] == 'network'
    assert QueueAddSummary(requested=1, added=1).added == 1


def test_new_preferences_normalize_and_round_trip(tmp_path, monkeypatch):
    path = tmp_path / 'ui_prefs.json'
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(path))

    assert config.set_filename_template('{site} {code} {title}') == (
        '{site} {code} {title}')
    assert config.set_download_schedule('7:05') == '07:05'
    assert config.set_minimum_free_gb('1.25') == 1.25
    assert config.set_auto_retry_count(99) == 10
    assert config.set_notify_on_complete(False) is False
    assert config.set_speed_limit_mbps('12.5') == 12.5
    assert config.set_download_directory(str(tmp_path / 'downloads')) == (
        str(tmp_path / 'downloads'))

    assert config.get_filename_template() == '{site} {code} {title}'
    assert config.get_download_schedule() == '07:05'
    assert config.get_minimum_free_gb() == 1.25
    assert config.get_auto_retry_count() == 10
    assert config.get_notify_on_complete() is False
    assert config.get_speed_limit_mbps() == 12.5
    assert config.get_download_directory() == str(tmp_path / 'downloads')


def test_notification_preference_parses_string_booleans(tmp_path, monkeypatch):
    path = tmp_path / 'ui_prefs.json'
    monkeypatch.setattr(config, '_ui_prefs_path', lambda: str(path))

    assert config.set_notify_on_complete('false') is False
    assert config.get_notify_on_complete() is False
    assert config.set_notify_on_complete('yes') is True
    assert config.get_notify_on_complete() is True


def test_pause_and_resume_a_pending_download(monkeypatch):
    manager = DownloadManager(max_concurrent=1)
    started = []
    monkeypatch.setattr(manager, '_start_download_thread', started.append)
    manager.add_item('first', dest='download')
    manager.enqueue('first', 'download')
    paused = manager.add_item('second', dest='download')
    manager.enqueue('second', 'download')

    assert manager.pause('second')
    assert paused.state == '已暫停'
    assert manager.pending_count == 0
    assert manager.resume('second')
    assert manager.pending_count == 1


def test_pause_active_preserves_chunks_and_finishes_as_paused(monkeypatch):
    manager = DownloadManager(max_concurrent=1)
    item = manager.add_item('active', state='下載中', dest='download')
    task = _DownloadTask('active', 'download', manager._cancel_epoch)
    cleanup_values = []
    task.job = SimpleNamespace(
        cancel_download=lambda cleanup=True: cleanup_values.append(cleanup))
    manager._active['active'] = task
    monkeypatch.setattr(manager, '_start_download_thread', lambda _task: None)

    assert manager.pause('active')
    manager._complete_download(task, '已取消')

    assert cleanup_values == [False]
    assert item.state == '已暫停'
    assert manager.active_count == 0


def test_future_schedule_waits_until_due(monkeypatch):
    now = [1000.0]
    manager = DownloadManager(max_concurrent=1)
    started = []
    monkeypatch.setattr(browse.time, 'time', lambda: now[0])
    monkeypatch.setattr(manager, '_arm_schedule_locked', lambda: None)
    monkeypatch.setattr(manager, '_start_download_thread', started.append)
    item = manager.add_item('scheduled', dest='download')

    assert manager.enqueue('scheduled', 'download', not_before=1100)
    assert item.state == '已排程'
    assert manager.pending_count == 1
    assert started == []

    now[0] = 1100
    manager._try_next()
    assert len(started) == 1
    assert manager.active_count == 1


def test_transient_failure_uses_bounded_backoff(monkeypatch):
    now = [1000.0]
    manager = DownloadManager(auto_retry_count_getter=lambda: 2)
    monkeypatch.setattr(browse.time, 'time', lambda: now[0])
    monkeypatch.setattr(manager, '_arm_schedule_locked', lambda: None)
    item = manager.add_item('retry', state='下載中', dest='download')
    task = _DownloadTask('retry', 'download', manager._cancel_epoch)
    manager._active['retry'] = task

    assert manager._schedule_transient_retry(task, 'connection timed out')
    assert item.state == '等待重試'
    assert item.retry_count == 1
    assert item.next_retry_at == 1002
    assert manager.pending_count == 1
    assert manager.active_count == 0
