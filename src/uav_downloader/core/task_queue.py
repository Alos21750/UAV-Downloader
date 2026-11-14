"""GUI-independent download and subtitle task queues.
Legacy Browser imports re-export these types; new frontends use this module.
"""
import csv
import inspect
import re
import os
import threading
from uav_downloader.core.stop import StopContext, StopReason, DownloadStopped, execution_scope, stop_scope
import time
import uuid
from uav_downloader.apps.downloads import (
    ACTIVE_STATES, DONE_STATES, QUEUED_STATES, STOPPING_STATES,
    classify_download_problem, format_eta, site_name, recovered_state,
)
from dataclasses import dataclass, fields
from uav_downloader.core import config
from uav_downloader.core.storage import StorageWriteError
from uav_downloader import sites as M3U8Sites
from uav_downloader.core.queue_store import write_csv_snapshot
from uav_downloader.core.video_identity import normalize_source_subtitle_evidence, trusted_chinese_subtitle_evidence
from uav_downloader.sites.base import MirrorsBlockedError
from uav_downloader.i18n.locales import T
from uav_downloader.subtitles.engine import SubtitleCancelled, generate_subtitles, normalize_subtitle_mode
from uav_downloader.subtitles.errors import translation_failure_message
SLOW_REQUEUE_GRACE_SECONDS = 60
SLOW_REQUEUE_MAX_ATTEMPTS = 3
DEFAULT_CONCURRENT = 2
MAX_CONCURRENT = 32
MAX_PERSIST_ROWS = 1000
MAX_VISIBLE_ROWS = 200
HARD_LOAD_LIMIT = 5000
ERR_BLOCKED = '__cf_blocked__'
@dataclass(frozen=True)
class DownloadSnapshot:
    url: str
    name: str
    state: str
    progress: int
    speed: str
    error: str
    dest: str
    action: str
    local: bool = False
    auto_requeues: int = 0
    job_id: str = ''
    site: str = ''
    resolution: str = ''
    filename_template: str = ''
    thumbnail: str = ''
    eta: str = ''
    error_code: str = ''
    retry_count: int = 0
    next_retry_at: float = 0
    not_before: float = 0
    created_at: float = 0
    updated_at: float = 0
    # Upstream has no byte/transfer telemetry. Unknown stays unknown; displaying
    # the latest virtualized row must not invent measurements or migrate a new
    # downloader protocol.
    downloaded_bytes: int = -1
    total_bytes: int = -1
    file_bytes: int = -1
    size_kind: str = 'unknown'
    segments_done: int = -1
    segments_total: int = -1
    size_recorded: bool = False
    source_kind: str = ''
    source_fallback: bool = False
    resume_hint: str = ''
    resume_requested: bool = False
    source_height: int = 0
    video_width: int = 0
    video_height: int = 0

    @property
    def can_cancel(self):
        return self.state in ACTIVE_STATES | QUEUED_STATES | {'已暫停'}

_STATE_PRIORITY = {
    '取消中': 0,
    '暫停中': 0,
    '停止中': 0,
    '重新排隊中': 0,
    '下載中': 0,
    '字幕準備中': 1,
    '字幕模型準備中': 1,
    '字幕辨識中': 2,
    '字幕翻譯中': 3,
    '準備中': 4,
    '等待中': 5,
    '等待重試': 5,
    '已排程': 5,
    '已暫停': 6,
    '未完成': 6,
    '封鎖/解析失敗': 7,
    '網址錯誤': 8,
    '未偵測到日語語音': 9,
    '已下載': 9,
    '已取消': 10,
}
_SUBTITLE_STATE_BY_STAGE = {
    'queued': '字幕準備中',
    'runtime': '字幕準備中',
    'model': '字幕準備中',
    'translation_model': '字幕模型準備中',
    'audio': '字幕準備中',
    'transcribe_ja': '字幕辨識中',
    'translate_en': '字幕翻譯中',
    'translate_zh': '字幕翻譯中',
}

def _visible_window(items, cap):
    ordered = sorted(
        enumerate(items),
        key=lambda pair: (_STATE_PRIORITY.get(pair[1].state, 8), pair[0]))
    return [item for _, item in ordered[:cap]]

def _select_persist(items, cap):
    terminal = {'已下載', '未偵測到日語語音', '已取消', '網址錯誤'}
    resumable = [i for i in items if i.state not in terminal]
    terminal_items = [i for i in items if i.state in terminal]
    budget = max(0, cap - len(resumable))
    kept_terminal = terminal_items[-budget:] if budget > 0 else []
    keep_ids = {id(i) for i in resumable} | {id(i) for i in kept_terminal}
    return [i for i in items if id(i) in keep_ids]

def _download_row_action(item):
    """Return the primary action exposed by a download-row button."""
    if item.state in STOPPING_STATES:
        return ''
    if item.state in ACTIVE_STATES or item.state in QUEUED_STATES:
        return 'pause'
    if item.state == '已暫停':
        return 'resume'
    if (item.state in {'未完成', '封鎖/解析失敗', '已取消'} or
            (item.state == '已下載' and bool(item.error))):
        return 'retry'
    return ''

class _LocalSubtitleJob:
    """Job stand-in that lets an existing local video reuse _run_subtitle."""
    __slots__ = (
        '_path', '_cancel_job',
    )
    def __init__(self, path: str):
        self._path = path
        self._cancel_job = False
    def _get_video_savename(self) -> str:
        return self._path
# ── Download Manager ────────────────────────────────────────────────
class DownloadItem:
    __slots__ = (
        'url', 'name', 'state', 'progress',
        'speed', 'error', 'dest', 'source_subtitle_evidence',
        'local', 'auto_requeues', 'downloaded_bytes', 'total_bytes',
        'file_bytes', 'size_kind', 'segments_done', 'segments_total',
        'size_recorded', 'job_id', 'site', 'resolution',
        'filename_template', 'thumbnail', 'eta', 'error_code',
        'retry_count', 'next_retry_at', 'not_before', 'created_at',
        'updated_at', '_stop_result',
    )
    def __init__(
            self, url: str, name: str = '', state: str = '', dest: str = '',
            source_subtitle_evidence=(), local: bool = False,
            job_id: str = '', site: str = '', resolution: str = '',
            filename_template: str = '', thumbnail: str = '',
            not_before: float = 0, created_at: float = 0):
        self.url = url
        self.name = name or url.rstrip('/').split('/')[-1]
        self.state = state
        self._stop_result = None
        self.progress = 0
        self.speed = ''
        self.error = ''
        self.dest = dest or ''
        self.auto_requeues = 0
        candidate_job_id = str(job_id or '').strip()
        self.job_id = (
            candidate_job_id
            if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', candidate_job_id)
            else uuid.uuid4().hex)
        self.site = str(site or site_name(url))[:80]
        candidate_resolution = str(
            resolution or config.get_resolution_pref()).strip().lower()
        self.resolution = (
            candidate_resolution
            if candidate_resolution in config.VALID_RESOLUTION_PREFS
            else config.get_resolution_pref())
        self.filename_template = config.normalize_filename_template(
            filename_template or config.get_filename_template())
        self.thumbnail = str(thumbnail or '')[:2048]
        self.eta = ''
        self.error_code = ''
        self.retry_count = 0
        self.next_retry_at = 0.0
        self.not_before = max(0.0, float(not_before or 0))
        self.created_at = float(created_at or time.time())
        self.updated_at = self.created_at
        self.source_subtitle_evidence = trusted_chinese_subtitle_evidence({
            'url': url,
            '_source_subtitle_evidence': source_subtitle_evidence,
        })
        # An existing local video queued for subtitles only: never
        # downloaded, requeued or persisted.
        self.local = local
        self.reset_sizes()


    def reset_sizes(self):
        self.downloaded_bytes = self.total_bytes = self.file_bytes = -1
        self.segments_done = self.segments_total = -1
        self.size_kind = 'unknown'
        self.size_recorded = False

