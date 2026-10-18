"""Testable download-workflow helpers shared by the desktop UI.

This module deliberately has no Tk dependency.  Queue import/export, filtering,
error classification and storage checks can therefore be verified without
constructing a window and can later be reused by another UI.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
TERMINAL_STATES = frozenset({
    '已下載', '未偵測到日語語音', '已取消', '網址錯誤',
})
ACTIVE_STATES = frozenset({
    '準備中', '下載中', '字幕準備中', '字幕模型準備中',
    '字幕辨識中', '字幕翻譯中',
})
QUEUED_STATES = frozenset({'等待中', '等待重試', '已排程'})
FAILED_STATES = frozenset({'未完成', '封鎖/解析失敗', '網址錯誤'})
DONE_STATES = frozenset({'已下載', '未偵測到日語語音'})
PAUSED_STATES = frozenset({'已暫停', '已取消'})


@dataclass(frozen=True)
class QueueAddSummary:
    """Outcome shown after adding or starting a batch."""

    requested: int = 0
    added: int = 0
    duplicates: int = 0
    unsupported: int = 0
    started: int = 0


@dataclass(frozen=True)
class StoragePreflight:
    ok: bool
    path: str
    free_bytes: int = 0
    error: str = ''


@dataclass(frozen=True)
class DownloadProblem:
    code: str
    retryable: bool


def extract_urls(text: object, *, max_urls: int = 5000) -> list[str]:
    """Extract unique HTTP(S) URLs while preserving user-visible order."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.finditer(str(text or '')):
        url = match.group(0).rstrip(".,;:!?)]}'\"")
        if len(url) > 2048 or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max_urls:
            break
    return urls


def load_url_file(path: str, *, max_urls: int = 5000) -> list[str]:
    """Read URLs from UTF-8 text, CSV, or JSON exports."""
    source = Path(path)
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('URL import file is too large')
    suffix = source.suffix.lower()
    if suffix == '.json':
        raw = json.loads(source.read_text(encoding='utf-8-sig'))
        if isinstance(raw, dict):
            raw = raw.get('items', raw.get('urls', []))
        values = []
        for item in raw if isinstance(raw, list) else []:
            values.append(item.get('url', '') if isinstance(item, dict) else item)
        return extract_urls('\n'.join(str(value) for value in values),
                            max_urls=max_urls)
    if suffix == '.csv':
        with source.open('r', encoding='utf-8-sig', newline='') as handle:
            values = []
            for row in csv.DictReader(handle):
                lowered = {str(key or '').strip().lower(): value
                           for key, value in row.items()}
                values.append(
                    lowered.get('url') or lowered.get('網址') or
                    lowered.get('source url') or '')
                if len(values) >= max_urls:
                    break
        return extract_urls('\n'.join(str(value) for value in values),
                            max_urls=max_urls)
    return extract_urls(
        source.read_text(encoding='utf-8-sig'), max_urls=max_urls)


def site_name(url: str) -> str:
    host = (urlsplit(str(url or '')).hostname or '').lower()
    if 'missav' in host:
        return 'MissAV'
    if host in {'jable.tv', 'www.jable.tv', 'fs1.app', 'www.fs1.app'}:
        return 'JableTV'
    if 'supjav' in host:
        return 'SupJav'
    if 'hanime1' in host:
        return 'Hanime1'
    return host or 'Local'


def video_code(url: str, title: str = '') -> str:
    """Return a concise code for secondary row metadata."""
    first = str(title or '').strip().split(maxsplit=1)
    if first and re.search(r'\d', first[0]):
        return first[0]
    path = urlsplit(str(url or '')).path.rstrip('/').rsplit('/', 1)[-1]
    return path or str(url or '')


def filter_items(items, filter_name: str):
    key = str(filter_name or 'all').strip().lower()
    if key == 'active':
        return [item for item in items if item.state in ACTIVE_STATES]
    if key == 'queued':
        return [item for item in items if item.state in QUEUED_STATES]
    if key == 'failed':
        return [item for item in items if item.state in FAILED_STATES]
    if key == 'done':
        return [item for item in items if item.state in DONE_STATES]
    if key == 'paused':
        return [item for item in items if item.state in PAUSED_STATES]
    return list(items)