class _DownloadTask:
    """One URL moving through the download and optional subtitle queues."""
    __slots__ = (
        'url', 'dest', 'epoch', 'job',
        'subtitle_mode', 'source_subtitle_evidence', 'cancelled', 'download_started',
        'slow_requeue_checked', 'prev_total', 'transfer_complete', 'transferred_bytes',
        'window_bytes', 'window_started', 'last_done', 'monitor_stop',
        'monitor_thread', 'monitor_unit', 'resolution', 'filename_template',
        'retry_count', 'not_before', 'pause_requested', 'job_id', 'stop',
    )
    def __init__(
            self, url: str, dest: str, epoch: int,
            source_subtitle_evidence=(), resolution: str = '',
            filename_template: str = '', retry_count: int = 0,
            not_before: float = 0, job_id: str = ''):
        self.url = url
        self.dest = dest
        self.epoch = epoch
        self.job = None
        self.subtitle_mode = 'none'
        self.source_subtitle_evidence = (
            normalize_source_subtitle_evidence(
                source_subtitle_evidence))
        self.stop = StopContext(managed=True)
        self.cancelled = self.stop.requested
        self.download_started = None
        self.slow_requeue_checked = False
        self.prev_total = None
        self.transfer_complete = False
        self.transferred_bytes = 0.0
        self.window_bytes = 0.0
        self.window_started = None
        self.last_done = 0
        self.monitor_stop = threading.Event()
        self.monitor_thread = None
        self.monitor_unit = None
        self.resolution = str(resolution or '')
        self.filename_template = str(filename_template or '')
        self.retry_count = max(0, int(retry_count or 0))
        self.not_before = max(0.0, float(not_before or 0))
        self.pause_requested = False
        self.job_id = job_id


class DownloadManager:
    """Thread-safe manager with separate download and subtitle queues."""
    def __init__(self, on_update=None, max_concurrent: int = DEFAULT_CONCURRENT,
                 subtitle_mode_getter=None, slow_requeue_kbps_getter=None,
                 auto_retry_count_getter=None):
        self._on_update = on_update
        self._subtitle_mode_getter = subtitle_mode_getter or config.get_subtitle_pref
        self._slow_requeue_kbps_getter = (
            slow_requeue_kbps_getter or config.get_slow_requeue_kbps)
        self._auto_retry_count_getter = (
            auto_retry_count_getter or config.get_auto_retry_count)
        self._pending: list[_DownloadTask] = []
        self._active: dict[str, _DownloadTask] = {}
        self._subtitle_pending: list[_DownloadTask] = []
        self._subtitle_active: dict[str, _DownloadTask] = {}
        self._items: dict[str, DownloadItem] = {}
        # Active tasks requested for restart stay registered until their old
        # worker reaches _complete_download().  Only then is the replacement
        # inserted at the front of the queue, preventing two workers from
        # touching the same temporary files at once.
        self._restart_pending: dict[
            str, tuple[_DownloadTask, str, threading.Event]] = {}
        self._cleanup_inflight: dict[_DownloadTask, int] = {}
        self._deferred_completions = {}
        self._deferred_subtitles = {}
        self._worker_threads = {}
        # RLock: enqueue() and cancel_all() call _set_state() while holding
        # the lock — a plain Lock would deadlock the caller (often the main
        # GUI thread, freezing the app).
        self._lock = threading.RLock()
        self._max_concurrent = max(
            1, min(int(max_concurrent), MAX_CONCURRENT))
        self._prep_sem = threading.Semaphore(1)
        self._cancel_epoch = 0
        self._schedule_timer = None
        self._schedule_deadline = 0.0

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent
    @max_concurrent.setter
    def max_concurrent(self, value: int):
        self._max_concurrent = max(1, min(value, MAX_CONCURRENT))
        for _ in range(self._max_concurrent):
            self._try_next()
    def add_item(
            self, url: str, name: str = '', state: str = '', dest: str = '',
            source_subtitle_evidence=(), *, resolution: str = '',
            filename_template: str = '', thumbnail: str = '',
            not_before: float = 0, job_id: str = '', site: str = '',
            created_at: float = 0):
        with self._lock:
            if url not in self._items:
                self._items[url] = DownloadItem(
                    url, name, state, dest, source_subtitle_evidence,
                    job_id=job_id, site=site, resolution=resolution,
                    filename_template=filename_template,
                    thumbnail=thumbnail, not_before=not_before,
                    created_at=created_at)
            else:
                item = self._items[url]
                # Refresh stale/persisted labels from the card the user just
                # selected.  Older queue entries often contained only a code,
                # even though the current MissAV result has a useful title.
                if name:
                    item.name = name
                if dest:
                    item.dest = dest
                if resolution:
                    candidate = str(resolution).strip().lower()
                    if candidate in config.VALID_RESOLUTION_PREFS:
                        item.resolution = candidate
                if filename_template:
                    item.filename_template = config.normalize_filename_template(
                        filename_template)
                if thumbnail:
                    item.thumbnail = str(thumbnail)
                if not_before:
                    item.not_before = max(0.0, float(not_before))
                item.updated_at = time.time()
                evidence = set(item.source_subtitle_evidence)
                evidence.update(trusted_chinese_subtitle_evidence({
                    'url': url,
                    '_source_subtitle_evidence':
                        source_subtitle_evidence,
                }))
                item.source_subtitle_evidence = (
                    normalize_source_subtitle_evidence(evidence))
            return self._items[url]

    def get_items(self) -> list[DownloadItem]:
        with self._lock:
            return list(self._items.values())
    def snapshot_items(self):
        """Return immutable rows without ever waiting on a busy command worker.

        A contended queue returns its last complete snapshot for one more poll.
        This keeps slow enqueue/configuration work off the Qt event loop.
        """
        if not self._lock.acquire(blocking=False):
            return getattr(self, '_ui_snapshot', ())
        names = tuple(field.name for field in fields(DownloadSnapshot) if field.name in DownloadItem.__slots__)
        try:
            self._ui_snapshot = tuple(DownloadSnapshot(**{name: getattr(item, name) for name in names},
                                                       action=_download_row_action(item))
                                      for item in self._items.values())
            return self._ui_snapshot
        finally:
            self._lock.release()
    def remove_item(self, url: str):
        with self._lock:
            self._items.pop(url, None)
            restart = self._restart_pending.pop(url, None)
            if restart is not None:
                restart[2].set()
            removed = [task for task in self._pending if task.url == url]
            self._pending = [task for task in self._pending if task.url != url]
            removed_subtitles = [
                task for task in self._subtitle_pending if task.url == url]
            self._subtitle_pending = [
                task for task in self._subtitle_pending if task.url != url]
            active = self._active.get(url)
            active_subtitle = self._subtitle_active.get(url)
            contexts = removed + removed_subtitles
            if active is not None:
                contexts.append(active)
            if active_subtitle is not None:
                contexts.append(active_subtitle)
            for task in contexts:
                task.pause_requested = False
                task.stop.request(StopReason.CANCEL)
            self._register_cleanup_locked(contexts)
            self._arm_schedule_locked()
        self._finish_cancel(contexts)
        if removed_subtitles:
            self._try_next_subtitle()

    def restart(self, url: str, dest: str, automatic: bool = False,
                expected_task: _DownloadTask | None = None) -> bool:
        """Cancel one active video and safely put a fresh task first in line.
        `automatic` marks a self-triggered slow-download requeue; manual
        requeues (the default) reset the item's automatic-retry counter.
        """
        with self._lock:
            item = self._items.get(url)
            task = self._active.get(url)
            if (item is None or task is None or task.epoch != self._cancel_epoch
                    or not self._task_owns_item_locked(task)
                    or (item.state in STOPPING_STATES and item.state != '重新排隊中')):
                return False
            if automatic:
                from uav_downloader.sites.base import speed_limiter
                # An asynchronous decision must never apply to a replacement
                # sharing the URL, nor to a transfer that has entered remux.
                if (task is not expected_task or
                        self._context_cancelled_locked(task) or
                        task.transfer_complete or task.monitor_stop.is_set() or
                        getattr(task.job, '_progress_unit', None) in (
                            'preparing', 'postprocess') or
                        item.state != '下載中' or
                        bool(getattr(task.job, '_storage_error', None)) or
                        speed_limiter.limit_bps > 0 or
                        not self._slow_requeue_kbps_getter()):
                    return False
                if url in self._restart_pending:
                    return False
                if item.auto_requeues >= SLOW_REQUEUE_MAX_ATTEMPTS:
                    return False
                item.auto_requeues += 1
            if not automatic:
                item.auto_requeues = 0
            restart_dest = dest or task.dest or item.dest
            existing = self._restart_pending.get(url)
            if existing is not None and existing[0] is task:
                self._restart_pending[url] = (
                    task, restart_dest, existing[2])
                return True
            item.dest = restart_dest
            cleanup_done = threading.Event()
            self._restart_pending[url] = (
                task, restart_dest, cleanup_done)
            task.pause_requested = False
            task.stop.request(StopReason.RETRY, cleanup=True)
            task.monitor_stop.set()
            self._begin_item_stop_locked(task)
            self._register_cleanup_locked([task])
        # Keep cleanup outside the manager lock.  The replacement is created
        # by _complete_download only after start_download() has returned.
        try:
            self._finish_cancel([task], cleanup=True, finish_signal=cleanup_done)
        finally:
            # The old worker can finish as soon as its cancel flag changes.
            # This gate also waits for temporary-file cleanup to finish before
            # a replacement is allowed to touch the same destination.
            cleanup_done.set()
        return True

    def enqueue(self, url: str, dest: str, not_before=None):
        with self._lock:
            if not self._can_enqueue_locked(url):
                return False
            task = self._enqueue_locked(url, dest, not_before)
        if task is not None:
            self._start_download_thread(task)
        return True


    def _enqueue_locked(self, url, dest, not_before=None):
        if not self._can_enqueue_locked(url):
            return None
        item = self._items.get(url)
        if item:
            item.dest = dest or item.dest
        else:
            self._items[url] = DownloadItem(url, dest=dest)
            item = self._items[url]
        item.auto_requeues = 0  # a fresh enqueue starts a new session
        item.reset_sizes()
        item.retry_count = 0
        due = (
            item.not_before if not_before is None
            else max(0.0, float(not_before or 0)))
        if due <= time.time():
            due = 0.0
        item.not_before = due
        item.next_retry_at = 0.0
        task = _DownloadTask(
            url, dest, self._cancel_epoch,
            item.source_subtitle_evidence,
            resolution=item.resolution,
            filename_template=item.filename_template,
            not_before=due, job_id=item.job_id)
        if due:
            self._pending.append(task)
            self._set_state(url, '已排程', progress=0, error='')
            self._arm_schedule_locked()
        elif not self._url_retiring_locked(url) and len(self._active) < self._max_concurrent:
            self._active[url] = task
            return task
        else:
            self._pending.append(task)
            self._set_state(url, '等待中')

        return None
    @property
    def cancellation_epoch(self):
        with self._lock:
            return self._cancel_epoch
    def enqueue_listing_video(self, video, dest, epoch, *, resolution='',
                              filename_template='', not_before=None, cancel_check=None):
        """Validate producer epoch, add and dispatch under the same lock."""
        url = video.get('url', '')
        if not isinstance(url, str) or not M3U8Sites.VaildateUrl(url):
            return False
        evidence = trusted_chinese_subtitle_evidence(video)
        with self._lock:
            if (epoch != self._cancel_epoch or url in self._items
                    or (cancel_check is not None and cancel_check())):
                return False
            self.add_item(url, name=video.get('title', ''), state='等待中', dest=dest,
                          source_subtitle_evidence=evidence, resolution=resolution,
                          filename_template=filename_template, not_before=not_before,
                          thumbnail=str(video.get('thumbnail') or ''),
                          site=str(video.get('_site') or video.get('site') or ''))
            start_task = self._enqueue_locked(url, dest, not_before)
        if start_task is not None:
            self._start_download_thread(start_task)
        return True
    def cancel_all(self, cleanup: bool = True, *, reason=StopReason.CANCEL):
        with self._lock:
            contexts = self._cancel_all_locked(reason=reason)
        self._finish_cancel(contexts, cleanup=cleanup)

    def _cancel_all_locked(self, reason=StopReason.CANCEL):
        self._cancel_epoch += 1
        if self._schedule_timer is not None:
            self._schedule_timer.cancel()
            self._schedule_timer = None
            self._schedule_deadline = 0.0
        for _task, _dest, cleanup_done in self._restart_pending.values():
            cleanup_done.set()
        self._restart_pending.clear()
        contexts = (list(self._pending) + list(self._subtitle_pending)
                    + list(self._active.values()) + list(self._subtitle_active.values()))
        self._pending.clear()
        self._subtitle_pending.clear()
        for task in contexts:
            task.pause_requested = False
            task.stop.request(reason)
            self._begin_item_stop_locked(task)
        self._register_cleanup_locked(contexts)
        return contexts

    def clear_all(self):
        with self._lock:
            contexts = self._cancel_all_locked()
            self._items.clear()
        # New-epoch work may arrive during slow cleanup. The old clear must
        # never remove its record or include it in the old cancellation set.
        self._finish_cancel(contexts, cleanup=True)

    def _register_cleanup_locked(self, contexts):
        for task in set(contexts):
            self._cleanup_inflight[task] = self._cleanup_inflight.get(task, 0) + 1

    def _finish_cancel(self, contexts, cleanup=True, finish_signal=None):
        """Keep old contexts registered until both worker and cleanup finish.

        A finishing worker defers its state transition instead of waiting on a
        thread which might itself be stopping that worker. Repeated cancels
        each retain their own cleanup reference.
        """
        completed = []
        try:
            self._cancel_contexts(contexts, cleanup=cleanup)
        finally:
            with self._lock:
                if finish_signal is not None:
                    finish_signal.set()
                for task in set(contexts):
                    count = self._cleanup_inflight.get(task, 0) - 1
                    if count > 0:
                        self._cleanup_inflight[task] = count
                    else:
                        self._cleanup_inflight.pop(task, None)
                        result = self._deferred_completions.pop(task, None)
                        if result is not None:
                            completed.append((task, result))
                        elif not self._context_inflight_locked(task):
                            task.stop.finish()
                for url in {task.url for task in contexts}:
                    self._settle_item_stop_locked(url)
            for task, (phase, state, progress, error) in completed:
                finish = self._complete_download if phase == 'download' else self._complete_subtitle
                finish(task, state, progress, error)
            self._try_next()
            self._try_next_subtitle()
    def _run(self, url: str, dest: str, epoch: int | None = None):
        """Compatibility entry point used by focused tests and older callers."""
        with self._lock:
            task = self._active.get(url)
            if task is None:
                item = self._items.get(url)
                task = _DownloadTask(
                    url, dest, self._cancel_epoch if epoch is None else epoch,
                    (
                        item.source_subtitle_evidence
                        if item is not None else ()), job_id=item.job_id if item is not None else '')
                self._active[url] = task
        self._run_download(task)
    def _run_download(self, task: _DownloadTask):
        try:
            with execution_scope(task.stop):
                self._run_download_body(task)
        except DownloadStopped:
            self._complete_download(task, '已取消')
        finally:
            self._drain_worker_completion(task)

    def _drain_worker_completion(self, task):
        with self._lock:
            if task.stop.active_count or self._cleanup_inflight.get(task, 0):
                return
            mode = self._deferred_subtitles.pop(task, None)
            result = self._deferred_completions.pop(task, None)
        if mode is not None:
            if not self._queue_subtitle(task, mode):
                self._complete_download(task, '已取消')
        elif result is not None:
            phase, state, progress, error = result
            finish = self._complete_download if phase == 'download' else self._complete_subtitle
            finish(task, state, progress, error)
        with self._lock:
            if not self._context_inflight_locked(task) and not self._cleanup_inflight.get(task, 0):
                task.stop.finish()

    def _run_download_body(self, task: _DownloadTask):
        url = task.url
        self._set_context_state(task, '準備中')
        try:
            if not task.stop.acquire(self._prep_sem, cancel_check=lambda: self._context_cancelled(task)):
                self._complete_download(task, '已取消')
                return
            try:
                if self._context_cancelled(task):
                    self._complete_download(task, '已取消')
                    return
                try:
                    with stop_scope(task.stop):
                        job = M3U8Sites.CreateSite(
                            url, task.dest,
                            resolution=task.resolution,
                            filename_template=task.filename_template)
                except TypeError as exc:
                    # Preserve compatibility with third-party site factories
                    # that still implement the v2 positional signature.
                    if 'unexpected keyword argument' not in str(exc):
                        raise
                    with stop_scope(task.stop):
                        job = M3U8Sites.CreateSite(url, task.dest)
                if (
                        job is not None
                        and hasattr(job, 'add_source_subtitle_evidence')):
                    job.add_source_subtitle_evidence(
                        task.source_subtitle_evidence)
                with self._lock:
                    if self._active.get(url) is task:
                        task.job = job
            finally:
                self._prep_sem.release()
            if self._context_cancelled(task):
                if job is not None:
                    try:
                        job._cancel_job = True
                    except Exception:
                        pass
                self._complete_download(task, '已取消')
                return
            if not job:
                self._complete_download(task, '網址錯誤')
                return
            if not job.is_url_vaildate():
                err = getattr(job, '_last_error', None)
                if isinstance(err, StorageWriteError):
                    raise err  # Preserve the storage-halt path, never retry I/O.
                if isinstance(err, MirrorsBlockedError):
                    error = ERR_BLOCKED
                else:
                    error = str(err) if err else T('parse_failed_short')
                if err is not None and self._schedule_transient_retry(task, error):
                    return
                self._complete_download(
                    task, '封鎖/解析失敗', error=error)
                return
            if self._context_cancelled(task):
                try:
                    job._cancel_job = True
                except Exception:
                    pass
                self._complete_download(task, '已取消')
                return
            name = job.target_name() or ''
            self._set_context_state(task, '下載中', name=name)
            job._progress_callback = (
                lambda d, t, s: self._on_context_progress(task, d, t, s))
            if self._context_cancelled(task):
                try:
                    job._cancel_job = True
                except Exception:
                    pass
                self._complete_download(task, '已取消')
                return
            self._start_slow_monitor(task)
            ok = job.start_download()
            task.monitor_stop.set()
            if ok is False and not job._cancel_job:
                raise Exception(T('parse_failed_short'))
            if job._cancel_job or self._context_cancelled(task):
                self._complete_download(task, '已取消')
                return
            self._record_file_size(task)
            mode = normalize_subtitle_mode(self._subtitle_mode_getter())
            if mode != 'none':
                if self._queue_subtitle(task, mode):
                    return
                if self._context_cancelled(task):
                    self._complete_download(task, '已取消')
                    return
            self._complete_download(task, '已下載', progress=100)
        except Exception as exc:
            print(f'[下載失敗] {url}\n  {exc}', flush=True)
            if isinstance(exc, StorageWriteError):
                # No automatic queue advance or cleanup writes on a device
                # that has just failed. The user must explicitly retry.
                self.cancel_all(cleanup=False, reason=StopReason.STORAGE_ERROR)
                with self._lock:
                    if self._task_owns_item_locked(task):
                        self._begin_item_stop_locked(task, result='未完成', error=str(exc))
                self._complete_download(task, '未完成', error=str(exc))
                return
            if self._context_cancelled(task):
                self._complete_download(task, '已取消')
            elif isinstance(exc, MirrorsBlockedError):
                if not self._schedule_transient_retry(task, ERR_BLOCKED):
                    self._complete_download(
                        task, '封鎖/解析失敗', error=ERR_BLOCKED)
            else:
                if not self._schedule_transient_retry(task, str(exc)):
                    self._complete_download(task, '未完成', error=str(exc))
        finally:
            task.monitor_stop.set()


    def _try_next(self):
        task = None
        with self._lock:
            candidates = len(self._pending)
            while (candidates > 0 and self._pending and
                   len(self._active) < self._max_concurrent):
                candidates -= 1
                candidate = self._pending.pop(0)
                if (candidate.cancelled.is_set() or
                        candidate.epoch != self._cancel_epoch):
                    candidate.cancelled.set()
                    self._set_state(candidate.url, '已取消')
                    continue
                if candidate.not_before > time.time():
                    self._pending.append(candidate)
                    continue
                if self._url_retiring_locked(candidate.url):
                    self._pending.append(candidate)
                    continue
                self._active[candidate.url] = candidate
                candidate.not_before = 0.0
                item = self._items.get(candidate.url)
                if item is not None:
                    item.not_before = 0.0
                    item.next_retry_at = 0.0
                task = candidate
                break
            self._arm_schedule_locked()
        if task is not None:
            self._start_download_thread(task)

    def _queue_subtitle(self, task: _DownloadTask, mode: str) -> bool:
        with self._lock:
            if (self._active.get(task.url) is not task or
                    self._context_cancelled_locked(task)):
                return False
            if task.stop.active_count:
                self._deferred_subtitles[task] = mode
                return True
            task.subtitle_mode = mode
            task.monitor_stop.set()
            self._active.pop(task.url, None)
            self._subtitle_pending.append(task)
            self._set_state(task.url, '字幕準備中', progress=0)
        # The video download slot is free before any subtitle work starts.
        self._try_next()
        self._try_next_subtitle()
        return True
    def _try_next_subtitle(self):
        task = None
        with self._lock:
            if self._subtitle_active:
                return
            while self._subtitle_pending:
                candidate = self._subtitle_pending.pop(0)
                if (candidate.cancelled.is_set() or
                        candidate.epoch != self._cancel_epoch):
                    candidate.cancelled.set()
                    self._set_state(candidate.url, '已取消')
                    continue
                self._subtitle_active[candidate.url] = candidate
                task = candidate
                break
        if task is not None:
            self._spawn_worker(self._run_subtitle, (task,), task=task, phase='subtitle')
    def _run_subtitle(self, task: _DownloadTask):
        try:
            with execution_scope(task.stop):
                self._run_subtitle_body(task)
        except DownloadStopped:
            self._complete_subtitle(task, '已取消')
        finally:
            self._drain_worker_completion(task)

    def _run_subtitle_body(self, task: _DownloadTask):
        job = task.job
        if isinstance(job, _LocalSubtitleJob):
            self._record_file_size(task)
        warning = ''
        completion_state = '已下載'
        def _subtitle_progress(stage, percent):
            state = _SUBTITLE_STATE_BY_STAGE.get(stage)
            if state:
                self._set_context_state(
                    task, state,
                    progress=percent if percent is not None else -1)
        try:
            if self._context_cancelled(task):
                raise SubtitleCancelled()
            evidence = set(task.source_subtitle_evidence)
            evidence_getter = getattr(
                job, 'source_subtitle_evidence', None)
            if callable(evidence_getter):
                evidence.update(normalize_source_subtitle_evidence(
                    evidence_getter()))
            evidence = normalize_source_subtitle_evidence(evidence)
            subtitle_kwargs = {
                'progress_callback': _subtitle_progress,
                'cancel_check': lambda: (
                    self._context_cancelled(task) or bool(job._cancel_job)),
            }
            if evidence:
                subtitle_kwargs['source_subtitle_evidence'] = evidence
            subtitle_result = generate_subtitles(
                job._get_video_savename(), task.subtitle_mode,
                **subtitle_kwargs)
            if (
                    subtitle_result.satisfied_by_source
                    and not subtitle_result.files):
                completion_state = '已下載'
                print(T('subtitle_source_chinese_skip'), flush=True)
            elif subtitle_result.no_speech:
                completion_state = '未偵測到日語語音'
            elif (
                    not subtitle_result.files
                    and not subtitle_result.satisfied_by_source):
                completion_state = '未完成'
                warning = T(
                    'subtitle_failed', error=T('subtitle_empty_result'))
        except SubtitleCancelled:
            task.cancelled.set()
        except StorageWriteError as exc:
            self.cancel_all(cleanup=False, reason=StopReason.STORAGE_ERROR)
            with self._lock:
                if self._task_owns_item_locked(task):
                    self._begin_item_stop_locked(task, result='未完成', error=str(exc))
            self._complete_subtitle(task, '未完成', error=str(exc))
            return
        except Exception as exc:
            warning = T(
                'subtitle_failed',
                error=translation_failure_message(exc))
        if (task.cancelled.is_set() or self._context_cancelled(task) or
                bool(getattr(job, '_cancel_job', False))):
            self._complete_subtitle(task, '已取消')
        else:
            self._complete_subtitle(
                task, completion_state, progress=100,
                error=warning if warning else None)
    def _start_download_thread(self, task: _DownloadTask):
        self._spawn_worker(self._run_download, (task,), task=task, phase='download')

    def _spawn_worker(self, target, args=(), *, task=None, phase='control', name=None):
        thread = threading.Thread(target=target, args=args, name=name or f'uav-{phase}', daemon=True)
        with self._lock:
            self._worker_threads = {t: owner for t, owner in self._worker_threads.items() if t.is_alive()}
            self._worker_threads[thread] = (task, phase)
            try:
                thread.start()
            except Exception as error:
                self._worker_threads.pop(thread, None)
                if task is not None and phase in {'download', 'subtitle'}:
                    finish = self._complete_download if phase == 'download' else self._complete_subtitle
                    finish(task, '未完成', error=str(error))
                raise
        return thread

    def wait_stopped(self, timeout=None):
        """Wait for owned workers and cleanup; a timeout never fakes completion."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        def remaining():
            return None if deadline is None else max(0.0, deadline - time.monotonic())
        while True:
            with self._lock:
                contexts = set(self._active.values()) | set(self._subtitle_active.values()) | set(self._cleanup_inflight)
                threads = dict(self._worker_threads)
                # Private recording dispatchers (and admission cancelled before
                # a worker launches) have no I/O owner. Retire them safely; a
                # later entry cannot reopen an acknowledged managed context.
                unstarted = [task for task in contexts if task.cancelled.is_set()
                    and not task.stop.active_count and not self._cleanup_inflight.get(task, 0)
                    and not any(t.is_alive() and owner[0] is task and owner[1] in {'download', 'subtitle'}
                                for t, owner in threads.items())]
            for task in unstarted:
                finish = self._complete_download if self._active.get(task.url) is task else self._complete_subtitle
                finish(task, '已取消')
            for task in contexts:
                if not task.stop.finished.wait(remaining()):
                    return False
            for thread in threads:
                if thread is threading.current_thread():
                    return False
                thread.join(remaining())
                if thread.is_alive():
                    return False
            with self._lock:
                self._worker_threads = {t: owner for t, owner in self._worker_threads.items() if t.is_alive()}
                if not (self._active or self._subtitle_active or self._pending or self._subtitle_pending
                        or self._cleanup_inflight or self._worker_threads):
                    return True
            if deadline is not None and remaining() <= 0:
                return False
    def _url_inflight_locked(self, url: str) -> bool:
        return (
            url in self._active
            or url in self._subtitle_active
            or any(task.url == url for task in self._pending)
            or any(task.url == url for task in self._subtitle_pending)
        )

    def _can_enqueue_locked(self, url):
        contexts = (*self._active.values(), *self._subtitle_active.values(),
                    *self._pending, *self._subtitle_pending)
        return not any(t.url == url and not t.cancelled.is_set() for t in contexts)

    def _url_retiring_locked(self, url):
        return (url in self._active or url in self._subtitle_active
                or any(t.url == url for t in self._cleanup_inflight))

    def _task_owns_item_locked(self, task):
        item = self._items.get(task.url)
        return item is not None and (not task.job_id or item.job_id == task.job_id)
    def _context_inflight_locked(self, task: _DownloadTask) -> bool:
        return (
            self._active.get(task.url) is task
            or self._subtitle_active.get(task.url) is task
            or any(candidate is task for candidate in self._pending)
            or any(candidate is task for candidate in self._subtitle_pending)
        )
    def _context_cancelled_locked(self, task: _DownloadTask) -> bool:
        return (
            task.cancelled.is_set()
            or task.epoch != self._cancel_epoch
            or (bool(task.job_id) and not self._task_owns_item_locked(task))
            or not self._context_inflight_locked(task)
        )
    def _context_cancelled(self, task: _DownloadTask) -> bool:
        with self._lock:
            return self._context_cancelled_locked(task)
    def _set_context_state(self, task: _DownloadTask, state: str,
                           name: str = '', progress: int = -1, error=None):
        with self._lock:
            if self._context_cancelled_locked(task):
                return
            self._set_state(
                task.url, state, name=name, progress=progress, error=error)
    def _owned_item_contexts_locked(self, item):
        contexts = set(self._active.values()) | set(self._subtitle_active.values()) | set(self._pending) | set(self._subtitle_pending) | set(self._cleanup_inflight)
        return [task for task in contexts if task.url == item.url and (not task.job_id or task.job_id == item.job_id)]

    def _begin_item_stop_locked(self, task, result=None, error=''):
        if not self._task_owns_item_locked(task):
            return
        item = self._items[task.url]
        reason = task.stop.reason or StopReason.CANCEL
        if result is not None and any(other is not task and other.epoch == self._cancel_epoch
                                      and not other.cancelled.is_set()
                                      for other in self._owned_item_contexts_locked(item)):
            return  # A late failure cannot stamp an explicitly queued new attempt.
        previous = item._stop_result
        if previous and StopContext._PRIORITY[previous[0]] >= StopContext._PRIORITY[reason] and result is None:
            return
        state = {StopReason.PAUSE: '暫停中', StopReason.RETRY: '重新排隊中',
                 StopReason.SHUTDOWN: '停止中', StopReason.STORAGE_ERROR: '停止中'}.get(reason, '取消中')
        terminal = result or ('已暫停' if reason == StopReason.PAUSE else '等待中' if reason == StopReason.RETRY else '未完成' if reason == StopReason.SHUTDOWN else '已取消')
        self._set_state(task.url, state, error=error)
        # This belongs to the record, so overlapping cleanup/completion of
        # several contexts cannot prematurely publish a different outcome.
        item._stop_result = (reason, terminal, error)

    def _settle_item_stop_locked(self, url):
        item = self._items.get(url)
        if item is None or item.state not in STOPPING_STATES or item._stop_result is None:
            return
        if self._owned_item_contexts_locked(item):
            return
        _reason, state, error = item._stop_result
        self._set_state(url, state, error=error)

    def _complete_download(self, task: _DownloadTask, state: str,
                           progress: int = -1, error=None):
        task.monitor_stop.set()
        with self._lock:
            if self._active.get(task.url) is not task:
                return
            if self._cleanup_inflight.get(task, 0) or task.stop.active_count:
                self._deferred_completions[task] = ('download', state, progress, error)
                return
            restart = self._restart_pending.get(task.url)
            cleanup_done = (
                restart[2]
                if restart is not None and restart[0] is task
                else None)
        if cleanup_done is not None and not cleanup_done.is_set():
            with self._lock:
                self._deferred_completions[task] = ('download', state, progress, error)
            return


        with self._lock:
            if self._active.get(task.url) is not task:
                return
            if self._cleanup_inflight.get(task, 0) or task.stop.active_count:
                self._deferred_completions[task] = ('download', state, progress, error)
                return
            restart = self._restart_pending.get(task.url)
            restart_requested = (
                restart is not None
                and restart[0] is task
                and task.epoch == self._cancel_epoch
                and self._task_owns_item_locked(task)
                and task.url in self._items)
            context_cancelled = self._context_cancelled_locked(task)
            self._active.pop(task.url, None)
            self._deferred_subtitles.pop(task, None)
            self._deferred_completions.pop(task, None)
            if restart_requested:
                self._restart_pending.pop(task.url, None)
                item = self._items[task.url]
                replacement = _DownloadTask(
                    task.url, restart[1], self._cancel_epoch,

                    item.source_subtitle_evidence,
                    resolution=item.resolution,
                    filename_template=item.filename_template, job_id=item.job_id)

                item.reset_sizes()
                self._pending.insert(0, replacement)
                self._set_state(
                    task.url, '等待中', progress=0, error='')
            else:
                self._restart_pending.pop(task.url, None)
                if task.pause_requested:
                    state, progress, error = '已暫停', -1, ''
                if context_cancelled and state != '已取消':
                    if not task.pause_requested:
                        state, progress, error = '已取消', -1, None
                if self._task_owns_item_locked(task):
                    item = self._items[task.url]
                    if (task.epoch == self._cancel_epoch and item._stop_result is None
                            and not self._owned_item_contexts_locked(item)):
                        self._set_state(task.url, state, progress=progress, error=error)
                    self._settle_item_stop_locked(task.url)
            task.stop.finish()
        self._try_next()


    def _complete_subtitle(self, task: _DownloadTask, state: str,
                           progress: int = -1, error=None):
        with self._lock:
            if self._subtitle_active.get(task.url) is not task:
                return
            if self._cleanup_inflight.get(task, 0) or task.stop.active_count:
                self._deferred_completions[task] = ('subtitle', state, progress, error)
                return
            if task.pause_requested:
                state, progress, error = '已暫停', -1, ''
            elif self._context_cancelled_locked(task) and state != '已取消':
                state, progress, error = '已取消', -1, None
            self._subtitle_active.pop(task.url, None)
            self._deferred_completions.pop(task, None)
            if self._task_owns_item_locked(task):
                item = self._items[task.url]
                if (task.epoch == self._cancel_epoch and item._stop_result is None
                        and not self._owned_item_contexts_locked(item)):
                    self._set_state(task.url, state, progress=progress, error=error)
                self._settle_item_stop_locked(task.url)
            task.stop.finish()
        self._try_next_subtitle()
        self._try_next()

    @staticmethod
    def _cancel_contexts(contexts, cleanup: bool = True):
        seen = set()
        for task in contexts:
            if id(task) in seen:
                continue
            seen.add(id(task))
            task.cancelled.set()
            task.monitor_stop.set()
            job = task.job
            task.stop.request(StopReason.PAUSE if task.pause_requested else task.stop.reason or StopReason.CANCEL,
                              cleanup=cleanup)
            effective_cleanup = cleanup and task.stop.reason != StopReason.STORAGE_ERROR
            if not job or not hasattr(job, 'cancel_download'):
                continue
            request_stop = getattr(job, 'request_stop', None)
            try:
                if callable(request_stop):
                    request_stop(task.stop.reason, cleanup=effective_cleanup)
            except Exception:
                pass
            cancel = job.cancel_download
            legacy_signature = False
            try:
                inspect.signature(cancel).bind(cleanup=effective_cleanup)
            except TypeError:
                legacy_signature = True
            except (ValueError, AttributeError):
                pass
            try:
                if legacy_signature:
                    cancel()
                else:
                    cancel(cleanup=effective_cleanup)
            except Exception:
                pass
            # cancel_download owns cleanup and defers it through its storage
            # session while writers are alive. Repeating cleanup here can
            # delete a legacy downloader's live cache without an exit ack.
    def _set_state(self, url: str, state: str, name: str = '', progress: int = -1, error=None):
        with self._lock:
            item = self._items.get(url)
            if item:
                item.state = state
                if state not in STOPPING_STATES:
                    item._stop_result = None
                if name:
                    item.name = name
                if progress >= 0:
                    item.progress = progress
                if error is not None:
                    item.error = error
                    item.error_code = classify_download_problem(
                        error, state).code
                elif state not in ('未完成', '封鎖/解析失敗'):
                    item.error = ''
                    item.error_code = ''
                if state != '下載中':
                    item.speed = ''
                    item.eta = ''
                if state in DONE_STATES:
                    item.retry_count = 0
                    item.next_retry_at = 0.0
                item.updated_at = time.time()

    def _on_progress(self, url: str, done: int, total: int, speed_bps: float):
        pct = min(100, max(0, int(done * 100 / total))) if total > 0 else 0
        spd = (f'{speed_bps / 1024:.0f} KB/s' if speed_bps < 1024 * 1024
               else f'{speed_bps / 1024 / 1024:.1f} MB/s')
        with self._lock:
            item = self._items.get(url)
            if item:
                item.progress = pct
                item.speed = spd
    def _on_context_progress(self, task: _DownloadTask, done: int,
                             total: int, speed_bps: float):
        with self._lock:
            if (self._active.get(task.url) is not task or
                    self._context_cancelled_locked(task)):
                return
            item = self._items.get(task.url)
            job = task.job
            unit = getattr(job, '_progress_unit', 'unknown')
            if item and unit in ('bytes', 'segments'):
                if item.size_kind != unit or done < task.last_done:
                    item.reset_sizes()
                item.size_kind = unit
                if unit == 'bytes':
                    item.downloaded_bytes = max(0, int(done))
                    item.total_bytes = int(total) if total > 0 else -1
                    self._on_progress(task.url, done, total, speed_bps)
                else:
                    full = max(int(total), len(getattr(job, '_tsList', ())))
                    item.segments_done = max(0, full - int(total) + int(done))
                    item.segments_total = full
                    item.downloaded_bytes = max(0, int(getattr(job, '_bytes_downloaded', 0)) + int(getattr(job, '_cached_bytes', 0)))
                    self._on_progress(task.url, item.segments_done, full, speed_bps)
            else:
                self._on_progress(task.url, done, total, speed_bps)
            now = time.monotonic()
            if task.download_started is None:
                task.download_started = now
                task.window_started = now
            source_changed = task.prev_total is not None and (
                    total != task.prev_total or done < task.last_done)
            if source_changed:
                # HLS -> direct MP4/source fallback starts a fresh byte window.
                task.download_started = now
                task.window_started = now
                task.window_bytes = 0.0
                task.transferred_bytes = 0.0
                task.slow_requeue_checked = False
            task.prev_total = total
            task.last_done = done
            task.transfer_complete = total > 0 and done >= total
            item = self._items.get(task.url)
            if (item is not None and done > 0 and total > done and
                    task.download_started is not None):
                elapsed = max(0.0, now - task.download_started)
                item.eta = format_eta(elapsed * (total - done) / done)
            elif item is not None and total > 0 and done >= total:
                item.eta = ''
            job = task.job
            if getattr(job, '_progress_unit', None) == 'bytes':
                task.transferred_bytes = float(done)
            elif hasattr(job, '_bytes_downloaded'):
                task.transferred_bytes = float(job._bytes_downloaded)
            else:
                # Compatibility for custom download jobs with only an average
                # speed callback. Production HLS/direct jobs use exact bytes.
                task.transferred_bytes = max(
                    task.transferred_bytes,
                    speed_bps * max(0.0, now - task.download_started))
            if source_changed:
                task.window_bytes = task.transferred_bytes
            should_restart = self._check_slow_requeue_locked(task, now)
        if should_restart:
            # Never restart/cancel synchronously here: this callback runs
            # inside the crawler's own worker pool (for HLS, its segment
            # ThreadPoolExecutor), and restart() waits on that same pool's
            # cleanup — a synchronous call would deadlock it.
            self._spawn_worker(self._restart_slow_download, (task,))


    def _record_file_size(self, task):
        # Completed output only; range downloads preallocate their .part file.
        try:
            size = os.path.getsize(task.job._get_video_savename())
        except (AttributeError, OSError, TypeError, ValueError):
            return
        with self._lock:
            if self._context_cancelled_locked(task):
                return
            item = self._items.get(task.url)
            if item:
                item.file_bytes = size
                item.size_recorded = False
    def snapshot_csv(self):
        """Copy all persisted values under the lock, without doing disk I/O."""
        with self._lock:
            items = _select_persist([item for item in self._items.values() if not item.local], MAX_PERSIST_ROWS)
            header = (
                '狀態', '名稱', '進度', '速度', '網址', '目標',
                '字幕來源證據',
                '已保存位元組', '總位元組', '檔案位元組', '大小類型', '已完成片段', '總片段',
                '工作 ID', '網站', '解析度', '檔名範本', '縮圖', '錯誤', '錯誤代碼', '重試次數', '排程時間', '建立時間', '更新時間',
            )
            return (header,) + tuple(
                (item.state, item.name, f'{item.progress}%', item.speed,
                 item.url, item.dest, '|'.join(item.source_subtitle_evidence),
                 item.downloaded_bytes, item.total_bytes, item.file_bytes, item.size_kind,
                 item.segments_done, item.segments_total, item.job_id, item.site, item.resolution, item.filename_template, item.thumbnail, item.error, item.error_code, item.retry_count, item.not_before, item.created_at, item.updated_at)
                for item in items)
    def save_csv(self, path: str):

        write_csv_snapshot(path, self.snapshot_csv())


    def load_csv(self, path: str):
        try:
            if not os.path.exists(path):
                return
            with open(path, 'r', encoding='utf-8') as f:
                for idx, row in enumerate(csv.DictReader(f)):
                    if idx >= HARD_LOAD_LIMIT:
                        break
                    url = row.get('網址', '')
                    if url:
                        stored_state = row.get('狀態', '')
                        state = recovered_state(stored_state)
                        def _number(name, default=0.0):
                            try:
                                return float(row.get(name, default) or default)
                            except (TypeError, ValueError):
                                return float(default)
                        item = self.add_item(
                            url, row.get('名稱', ''), state,
                            row.get('目標', ''),
                            row.get('字幕來源證據', ''),
                            resolution=row.get('解析度', ''),
                            filename_template=row.get('檔名範本', ''),
                            thumbnail=row.get('縮圖', ''),
                            job_id=row.get('工作 ID', ''),
                            site=row.get('網站', ''),
                            created_at=_number('建立時間'))
                        progress = (row.get('進度', '') or '').rstrip('%')
                        try:
                            item.progress = int(float(progress))
                        except (TypeError, ValueError):
                            pass
                        item.speed = '' if stored_state in ACTIVE_STATES or stored_state in QUEUED_STATES else row.get('速度', '') or ''

                        for field, column in [('downloaded_bytes', '已保存位元組'), ('total_bytes', '總位元組'),
                                              ('file_bytes', '檔案位元組'), ('segments_done', '已完成片段'),
                                              ('segments_total', '總片段')]:
                            try:
                                value = int(row.get(column, -1))
                                setattr(item, field, value if 0 <= value <= 2**63 - 1 else -1)
                            except (TypeError, ValueError):
                                pass
                        kind = row.get('大小類型', 'unknown')
                        item.size_kind = kind if kind in ('bytes', 'segments') else 'unknown'
                        item.size_recorded = item.file_bytes >= 0 or item.downloaded_bytes >= 0

                        item.error = row.get('錯誤', '') or ''
                        item.error_code = (
                            row.get('錯誤代碼', '') or
                            classify_download_problem(
                                item.error, item.state).code)
                        try:
                            item.retry_count = max(
                                0, int(row.get('重試次數', 0) or 0))
                        except (TypeError, ValueError):
                            item.retry_count = 0
                        item.updated_at = _number(
                            '更新時間', item.created_at)

            # Keep load memory bounded after the safety read limit. Python 3.7+
            # dict insertion order is the recency proxy for terminal items.
            kept = _select_persist(self.get_items(), MAX_PERSIST_ROWS)
            keep_urls = {item.url for item in kept}
            with self._lock:
                for url in list(self._items.keys()):
                    if url not in keep_urls:
                        self._items.pop(url, None)
        except (OSError, UnicodeDecodeError, csv.Error):
            try:
                os.replace(path, path + '.bak')
            except Exception:
                pass


    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)
    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
    @property
    def subtitle_active_count(self) -> int:
        with self._lock:
            return len(self._subtitle_active)
    @property
    def subtitle_pending_count(self) -> int:
        with self._lock:
            return len(self._subtitle_pending)
    def enqueue_local_subtitles(self, path: str, mode: str) -> bool:
        """Queue an existing local video for subtitle generation only."""
        mode = normalize_subtitle_mode(mode)
        if mode == 'none':
            return False
        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            return False
        with self._lock:
            if self._url_inflight_locked(abs_path):
                return False
            item = self._items.get(abs_path)
            if item is None:
                item = DownloadItem(
                    abs_path, name=os.path.basename(abs_path),
                    dest=os.path.dirname(abs_path), local=True)
                self._items[abs_path] = item
            else:
                item.local = True
            task = _DownloadTask(abs_path, item.dest, self._cancel_epoch, job_id=item.job_id)
            task.subtitle_mode = mode
            task.job = _LocalSubtitleJob(abs_path)
            self._subtitle_pending.append(task)
            self._set_state(abs_path, '字幕準備中', progress=0)
        self._try_next_subtitle()
        return True
    def _restart_slow_download(self, task: _DownloadTask) -> bool:
        accepted = self.restart(task.url, task.dest, automatic=True,
                                expected_task=task)
        if not accepted:
            with self._lock:
                # A user may switch the option off or apply a speed cap
                # between decision and cancellation. Keep monitoring this
                # transfer if that decision was superseded.
                if not self._context_cancelled_locked(task):
                    task.slow_requeue_checked = False
        return accepted
    def _start_slow_monitor(self, task: _DownloadTask):
        with self._lock:
            if self._context_cancelled_locked(task):
                return
            now = time.monotonic()
            task.download_started = task.window_started = now
            task.monitor_unit = getattr(task.job, '_progress_unit', None)
            task.monitor_thread = self._spawn_worker(self._monitor_slow_download, (task,),
                task=task, phase='monitor', name='uav-slow-download-monitor')
    def _monitor_slow_download(self, task: _DownloadTask):
        # One bounded watcher per active transfer; no manager-wide thread that
        # could keep a discarded manager alive. Completion/cancel wakes it.
        while not task.monitor_stop.wait(1.0):
            with self._lock:
                if (self._active.get(task.url) is not task or
                        self._context_cancelled_locked(task)):
                    return
                should_restart = self._check_slow_requeue_locked(
                    task, time.monotonic())
            if should_restart:
                if self._restart_slow_download(task):
                    return
    def _check_slow_requeue_locked(self, task: _DownloadTask,
                                   now: float) -> bool:
        """Evaluate successive 60-second byte windows, including no progress.
        Called under the manager lock. Callers restart outside it. The
        decision is advisory; restart rechecks identity and completion.
        """
        item = self._items.get(task.url)
        if (self._active.get(task.url) is not task or
                self._context_cancelled_locked(task) or item is None or
                item.state != '下載中' or task.transfer_complete or
                task.monitor_stop.is_set() or task.slow_requeue_checked or
                bool(getattr(task.job, '_storage_error', None))):
            return False
        unit = getattr(task.job, '_progress_unit', None)
        if unit in ('preparing', 'postprocess'):
            task.monitor_unit = unit
            task.window_started = task.download_started = None
            return False
        if unit in ('segments', 'bytes') and unit != task.monitor_unit:
            task.monitor_unit = unit
            task.window_started = task.download_started = now
            task.window_bytes = task.transferred_bytes
            return False
        if task.window_started is None:
            task.window_started = task.download_started
        if task.window_started is None:
            return False
        elapsed = now - task.window_started
        if elapsed < SLOW_REQUEUE_GRACE_SECONDS:
            return False
        speed_bps = max(
            0.0, task.transferred_bytes - task.window_bytes) / elapsed
        task.window_started = now
        task.window_bytes = task.transferred_bytes
        threshold_kbps = self._slow_requeue_kbps_getter()
        if not threshold_kbps:
            return False
        from uav_downloader.sites.base import speed_limiter
        if speed_limiter.limit_bps > 0:
            return False
        if speed_bps >= threshold_kbps * 1024:
            return False
        if item.auto_requeues < SLOW_REQUEUE_MAX_ATTEMPTS:
            task.slow_requeue_checked = True
            print(
                f'[慢速自動重排] {task.url} 速度 {speed_bps / 1024:.0f} KB/s '
                f'低於門檻 {threshold_kbps} KB/s，第 {item.auto_requeues + 1}/'
                f'{SLOW_REQUEUE_MAX_ATTEMPTS} 次自動重新排隊', flush=True)
            return True
        print(
            f'[慢速自動重排] {task.url} 已自動重試 {SLOW_REQUEUE_MAX_ATTEMPTS} '
            '次仍低速，停止自動重試並繼續下載', flush=True)
        item.error = T('slow_requeue_exhausted', n=SLOW_REQUEUE_MAX_ATTEMPTS)
        return False
    def _schedule_transient_retry(self, task: _DownloadTask, error: str) -> bool:
        problem = classify_download_problem(error, '未完成')
        with self._lock:
            item = self._items.get(task.url)
            retry_limit = max(0, int(self._auto_retry_count_getter()))
            if (not problem.retryable or item is None or
                    self._active.get(task.url) is not task or
                    task.retry_count >= retry_limit or
                    self._context_cancelled_locked(task)):
                return False
            self._active.pop(task.url, None)
            retry_count = task.retry_count + 1
            delay = min(60.0, float(2 ** retry_count))
            due = time.time() + delay
            item.retry_count = retry_count
            item.next_retry_at = due
            item.not_before = due
            replacement = _DownloadTask(
                task.url, task.dest, self._cancel_epoch,
                item.source_subtitle_evidence,
                resolution=item.resolution,
                filename_template=item.filename_template,
                retry_count=retry_count, not_before=due, job_id=item.job_id)
            self._pending.append(replacement)
            self._set_state(
                task.url, '等待重試', progress=0, error=error)
            self._arm_schedule_locked()
        self._try_next()
        return True

    def _arm_schedule_locked(self):
        deadlines = [
            task.not_before for task in self._pending
            if task.not_before > time.time() and not task.cancelled.is_set()
        ]
        deadline = min(deadlines) if deadlines else 0.0
        if self._schedule_timer is not None:
            if deadline and abs(deadline - self._schedule_deadline) < 0.5:
                return
            self._schedule_timer.cancel()
            self._schedule_timer = None
            self._schedule_deadline = 0.0
        if not deadline:
            return
        def _wake():
            with self._lock:
                self._schedule_timer = None
                self._schedule_deadline = 0.0
            for _ in range(self._max_concurrent):
                self._try_next()
        self._schedule_deadline = deadline
        self._schedule_timer = threading.Timer(
            max(0.05, deadline - time.time()), _wake)
        self._schedule_timer.daemon = True
        self._worker_threads[self._schedule_timer] = (None, 'schedule')
        self._schedule_timer.start()

    def pause_all(self) -> int:
        with self._lock:
            urls = [
                item.url for item in self._items.values()
                if item.state in ACTIVE_STATES or item.state in QUEUED_STATES
            ]
        return sum(1 for url in urls if self.pause(url))

    def resume(self, url: str, dest: str = '') -> bool:
        with self._lock:
            item = self._items.get(url)
            if item is None or item.state not in {'已暫停', '已取消'}:
                return False
            item.not_before = 0.0
            local = item.local
            item_dest = item.dest
        if local:
            return self.enqueue_local_subtitles(
                url, normalize_subtitle_mode(self._subtitle_mode_getter()))
        return bool(self.enqueue(url, dest or item_dest))

    def pause(self, url: str) -> bool:
        """Pause one queued/active item while preserving resumable chunks."""
        contexts = []
        with self._lock:
            item = self._items.get(url)
            if item is None or item.state in STOPPING_STATES:
                return False
            pending = [task for task in self._pending if task.url == url]
            if pending:
                self._pending = [
                    task for task in self._pending if task.url != url]
                for task in pending:
                    task.pause_requested = True
                    task.stop.request(StopReason.PAUSE)
                contexts.extend(pending)
            subtitle_pending = [
                task for task in self._subtitle_pending if task.url == url]
            if subtitle_pending:
                self._subtitle_pending = [
                    task for task in self._subtitle_pending
                    if task.url != url]
                for task in subtitle_pending:
                    task.pause_requested = True
                    task.stop.request(StopReason.PAUSE)
                contexts.extend(subtitle_pending)
            active = self._active.get(url)
            if active is not None:
                active.pause_requested = True
                active.stop.request(StopReason.PAUSE)
                active.monitor_stop.set()
                contexts.append(active)
            subtitle = self._subtitle_active.get(url)
            if subtitle is not None:
                subtitle.pause_requested = True
                subtitle.stop.request(StopReason.PAUSE)
                contexts.append(subtitle)
            if not contexts:
                if item.state in {'已下載', '未偵測到日語語音'}:
                    return False
                self._set_state(url, '已暫停', error='')
                return True
            for task in contexts:
                self._begin_item_stop_locked(task)
            self._register_cleanup_locked(contexts)
            self._arm_schedule_locked()
        self._finish_cancel(contexts, cleanup=False)
        self._try_next()
        return True

    def contains(self, url: str) -> bool:
        with self._lock:
            return url in self._items

    def activity_counts(self):
        if not self._lock.acquire(blocking=False):
            return getattr(self, '_ui_counts', (0, 0, 0, 0, self._max_concurrent))
        try:
            self._ui_counts = (len(self._active), len(self._pending),
                               len(self._subtitle_active), len(self._subtitle_pending),
                               self._max_concurrent)
            return self._ui_counts
        finally:
            self._lock.release()