def classify_download_problem(error: object, state: str = '') -> DownloadProblem:
    value = str(error or '').lower()
    if state == '網址錯誤' or 'unsupported' in value or '不支援' in value:
        return DownloadProblem('unsupported_url', False)
    if ('no space' in value or 'disk full' in value or
            'not enough free' in value or 'enospc' in value):
        return DownloadProblem('disk_full', False)
    if ('permission' in value or 'access is denied' in value or
            'permission denied' in value):
        return DownloadProblem('permission', False)
    if ('ffmpeg' in value and ('not found' in value or 'missing' in value)):
        return DownloadProblem('ffmpeg_missing', False)
    if (state == '封鎖/解析失敗' or 'cloudflare' in value or
            'blocked' in value or '429' in value or '403' in value):
        return DownloadProblem('blocked', True)
    if ('timeout' in value or 'timed out' in value or
            'connection' in value or 'reset by peer' in value or
            'temporarily unavailable' in value):
        return DownloadProblem('network', True)
    if ('not found' in value or '404' in value or 'removed' in value):
        return DownloadProblem('video_missing', False)
    if state in FAILED_STATES or value:
        return DownloadProblem('parse_or_unknown', True)
    return DownloadProblem('', False)


def storage_preflight(
        destination: str, *, minimum_free_bytes: int = 512 * 1024 * 1024,
) -> StoragePreflight:
    """Create/check a destination before a batch is allowed to start."""
    path = os.path.abspath(destination or 'download')
    try:
        os.makedirs(path, exist_ok=True)
        if not os.path.isdir(path):
            return StoragePreflight(False, path, error='not_a_directory')
        descriptor, probe = tempfile.mkstemp(
            prefix='.uav-write-test-', dir=path)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                descriptor = -1
                handle.write(b'uav')
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.remove(probe)
            except FileNotFoundError:
                pass
        free = int(shutil.disk_usage(path).free)
        if free < max(0, int(minimum_free_bytes)):
            return StoragePreflight(False, path, free, 'low_space')
        return StoragePreflight(True, path, free)
    except OSError as exc:
        return StoragePreflight(False, path, error=str(exc))


def format_eta(seconds: object) -> str:
    try:
        remaining = max(0, int(float(seconds)))
    except (TypeError, ValueError, OverflowError):
        return ''
    if remaining >= 86400:
        return f'{remaining // 86400}d {(remaining % 86400) // 3600}h'
    if remaining >= 3600:
        return f'{remaining // 3600}h {(remaining % 3600) // 60}m'
    if remaining >= 60:
        return f'{remaining // 60}m {remaining % 60}s'
    return f'{remaining}s'


def next_schedule_epoch(value: object, *, now: float | None = None) -> float:
    """Return the next local HH:MM occurrence; blank means immediately."""
    raw = str(value or '').strip()
    current = time.time() if now is None else float(now)
    if not raw:
        return 0.0
    match = re.fullmatch(r'([01]?\d|2[0-3]):([0-5]\d)', raw)
    if not match:
        raise ValueError('Schedule must use HH:MM')
    local = time.localtime(current)
    candidate = time.mktime((
        local.tm_year, local.tm_mon, local.tm_mday,
        int(match.group(1)), int(match.group(2)), 0,
        local.tm_wday, local.tm_yday, local.tm_isdst,
    ))
    if candidate <= current:
        candidate += 86400
    return candidate


def export_items(items, path: str) -> None:
    """Export a user-readable queue/history snapshot to CSV or JSON."""
    fields = (
        'job_id', 'state', 'name', 'site', 'code', 'progress', 'speed',
        'eta', 'url', 'destination', 'resolution', 'filename_template',
        'error_code', 'error', 'created_at', 'updated_at',
    )
    records = []
    for item in items:
        record = {
            'job_id': getattr(item, 'job_id', ''),
            'state': getattr(item, 'state', ''),
            'name': getattr(item, 'name', ''),
            'site': getattr(item, 'site', '') or site_name(item.url),
            'code': video_code(item.url, getattr(item, 'name', '')),
            'progress': getattr(item, 'progress', 0),
            'speed': getattr(item, 'speed', ''),
            'eta': getattr(item, 'eta', ''),
            'url': getattr(item, 'url', ''),
            'destination': getattr(item, 'dest', ''),
            'resolution': getattr(item, 'resolution', ''),
            'filename_template': getattr(item, 'filename_template', ''),
            'error_code': getattr(item, 'error_code', ''),
            'error': getattr(item, 'error', ''),
            'created_at': getattr(item, 'created_at', 0),
            'updated_at': getattr(item, 'updated_at', 0),
        }
        records.append(record)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.tmp')
    if destination.suffix.lower() == '.json':
        temporary.write_text(
            json.dumps({'schema': 1, 'items': records}, ensure_ascii=False,
                       indent=2),
            encoding='utf-8',
        )
    else:
        with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(temporary, destination)
