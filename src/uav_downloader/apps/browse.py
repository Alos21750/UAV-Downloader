#!/usr/bin/env python
# coding: utf-8
"""UAV Browser: interactive JableTV, MissAV, SupJav, and Hanime1 GUI."""

import os
import sys
import re
import io
import csv
import time
import shutil
import webbrowser
import threading
import concurrent.futures
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional
from urllib.parse import urlsplit

import customtkinter as ctk
import requests
from PIL import Image

from uav_downloader.core import config
from uav_downloader import sites as M3U8Sites
from uav_downloader.i18n import sites as site_i18n
from uav_downloader.core import updater
from uav_downloader.core.ssl import SharedSSLAdapter, get_shared_ssl_context
from uav_downloader.sites.jabletv import JableTVBrowser
from uav_downloader.sites.missav import MissAVBrowser
from uav_downloader.sites.supjav import SupJavBrowser
from uav_downloader.sites.hanime1 import (
    HANIME1_FILTER_TAG_GROUPS,
    Hanime1Browser,
    SiteHanime1,
)
from uav_downloader.sites.base import MirrorsBlockedError
from uav_downloader.core.config import headers
from uav_downloader.i18n.locales import T, set_lang, get_lang, ui_font, LANGUAGES, state_label
from uav_downloader.subtitles.engine import (
    SubtitleCancelled,
    generate_subtitles,
    normalize_recognition_quality,
    normalize_subtitle_mode,
)
from uav_downloader.ui.translation_settings import (
    open_translation_settings_dialog,
    translation_failure_message,
    translation_provider_summary,
)
from uav_downloader.core.video_identity import (
    normalize_source_subtitle_evidence,
    trusted_chinese_subtitle_evidence,
)
from uav_downloader.ui.theme import (
    ACCENT, ACCENT_HOVER, ACCENT_DIM,
    SUCCESS, SUCCESS_DIM, WARNING, WARNING_DIM, ERROR_C, ERROR_DIM,
    BG_DARK, BG_CARD, BG_CARD_HOVER, BG_INPUT, BG_HEADER, BG_SECTION,
    BG_SIDEBAR, BG_BADGE, TEXT_PRI, TEXT_SEC, TEXT_DIM, TEXT_LINK,
    BORDER, BORDER_HOVER, BORDER_CARD, WHITE, CARD_RADIUS, CONTROL_RADIUS,
    browse_columns_for_width,
)
from uav_downloader.metadata import APP_DISPLAY_NAMES, PRODUCT_NAME, VERSION

APP_VERSION = VERSION

# issue #24: startup breadcrumbs — no-op if crashlog unavailable
try:
    from uav_downloader.core.crashlog import breadcrumb as _crumb
except Exception:
    def _crumb(msg):
        pass

DEFAULT_CONCURRENT = 2
MAX_CONCURRENT = 32
SETTINGS_INLINE_HELP_WRAP = 620
MAX_VISIBLE_ROWS = 200
ROW_BUILD_BUDGET = 40
MAX_PERSIST_ROWS = 1000
HARD_LOAD_LIMIT = 5000
CSV_PATH = config.queue_csv_path()
ERR_BLOCKED = '__cf_blocked__'

SITES = {
    'JableTV': {'browser': JableTVBrowser},
    'MissAV': {'browser': MissAVBrowser},
    'SupJav': {'browser': SupJavBrowser},
    'Hanime1': {'browser': Hanime1Browser},
}


_STATE_PRIORITY = {
    '下載中': 0,
    '字幕準備中': 1,
    '字幕辨識中': 2,
    '字幕翻譯中': 3,
    '準備中': 4,
    '等待中': 5,
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
    """Return the retry action exposed by a download-row button."""
    if item.state in {'準備中', '下載中'}:
        return 'requeue'
    if (item.state in {'未完成', '封鎖/解析失敗', '已取消'} or
            (item.state == '已下載' and bool(item.error))):
        return 'retry'
    return ''


# ── Download Manager ────────────────────────────────────────────────
class DownloadItem:
    __slots__ = (
        'url', 'name', 'state', 'progress', 'speed', 'error', 'dest',
        'source_subtitle_evidence',
    )

    def __init__(
            self, url: str, name: str = '', state: str = '', dest: str = '',
            source_subtitle_evidence=()):
        self.url = url
        self.name = name or url.rstrip('/').split('/')[-1]
        self.state = state
        self.progress = 0
        self.speed = ''
        self.error = ''
        self.dest = dest or ''
        self.source_subtitle_evidence = trusted_chinese_subtitle_evidence({
            'url': url,
            '_source_subtitle_evidence': source_subtitle_evidence,
        })


class _DownloadTask:
    """One URL moving through the download and optional subtitle queues."""

    __slots__ = (
        'url', 'dest', 'epoch', 'job', 'subtitle_mode',
        'source_subtitle_evidence', 'cancelled',
    )

    def __init__(
            self, url: str, dest: str, epoch: int,
            source_subtitle_evidence=()):
        self.url = url
        self.dest = dest
        self.epoch = epoch
        self.job = None
        self.subtitle_mode = 'none'
        self.source_subtitle_evidence = (
            normalize_source_subtitle_evidence(
                source_subtitle_evidence))
        self.cancelled = threading.Event()


class DownloadManager:
    """Thread-safe manager with separate download and subtitle queues."""

    def __init__(self, on_update=None, max_concurrent: int = DEFAULT_CONCURRENT,
                 subtitle_mode_getter=None):
        self._on_update = on_update
        self._subtitle_mode_getter = subtitle_mode_getter or config.get_subtitle_pref
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
        # RLock: enqueue() and cancel_all() call _set_state() while holding
        # the lock — a plain Lock would deadlock the caller (often the main
        # GUI thread, freezing the app).
        self._lock = threading.RLock()
        self._max_concurrent = max(
            1, min(int(max_concurrent), MAX_CONCURRENT))
        self._prep_sem = threading.Semaphore(1)
        self._cancel_epoch = 0

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
            source_subtitle_evidence=()):
        with self._lock:
            if url not in self._items:
                self._items[url] = DownloadItem(
                    url, name, state, dest, source_subtitle_evidence)
            else:
                item = self._items[url]
                if dest:
                    item.dest = dest
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
                task.cancelled.set()
        self._cancel_contexts(contexts)
        if removed_subtitles:
            self._try_next_subtitle()

    def restart(self, url: str, dest: str) -> bool:
        """Cancel one active video and safely put a fresh task first in line."""
        with self._lock:
            item = self._items.get(url)
            task = self._active.get(url)
            if item is None or task is None:
                return False

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
            task.cancelled.set()
            self._set_state(url, '等待中', progress=0, error='')

        # Keep cleanup outside the manager lock.  The replacement is created
        # by _complete_download only after start_download() has returned.
        try:
            self._cancel_contexts([task], cleanup=True)
        finally:
            # The old worker can finish as soon as its cancel flag changes.
            # This gate also waits for temporary-file cleanup to finish before
            # a replacement is allowed to touch the same destination.
            cleanup_done.set()
        return True

    def enqueue(self, url: str, dest: str):
        start_task = None
        with self._lock:
            if self._url_inflight_locked(url):
                return
            item = self._items.get(url)
            if item:
                item.dest = dest or item.dest
            else:
                self._items[url] = DownloadItem(url, dest=dest)
                item = self._items[url]
            task = _DownloadTask(
                url, dest, self._cancel_epoch,
                item.source_subtitle_evidence)
            if len(self._active) < self._max_concurrent:
                self._active[url] = task
                start_task = task
            else:
                self._pending.append(task)
                self._set_state(url, '等待中')
        if start_task is not None:
            self._start_download_thread(start_task)

    def cancel_all(self, cleanup: bool = True):
        with self._lock:
            self._cancel_epoch += 1
            for _task, _dest, cleanup_done in self._restart_pending.values():
                cleanup_done.set()
            self._restart_pending.clear()
            pending = list(self._pending)
            pending_subtitles = list(self._subtitle_pending)
            self._pending.clear()
            self._subtitle_pending.clear()
            active = list(self._active.values())
            active_subtitles = list(self._subtitle_active.values())
            contexts = pending + pending_subtitles + active + active_subtitles
            for task in contexts:
                task.cancelled.set()
                self._set_state(task.url, '已取消')
        self._cancel_contexts(contexts, cleanup=cleanup)

    def clear_all(self):
        self.cancel_all()
        with self._lock:
            self._items.clear()

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
                        if item is not None else ()))
                self._active[url] = task
        self._run_download(task)

    def _run_download(self, task: _DownloadTask):
        url = task.url
        self._set_context_state(task, '準備中')
        try:
            self._prep_sem.acquire()
            try:
                if self._context_cancelled(task):
                    self._complete_download(task, '已取消')
                    return
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
                if isinstance(err, MirrorsBlockedError):
                    error = ERR_BLOCKED
                else:
                    error = str(err) if err else T('parse_failed_short')
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
            ok = job.start_download()
            if ok is False and not job._cancel_job:
                raise Exception(T('parse_failed_short'))
            if job._cancel_job or self._context_cancelled(task):
                self._complete_download(task, '已取消')
                return
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
            if self._context_cancelled(task):
                self._complete_download(task, '已取消')
            elif isinstance(exc, MirrorsBlockedError):
                self._complete_download(
                    task, '封鎖/解析失敗', error=ERR_BLOCKED)
            else:
                self._complete_download(task, '未完成', error=str(exc))

    def _try_next(self):
        task = None
        with self._lock:
            while self._pending and len(self._active) < self._max_concurrent:
                candidate = self._pending.pop(0)
                if (candidate.cancelled.is_set() or
                        candidate.epoch != self._cancel_epoch):
                    candidate.cancelled.set()
                    self._set_state(candidate.url, '已取消')
                    continue
                self._active[candidate.url] = candidate
                task = candidate
                break
        if task is not None:
            self._start_download_thread(task)

    def _queue_subtitle(self, task: _DownloadTask, mode: str) -> bool:
        with self._lock:
            if (self._active.get(task.url) is not task or
                    self._context_cancelled_locked(task)):
                return False
            task.subtitle_mode = mode
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
            threading.Thread(
                target=self._run_subtitle, args=(task,), daemon=True).start()

    def _run_subtitle(self, task: _DownloadTask):
        job = task.job
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
        threading.Thread(
            target=self._run_download, args=(task,), daemon=True).start()

    def _url_inflight_locked(self, url: str) -> bool:
        return (
            url in self._active
            or url in self._subtitle_active
            or any(task.url == url for task in self._pending)
            or any(task.url == url for task in self._subtitle_pending)
        )

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

    def _complete_download(self, task: _DownloadTask, state: str,
                           progress: int = -1, error=None):
        with self._lock:
            restart = self._restart_pending.get(task.url)
            cleanup_done = (
                restart[2]
                if restart is not None and restart[0] is task
                else None)
        if cleanup_done is not None:
            cleanup_done.wait()

        with self._lock:
            if self._active.get(task.url) is not task:
                return
            restart = self._restart_pending.get(task.url)
            restart_requested = (
                restart is not None
                and restart[0] is task
                and task.epoch == self._cancel_epoch
                and task.url in self._items)
            context_cancelled = self._context_cancelled_locked(task)
            self._active.pop(task.url, None)
            if restart_requested:
                self._restart_pending.pop(task.url, None)
                item = self._items[task.url]
                replacement = _DownloadTask(
                    task.url, restart[1], self._cancel_epoch,
                    item.source_subtitle_evidence)
                self._pending.insert(0, replacement)
                self._set_state(
                    task.url, '等待中', progress=0, error='')
            else:
                self._restart_pending.pop(task.url, None)
                if context_cancelled and state != '已取消':
                    state, progress, error = '已取消', -1, None
                self._set_state(
                    task.url, state, progress=progress, error=error)
        self._try_next()

    def _complete_subtitle(self, task: _DownloadTask, state: str,
                           progress: int = -1, error=None):
        with self._lock:
            if self._subtitle_active.get(task.url) is not task:
                return
            if self._context_cancelled_locked(task) and state != '已取消':
                state, progress, error = '已取消', -1, None
            self._set_state(
                task.url, state, progress=progress, error=error)
            self._subtitle_active.pop(task.url, None)
        self._try_next_subtitle()

    @staticmethod
    def _cancel_contexts(contexts, cleanup: bool = True):
        seen = set()
        for task in contexts:
            if id(task) in seen:
                continue
            seen.add(id(task))
            task.cancelled.set()
            job = task.job
            if not job or not hasattr(job, 'cancel_download'):
                continue
            try:
                job.cancel_download(cleanup=cleanup)
            except TypeError:
                try:
                    job.cancel_download()
                except Exception:
                    pass
            except Exception:
                pass
            if cleanup and hasattr(job, 'cleanup_temp'):
                try:
                    job.cleanup_temp()
                except Exception:
                    pass

    def _set_state(self, url: str, state: str, name: str = '', progress: int = -1, error=None):
        with self._lock:
            item = self._items.get(url)
            if item:
                item.state = state
                if name:
                    item.name = name
                if progress >= 0:
                    item.progress = progress
                if error is not None:
                    item.error = error
                elif state not in ('未完成', '封鎖/解析失敗'):
                    item.error = ''
                if state != '下載中':
                    item.speed = ''

    def _on_progress(self, url: str, done: int, total: int, speed_bps: float):
        if total <= 0:
            return
        pct = int(done * 100 / total)
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
            self._on_progress(task.url, done, total, speed_bps)

    def save_csv(self, path: str):
        with self._lock:
            items = list(self._items.values())
        # Python 3.7+ dict insertion order is used as the recency proxy for
        # capped terminal history; resumable items are never dropped.
        items = _select_persist(items, MAX_PERSIST_ROWS)
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow([
                '狀態', '名稱', '進度', '速度', '網址', '目標',
                '字幕來源證據',
            ])
            for item in items:
                w.writerow([item.state, item.name, f'{item.progress}%',
                            item.speed, item.url, item.dest,
                            '|'.join(item.source_subtitle_evidence)])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

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
                        state = row.get('狀態', '')
                        if state in (
                                '下載中', '準備中', '等待中',
                                '字幕準備中', '字幕辨識中', '字幕翻譯中'):
                            state = '未完成'
                        item = self.add_item(
                            url, row.get('名稱', ''), state,
                            row.get('目標', ''),
                            row.get('字幕來源證據', ''))
                        progress = (row.get('進度', '') or '').rstrip('%')
                        try:
                            item.progress = int(float(progress))
                        except (TypeError, ValueError):
                            pass
                        item.speed = row.get('速度', '') or ''
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


# ── Browse helper ────────────────────────────────────────────────────
def fetch_page_data(browser_cls, url: str) -> dict:
    """Fetch video list from a category/search URL. Returns dict with videos list."""
    try:
        videos = browser_cls.fetch_page(url)
        return {'videos': videos}
    except MirrorsBlockedError:
        raise
    except Exception as e:
        print(f'[瀏覽錯誤] {e}')
        return {'videos': []}


# ── Thumbnail loader ────────────────────────────────────────────────
_thumb_session: Optional[requests.Session] = None
_thumb_lock = threading.Lock()
_thumb_cache: dict = {}   # url -> PIL.Image (raw, not CTkImage; Tk root needed)
_thumb_cache_lock = threading.Lock()   # guards _thumb_cache mutation across the 4 worker threads
_THUMB_SIZE = (300, 169)  # readable 16:9 cards at the default three-column layout


def _get_thumb_session() -> requests.Session:
    global _thumb_session
    if _thumb_session is None:
        with _thumb_lock:
            if _thumb_session is None:
                s = requests.Session()
                s.mount('http://', requests.adapters.HTTPAdapter(pool_connections=8,
                                                                 pool_maxsize=32))
                s.mount('https://', SharedSSLAdapter(pool_connections=8,
                                                     pool_maxsize=32))
                _thumb_session = s
    return _thumb_session


def _thumbnail_request_context(url: str, site_key: str = ''):
    """Build thumbnail headers and a domain-scoped SupJav clearance jar."""
    request_headers = dict(headers)
    cookies = None
    try:
        parsed = urlsplit(str(url or ''))
        host = (parsed.hostname or '').lower().rstrip('.')
    except (TypeError, ValueError):
        return request_headers, cookies

    trusted_supjav = (
        str(site_key or '').lower() == 'supjav'
        and parsed.scheme.lower() == 'https'
        and host in {'supjav.com', 'www.supjav.com', 'img.supjav.com'}
    )
    if not trusted_supjav:
        return request_headers, cookies

    request_headers['Referer'] = 'https://supjav.com/'
    override = config.get_cf_override('supjav.com') or {}
    if override.get('ua'):
        request_headers['User-Agent'] = override['ua']
    if override.get('cookie'):
        cookies = requests.cookies.RequestsCookieJar()
        cookies.set(
            'cf_clearance', override['cookie'], domain='.supjav.com',
            path='/', secure=True)
    return request_headers, cookies


def _fetch_thumbnail(url: str, site_key: str = '') -> Optional[Image.Image]:
    """Download and decode a thumbnail; cached per-URL."""
    if not url:
        return None
    cached = _thumb_cache.get(url)
    if cached is not None:
        return cached
    request_headers, cookies = _thumbnail_request_context(url, site_key)
    attempts = [(request_headers, cookies)]
    if request_headers != headers or cookies is not None:
        # A saved browser context can expire.  Match the listing fetcher's
        # behavior by trying one ordinary request before declaring failure.
        attempts.append((dict(headers), None))

    for attempt_headers, attempt_cookies in attempts:
        request_kwargs = {
            'headers': attempt_headers,
            'timeout': 12,
            **config.proxy_request_kwargs(),
        }
        if attempt_cookies is not None:
            request_kwargs['cookies'] = attempt_cookies
        response = None
        try:
            response = _get_thumb_session().get(url, **request_kwargs)
            if response.status_code != 200:
                continue
            img = Image.open(io.BytesIO(response.content)).convert('RGB')
            img.thumbnail(_THUMB_SIZE, Image.LANCZOS)
        except Exception:
            continue
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        with _thumb_cache_lock:
            _thumb_cache[url] = img
            # Limit cache growth — under the lock so a concurrent insert can't resize the
            # dict mid-iteration (RuntimeError: dictionary changed size during iteration).
            if len(_thumb_cache) > 200:
                for k in list(_thumb_cache.keys())[:40]:
                    _thumb_cache.pop(k, None)
        return img
    return None


# ── Main App ─────────────────────────────────────────────────────────
class ModernApp(ctk.CTk):
    def __init__(self, url: str = '', dest: str = 'download', lang: str = 'en'):
        super().__init__()

        get_shared_ssl_context()

        config.load_cf_overrides()
        self._lang_code_by_name = {name: code for code, name in LANGUAGES}
        self._lang_name_by_code = {code: name for code, name in LANGUAGES}
        self._theme_mode = config.get_theme()
        ctk.set_appearance_mode(self._theme_mode)
        ctk.set_default_color_theme('blue')

        stored = config.get_ui_lang()
        set_lang(stored or 'en')
        self._needs_lang_prompt = (stored is None)

        self.title(f"{APP_DISPLAY_NAMES['browse']} — {PRODUCT_NAME}")
        self.geometry('1280x820')
        self.minsize(980, 680)
        self.configure(fg_color=BG_DARK)

        self._dest = dest
        self._url_input = url
        self._is_closing = False
        self._rebuilding = False

        # Browse state
        self._site_key = 'JableTV'
        self._categories: list[dict] = []
        self._current_base_url = ''
        self._page = 1
        self._has_next = True
        self._videos: list[dict] = []
        self._selected_urls: set = set()
        self._selected_source_subtitle_evidence: dict[str, tuple[str, ...]] = {}
        self._sidebar_expanded: dict[str, bool] = {}
        self._hanime_filter_state = {
            'query': '', 'genre': '', 'sort': '', 'date': '',
            'duration': '', 'tags': (),
        }
        self._hanime_filter_popup = None
        self._grid_gen: int = 0  # bumps on each page refresh so stale thumbs are dropped
        self._grid_columns = browse_columns_for_width(1280)
        self._resize_after_id = None
        self._page_req: int = 0
        self._build_gen: int = 0
        self._active_tab_idx: int = 0
        self._last_loaded_page: int = 1
        self._browse_blocked = False
        self._browse_empty_message = ''
        self._card_widgets: dict = {}  # url -> {card, sel_btn}
        self._dl_rows: dict = {}   # url -> {row, state_lbl, name_lbl, pb, pct, spd, remove}
        self._dl_empty_lbl = None
        self._dl_footer_lbl = None
        self._dl_drain_id = None
        self._dl_gen = 0
        self._thumb_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self._speed_mbps = 0.0
        self._download_autosave_ticks = 0
        self._last_download_save_sig = None
        self._update_info = None
        self._update_checking = False
        self._update_installing = False
        self._update_prompt_shown = False
        self._update_status_text = ''
        self._update_status_color = TEXT_DIM
        self._update_badge = None
        self._update_status_lbl = None
        self._update_note_lbl = None
        self._update_check_btn = None
        self._update_now_btn = None

        # Download manager
        self._dlmgr = DownloadManager(
            max_concurrent=config.get_download_concurrency())
        if not os.path.exists(CSV_PATH):
            old_csv = os.path.join(os.getcwd(), 'JableTV.csv')
            if (os.path.exists(old_csv) and
                    os.path.abspath(old_csv) != os.path.abspath(CSV_PATH)):
                try:
                    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
                    shutil.copy2(old_csv, CSV_PATH)
                except Exception:
                    pass
        self._dlmgr.load_csv(CSV_PATH)

        from uav_downloader.sites.base import set_resolution_pref
        set_resolution_pref(config.get_resolution_pref())

        self._build_ui()
        self.bind('<Configure>', self._on_root_resize, add='+')
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        # Start periodic refresh for downloads
        self._refresh_downloads()
        # Start clipboard monitor (main-thread safe)
        self._clp_text = ''
        self._clipboard_poll()

        # Tk rejects worker-thread ``after`` calls before mainloop starts.  Defer
        # every worker that can complete quickly until the event loop is active.
        self.after_idle(self._start_initial_background_tasks)
        if self._needs_lang_prompt:
            self.after(250, self._first_run_language_prompt)

    def _start_initial_background_tasks(self):
        if self._is_closing:
            return
        self._start_update_check(manual=False)
        self._load_categories()

    def _ask_language_first_run(self):
        popup = None
        try:
            idx = 0 if ctk.get_appearance_mode() == 'Light' else 1
            def C(tok):                      # resolve a (light,dark) token to a single hex string
                return tok[idx] if isinstance(tok, (tuple, list)) else tok
            bg, card, fg, border, accent, cardh = (
                C(BG_DARK), C(BG_CARD), C(TEXT_PRI), C(BORDER_HOVER), C(ACCENT), C(BG_CARD_HOVER))

            popup = tk.Toplevel(self)
            popup.title(T('lang_picker_title'))
            popup.configure(bg=bg)
            popup.resizable(False, False)
            popup.transient(self)

            picker_font = 'Microsoft JhengHei'   # renders all 4 native scripts
            tk.Label(popup, text=T('lang_picker_title'), bg=bg, fg=fg,
                     font=(picker_font, 15, 'bold')).pack(padx=32, pady=(24, 14))

            def _choose(code='en'):
                config.set_ui_lang(code)
                if code != get_lang():
                    self._apply_language(code)
                try:
                    popup.destroy()
                except tk.TclError:
                    pass

            for code, name in LANGUAGES:
                tk.Button(popup, text=name, width=22,
                          bg=card, fg=fg, activebackground=accent, activeforeground='#ffffff',
                          relief='flat', bd=1, highlightbackground=border, highlightthickness=1,
                          padx=12, pady=9, font=(picker_font, 12), cursor='hand2',
                          command=lambda c=code: _choose(c)).pack(padx=32, pady=5)

            popup.protocol('WM_DELETE_WINDOW', lambda: _choose(get_lang() or 'en'))
            popup.update_idletasks()
            w = max(popup.winfo_reqwidth(), 320)
            h = max(popup.winfo_reqheight(), 280)
            x = max((self.winfo_screenwidth() - w) // 2, 0)
            y = max((self.winfo_screenheight() - h) // 3, 0)
            popup.geometry(f'{w}x{h}+{x}+{y}')
            # Force the picker visible (plain tk.Toplevel shows reliably in frozen builds)
            popup.deiconify()
            popup.lift()
            try:
                popup.attributes('-topmost', True)
                popup.after(300, lambda: popup.winfo_exists() and popup.attributes('-topmost', False))
            except tk.TclError:
                pass
            popup.update_idletasks()
            popup.focus_force()
        except Exception:
            if popup is not None:
                try:
                    popup.destroy()
                except tk.TclError:
                    pass
            try:
                config.set_ui_lang('en')
            except Exception:
                pass

    def _first_run_language_prompt(self):
        if self._is_closing:
            return
        try:
            self.deiconify()
            self.update_idletasks()
        except tk.TclError:
            pass
        self._ask_language_first_run()

    def _ui(self, fn, gen: int | None = None):
        if self._is_closing:
            return
        if gen is not None and gen != self._build_gen:
            return

        def _run():
            if self._is_closing:
                return
            if gen is not None and gen != self._build_gen:
                return
            try:
                fn()
            except tk.TclError:
                pass

        try:
            self.after(0, _run)
        except tk.TclError:
            pass

    def _short_update_note(self, info):
        notes = (info or {}).get('notes') or ''
        for line in notes.splitlines():
            line = line.strip()
            if line:
                return line[:180]
        return ''

    def _show_update_prompt(self, info):
        if self._is_closing or self._update_prompt_shown:
            return
        self._update_prompt_shown = True
        prompt = None
        try:
            prompt = ctk.CTkToplevel(self)
            prompt.title(T('update_prompt_title'))
            prompt.configure(fg_color=BG_CARD)
            prompt.resizable(False, False)
            prompt.transient(self)

            pos_width, pos_height = 420, 220
            self.update_idletasks()
            x = self.winfo_rootx() + max((self.winfo_width() - pos_width) // 2, 0)
            y = self.winfo_rooty() + 80
            x = max(min(x, self.winfo_screenwidth() - pos_width), 0)
            y = max(min(y, self.winfo_screenheight() - pos_height), 0)

            body = ctk.CTkFrame(prompt, fg_color=BG_CARD, corner_radius=0)
            body.pack(fill='both', expand=True, padx=22, pady=20)

            ctk.CTkLabel(
                body, text=T('update_prompt_title'),
                font=(ui_font(), 16, 'bold'), text_color=TEXT_PRI
            ).pack(anchor='w')

            tag = (info or {}).get('tag') or (info or {}).get('version')
            ctk.CTkLabel(
                body, text=T('update_available', version=tag),
                font=(ui_font(), 12), text_color=TEXT_SEC,
                wraplength=360, justify='left'
            ).pack(anchor='w', pady=(12, 0))

            note = self._short_update_note(info)
            if note:
                ctk.CTkLabel(
                    body, text=note, font=(ui_font(), 10),
                    text_color=TEXT_DIM, wraplength=360, justify='left'
                ).pack(anchor='w', pady=(8, 0))

            row = ctk.CTkFrame(body, fg_color='transparent')
            row.pack(fill='x', pady=(18, 0))

            def _close():
                try:
                    prompt.destroy()
                except tk.TclError:
                    pass

            def _install():
                _close()
                self._start_update_install()

            later_btn = ctk.CTkButton(
                row, text=T('update_prompt_later'), width=118, height=34,
                corner_radius=8, fg_color='transparent', border_width=1,
                border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
                text_color=TEXT_PRI, command=_close)

            update_btn = ctk.CTkButton(
                row, text=T('update_now_btn'), width=118, height=34,
                corner_radius=8, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                text_color=('#FFFFFF', '#FFFFFF'), command=_install
            )
            update_btn.pack(side='right')
            later_btn.pack(side='right', padx=(0, 8))

            prompt.protocol('WM_DELETE_WINDOW', _close)
            prompt.bind('<Escape>', lambda _event: _close())
            prompt.update_idletasks()
            prompt.geometry(f'+{x}+{y}')
            prompt.deiconify()
            prompt.lift()
            prompt.focus_force()
            later_btn.focus_force()
        except Exception:
            if prompt is not None:
                try:
                    prompt.destroy()
                except tk.TclError:
                    pass

    def _set_update_status(self, text, color=None):
        self._update_status_text = text
        self._update_status_color = color or TEXT_DIM
        self._refresh_update_ui()

    def _refresh_update_ui(self):
        available = bool(self._update_info)
        status = self._update_status_text or T('update_idle')
        if self._update_status_lbl is not None:
            try:
                self._update_status_lbl.configure(
                    text=status, text_color=self._update_status_color)
            except tk.TclError:
                pass
        if self._update_note_lbl is not None:
            try:
                if available:
                    note = self._short_update_note(self._update_info)
                    tag = self._update_info.get('tag') or self._update_info.get('version')
                    text = T('update_available', version=tag)
                    if note:
                        text = f'{text}  {note}'
                    self._update_note_lbl.configure(text=text)
                else:
                    self._update_note_lbl.configure(text='')
            except tk.TclError:
                pass
        if self._update_check_btn is not None:
            try:
                self._update_check_btn.configure(
                    state='disabled' if self._update_checking else 'normal')
            except tk.TclError:
                pass
        if self._update_now_btn is not None:
            try:
                if available:
                    if not self._update_now_btn.winfo_manager():
                        self._update_now_btn.pack(side='left', padx=(8, 0))
                    self._update_now_btn.configure(
                        state='disabled' if self._update_installing else 'normal')
                else:
                    self._update_now_btn.configure(state='disabled')
                    self._update_now_btn.pack_forget()
            except tk.TclError:
                pass
        if self._update_badge is not None:
            try:
                if available:
                    if not self._update_badge.winfo_manager():
                        self._update_badge.pack(side='left', padx=(8, 0))
                else:
                    self._update_badge.pack_forget()
            except tk.TclError:
                pass

    def _start_update_check(self, manual=False):
        if self._is_closing or self._update_checking:
            return
        self._update_checking = True
        if manual:
            self._set_update_status(T('update_checking'), TEXT_SEC)
        else:
            self._refresh_update_ui()

        def _worker():
            info = None
            newer = False
            try:
                info = updater.check_latest()
                newer = bool(info and updater.is_newer(
                    info.get('version', ''), APP_VERSION))
            except Exception:
                info = None
                newer = False

            def _apply():
                self._update_checking = False
                if newer:
                    self._update_info = info
                    tag = info.get('tag') or info.get('version')
                    self._set_update_status(
                        T('update_available', version=tag), SUCCESS)
                    if not manual and not self._update_prompt_shown:
                        self._show_update_prompt(info)
                elif manual:
                    self._update_info = None
                    self._set_update_status(T('update_uptodate'), TEXT_SEC)
                elif info is not None:
                    self._update_info = None
                    self._refresh_update_ui()
                else:
                    if manual:
                        self._set_update_status(T('update_failed'), ERROR_C)
                    else:
                        self._refresh_update_ui()

            self._ui(_apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _start_update_install(self):
        info = self._update_info
        if self._is_closing or self._update_installing or not info:
            return
        if not updater.is_frozen():
            try:
                webbrowser.open(info.get('html_url') or updater.API_LATEST)
            except Exception:
                pass
            self._set_update_status(T('update_from_source'), WARNING)
            return

        name = updater.current_exe_name()
        url = updater.find_asset_url(info, 'browse')
        if not url:
            self._set_update_status(T('update_failed'), ERROR_C)
            return

        self._update_installing = True
        self._set_update_status(T('update_downloading', pct=0), TEXT_SEC)

        def _worker():
            exe_dir = os.path.dirname(sys.executable)
            new_path = os.path.join(exe_dir, name + '.new')

            def _progress(downloaded, total):
                pct = int(downloaded * 100 / total) if total else 0
                self._ui(lambda p=pct: self._set_update_status(
                    T('update_downloading', pct=p), TEXT_SEC))

            ok = updater.download_asset(url, new_path, progress_cb=_progress)
            if ok and updater.apply_update_and_restart(new_path):
                self._ui(lambda: self._set_update_status(
                    T('update_restarting'), SUCCESS))
                self._ui(self._on_close)
                return

            def _failed():
                self._update_installing = False
                self._set_update_status(T('update_failed'), ERROR_C)

            self._ui(_failed)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_settings(self, event=None):
        self._select_tab('settings')

    # ── Build UI ─────────────────────────────────────────────────────
    def _theme_glyph(self):
        return {'system': '◐', 'light': '☀', 'dark': '☾'}.get(self._theme_mode, '◐')

    def _cycle_theme(self):
        modes = ('system', 'light', 'dark')
        try:
            idx = modes.index(self._theme_mode)
        except ValueError:
            idx = 0
        self._theme_mode = modes[(idx + 1) % len(modes)]
        ctk.set_appearance_mode(self._theme_mode)
        config.set_theme(self._theme_mode)
        self._theme_btn.configure(text=self._theme_glyph())

    def _on_root_resize(self, event):
        if event.widget is not self or self._is_closing:
            return
        try:
            logical_width = event.width / max(self._get_window_scaling(), 1.0)
        except Exception:
            logical_width = event.width
        columns = browse_columns_for_width(logical_width)
        if columns == self._grid_columns:
            return
        self._grid_columns = columns
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        try:
            self._resize_after_id = self.after(180, self._apply_responsive_grid)
        except tk.TclError:
            self._resize_after_id = None

    def _apply_responsive_grid(self):
        self._resize_after_id = None
        if (self._is_closing or not self._videos or
                getattr(self, '_grid_scroll', None) is None):
            return
        self._refresh_grid()

    def _current_tab_index(self):
        return self._active_tab_idx

    def _set_tab_index(self, idx: int):
        idx = max(0, min(int(idx), len(self._tab_keys) - 1))
        self._select_tab(self._tab_keys[idx])

    def _select_tab(self, key):
        if key not in getattr(self, '_tab_frames', {}):
            return
        for f in self._tab_frames.values():
            f.pack_forget()
        self._tab_frames[key].pack(fill='both', expand=True)
        for k, w in self._tab_buttons.items():
            active = (k == key)
            try:
                w['lbl'].configure(
                    text_color=(TEXT_PRI if active else TEXT_SEC),
                    font=(ui_font(), 13, 'bold') if active else (ui_font(), 13))
                w['underline'].configure(fg_color=(ACCENT if active else 'transparent'))
            except tk.TclError:
                pass
        self._active_tab_idx = self._tab_keys.index(key)

    def _speed_values(self):
        return [T('unlimited'), '1 MB/s', '2 MB/s', '5 MB/s',
                '10 MB/s', '15 MB/s']

    def _speed_label(self):
        return T('unlimited') if self._speed_mbps == 0 else f'{int(self._speed_mbps)} MB/s'

    def _resolution_values(self):
        return [T('resolution_highest'), '1080p', '720p', '480p', '360p',
                T('resolution_lowest')]

    def _resolution_pref_from_label(self, label):
        label = str(label or '').strip()
        if label == T('resolution_lowest'):
            return 'lowest'
        if label in {'1080p', '720p', '480p', '360p'}:
            return label[:-1]
        return 'highest'

    def _resolution_label(self):
        from uav_downloader.sites.base import get_resolution_pref
        pref = get_resolution_pref()
        if pref == 'lowest':
            return T('resolution_lowest')
        if pref in {'1080', '720', '480', '360'}:
            return f'{pref}p'
        return T('resolution_highest')

    def _filename_mode_values(self):
        return [T('filename_mode_full'), T('filename_mode_code')]

    def _filename_mode_from_label(self, label):
        if str(label or '') == T('filename_mode_code'):
            return 'code-only'
        return 'full-title'

    def _filename_mode_label(self, mode=None):
        if mode is None:
            mode = config.get_filename_mode()
        mode = config.normalize_filename_mode(mode)
        if mode == 'code-only':
            return T('filename_mode_code')
        return T('filename_mode_full')

    def _subtitle_values(self):
        return [
            T('subtitle_none'), T('subtitle_ja'), T('subtitle_en'),
            T('subtitle_zh'), T('subtitle_all'),
        ]

    def _subtitle_pref_from_label(self, label):
        return {
            T('subtitle_none'): 'none',
            T('subtitle_ja'): 'ja',
            T('subtitle_en'): 'en',
            T('subtitle_zh'): 'zh',
            T('subtitle_all'): 'all',
        }.get(str(label or ''), 'none')

    def _subtitle_label(self):
        return {
            'none': T('subtitle_none'),
            'ja': T('subtitle_ja'),
            'en': T('subtitle_en'),
            'zh': T('subtitle_zh'),
            'all': T('subtitle_all'),
        }.get(config.get_subtitle_pref(), T('subtitle_none'))

    def _recognition_quality_values(self):
        return [
            T('recognition_quality_auto'),
            T('recognition_quality_quality'),
            T('recognition_quality_balanced'),
            T('recognition_quality_fast'),
        ]

    def _recognition_quality_from_label(self, label):
        return normalize_recognition_quality({
            T('recognition_quality_auto'): 'auto',
            T('recognition_quality_quality'): 'quality',
            T('recognition_quality_balanced'): 'balanced',
            T('recognition_quality_fast'): 'fast',
        }.get(str(label or ''), 'auto'))

    def _recognition_quality_label(self, quality=None):
        quality = (
            config.get_recognition_quality()
            if quality is None else quality)
        return {
            'auto': T('recognition_quality_auto'),
            'quality': T('recognition_quality_quality'),
            'balanced': T('recognition_quality_balanced'),
            'fast': T('recognition_quality_fast'),
        }[normalize_recognition_quality(quality)]

    def _open_translation_settings(self):
        open_translation_settings_dialog(
            self, on_saved=self._refresh_translation_provider_status)

    def _refresh_translation_provider_status(self):
        label = getattr(self, '_translation_provider_status_lbl', None)
        if label is None:
            return
        try:
            label.configure(text=translation_provider_summary())
        except tk.TclError:
            pass

    def _on_lang_change(self, display_name):
        code = self._lang_code_by_name.get(display_name)
        if not code or code == get_lang():
            return
        self._apply_language(code)

    def _var_get(self, name, default=''):
        var = getattr(self, name, None)
        if var is None:
            return default
        try:
            return var.get()
        except (AttributeError, tk.TclError):
            return default

    def _apply_language(self, code):
        self._rebuilding = True
        from uav_downloader.sites.base import get_resolution_pref, set_resolution_pref

        try:
            snapshot = {
                'tab_idx': self._current_tab_index(),
                'dest': self._var_get('_dest_var', self._dest),
                'dl_url': self._var_get('_dl_url_var', self._url_input),
                'cf_host': self._var_get('_cf_host_var'),
                'cf_cookie': self._var_get('_cf_cookie_var'),
                'cf_ua': self._var_get('_cf_ua_var'),
                'page_jump': self._var_get('_page_jump_var'),
                'concurrency': self._dlmgr.max_concurrent,
                'max_workers_per_video': self._commit_workers_preference(),
                'speed_mbps': self._speed_mbps,
                'resolution_pref': get_resolution_pref(),
                'site_key': self._site_key,
            }

            set_lang(code)
            config.set_ui_lang(code)
            self._build_gen += 1
            self._page_req += 1
            self._grid_gen += 1

            for child in self.winfo_children():
                try:
                    child.destroy()
                except tk.TclError:
                    pass

            self._card_widgets = {}
            self._dl_rows = {}
            self._dl_footer_lbl = None
            self._dl_drain_id = None
            self._dl_gen += 1
            self._categories = []
            self._selected_urls.clear()
            self._dl_empty_lbl = None
            self._videos = []
            self._browse_blocked = False
            self._browse_empty_message = ''
            self._cf_status_lbl = None
            self._site_menu = None
            self._cat_menu = None
            self._grid_scroll = None
            self._dl_scroll = None
            self._sidebar = None
            self._status_lbl = None

            self._dest = snapshot['dest']
            self._url_input = snapshot['dl_url']
            self._site_key = snapshot['site_key']
            self._speed_mbps = snapshot['speed_mbps']
            set_resolution_pref(snapshot['resolution_pref'])

            self._build_ui()

            self._site_key = snapshot['site_key']
            self._site_var.set(snapshot['site_key'])
            self._dest_var.set(snapshot['dest'])
            self._dl_url_var.set(snapshot['dl_url'])
            self._page_jump_var.set(snapshot['page_jump'])
            self._conc_var.set(str(snapshot['concurrency']))
            self._workers_var.set(str(snapshot['max_workers_per_video']))
            self._speed_var.set(self._speed_label())
            self._res_var.set(self._resolution_label())
            if snapshot['cf_host']:
                self._cf_host_var.set(snapshot['cf_host'])
            self._cf_cookie_var.set(snapshot['cf_cookie'])
            self._cf_ua_var.set(snapshot['cf_ua'])
            self._refresh_cf_status()
            self._set_tab_index(snapshot['tab_idx'])
            self._update_selection_count()
            self._rebuild_sidebar()
            self._load_categories()
        finally:
            self._rebuilding = False
        self._refresh_downloads(schedule=False)

    def _build_ui(self):
        # ── Header bar ──────────────────────────────────────────────
        header = ctk.CTkFrame(self, height=64, fg_color=BG_HEADER, corner_radius=0)
        header.pack(fill='x')
        header.pack_propagate(False)

        # Brand — stacked to keep the product name readable at compact widths.
        brand = ctk.CTkFrame(header, fg_color='transparent')
        brand.pack(side='left', padx=24, fill='y')
        ctk.CTkLabel(brand, text=APP_DISPLAY_NAMES['browse'],
                     font=(ui_font(), 17, 'bold'),
                     text_color=TEXT_PRI).pack(anchor='w', pady=(9, 0))
        ctk.CTkLabel(brand, text='UAV DOWNLOADER  /  BROWSER',
                     font=('Consolas', 9, 'bold'),
                     text_color=ACCENT).pack(anchor='w', pady=(0, 9))

        # Right info
        right_info = ctk.CTkFrame(header, fg_color='transparent')
        right_info.pack(side='right', padx=24, fill='y')
        version_box = ctk.CTkFrame(
            right_info, fg_color=BG_BADGE, corner_radius=6,
            border_width=1, border_color=BORDER)
        version_box.pack(side='right', padx=(10, 0), pady=14)
        ctk.CTkLabel(version_box, text=f'v{APP_VERSION}',
                     font=('Consolas', 10, 'bold'),
                     text_color=TEXT_SEC).pack(side='left', padx=10, pady=4)
        self._update_badge = ctk.CTkLabel(
            version_box, text=T('update_new_badge'),
            font=(ui_font(), 10, 'bold'), text_color=ACCENT)
        try:
            self._update_badge.configure(cursor='hand2')
        except Exception:
            pass
        self._update_badge.bind('<Button-1>', self._show_update_settings)
        self._theme_btn = ctk.CTkButton(
            right_info, text=self._theme_glyph(), width=36, height=36,
            corner_radius=CONTROL_RADIUS, fg_color=BG_CARD, border_width=1,
            border_color=BORDER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_SEC, font=(ui_font(), 14),
            command=self._cycle_theme)
        self._theme_btn.pack(side='right', padx=(8, 0), pady=14)
        self._lang_var = ctk.StringVar(value=self._lang_name_by_code.get(get_lang(), 'English'))
        self._lang_menu = ctk.CTkOptionMenu(
            right_info, values=[name for _, name in LANGUAGES],
            variable=self._lang_var, command=self._on_lang_change,
            width=126, height=36, corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(ui_font(), 11), dropdown_font=(ui_font(), 11))
        self._lang_menu.pack(side='right', pady=14)

        # Header separator
        ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0).pack(fill='x')

        # ── Custom underline tab bar (Studio Noir) ──────────────────
        self._tab_keys = ['browse', 'download', 'settings']
        tab_labels = {'browse': T('tab_browse'), 'download': T('tab_download'), 'settings': T('tab_settings')}

        tabbar = ctk.CTkFrame(self, height=48, fg_color=BG_HEADER, corner_radius=0)
        tabbar.pack(fill='x')
        tabbar.pack_propagate(False)
        tabbar_inner = ctk.CTkFrame(tabbar, fg_color='transparent')
        tabbar_inner.pack(side='left', padx=18, fill='y')

        self._tab_buttons = {}   # key -> {'lbl': CTkLabel, 'underline': CTkFrame}
        for key in self._tab_keys:
            holder = ctk.CTkFrame(tabbar_inner, fg_color='transparent')
            holder.pack(side='left', padx=(0, 6), fill='y')
            # underline FIRST at the bottom so it is never clipped
            underline = ctk.CTkFrame(holder, height=3, fg_color='transparent', corner_radius=2)
            underline.pack(side='bottom', fill='x', padx=4, pady=(0, 0))
            lbl = ctk.CTkLabel(holder, text=tab_labels[key],
                               font=(ui_font(), 13), text_color=TEXT_SEC, cursor='hand2')
            lbl.pack(side='top', fill='both', expand=True, padx=14)
            lbl.bind('<Button-1>', lambda e, k=key: self._select_tab(k))
            self._tab_buttons[key] = {'lbl': lbl, 'underline': underline}

        # Header separator already drawn above; add one below the tab bar
        ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0).pack(fill='x')

        # Content container holding the 3 tab frames
        self._tab_container = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self._tab_container.pack(fill='both', expand=True)
        self._tab_frames = {}
        for key in self._tab_keys:
            self._tab_frames[key] = ctk.CTkFrame(
                self._tab_container, fg_color=BG_DARK, corner_radius=0)

        self._build_browse_tab()
        self._build_download_tab()
        self._build_settings_tab()

        self._select_tab(self._tab_keys[self._active_tab_idx])

        # ── Status bar ──────────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0).pack(fill='x')
        status_bar = ctk.CTkFrame(self, height=30, fg_color=BG_HEADER, corner_radius=0)
        status_bar.pack(fill='x')
        status_bar.pack_propagate(False)
        self._status_lbl = ctk.CTkLabel(status_bar, text=T('status_ready'),
                                         font=('Consolas', 10),
                                         text_color=TEXT_SEC)
        self._status_lbl.pack(side='left', padx=16)

    # ── Browse Tab ───────────────────────────────────────────────────
    def _build_browse_tab(self):
        tab = self._tab_frames['browse']

        # ── Two-level workspace toolbar ─────────────────────────────
        top = ctk.CTkFrame(tab, fg_color=BG_SECTION, corner_radius=0, height=108)
        top.pack(fill='x')
        top.pack_propagate(False)

        filters = ctk.CTkFrame(top, fg_color='transparent')
        filters.pack(fill='x', padx=20, pady=(10, 6))
        self._site_var = ctk.StringVar(value=self._site_key)
        self._site_menu = ctk.CTkSegmentedButton(
            filters, values=list(SITES.keys()), variable=self._site_var,
            command=self._on_site_change, height=36, corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_INPUT, unselected_hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(ui_font(), 11, 'bold'))
        self._site_menu.pack(side='left')

        self._cat_var = ctk.StringVar(value=T('loading_browse'))
        self._cat_menu = ctk.CTkOptionMenu(
            filters, values=[T('loading_browse')], variable=self._cat_var,
            command=self._on_cat_change, width=190, height=36,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI, corner_radius=CONTROL_RADIUS,
            font=(ui_font(), 11), dropdown_font=(ui_font(), 11))
        self._cat_menu.pack(side='left', padx=(10, 0))

        self._hanime_filter_box = ctk.CTkFrame(
            filters, fg_color='transparent')
        self._hanime_filter_btn = ctk.CTkButton(
            self._hanime_filter_box, text=T('hanime_filter_button'),
            command=self._open_hanime_filters,
            width=112, height=36, corner_radius=CONTROL_RADIUS,
            fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(ui_font(), 10, 'bold'))
        self._hanime_filter_btn.pack()

        self._search_var = ctk.StringVar()
        self._search_entry = ctk.CTkEntry(
            filters, textvariable=self._search_var,
            placeholder_text=T('search_placeholder'), height=36,
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
            corner_radius=CONTROL_RADIUS, text_color=TEXT_PRI,
            font=(ui_font(), 11))
        self._search_entry.pack(
            side='left', fill='x', expand=True, padx=(10, 8))
        self._search_entry.bind('<Return>', lambda e: self._on_search())
        ctk.CTkButton(filters, text=T('search_btn'), command=self._on_search,
                      width=76, height=36, corner_radius=CONTROL_RADIUS,
                      fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      text_color=WHITE, font=(ui_font(), 11, 'bold')).pack(side='left')
        self._sync_hanime_filter_button_visibility()

        actions = ctk.CTkFrame(top, fg_color='transparent')
        actions.pack(fill='x', padx=20, pady=(0, 10))

        self._sel_lbl = ctk.CTkLabel(
            actions, text=f'0 {T("selected")}', text_color=TEXT_SEC,
            font=(ui_font(), 11, 'bold'))
        self._sel_lbl.pack(side='left')
        ctk.CTkButton(actions, text=T('download_selected'), command=self._download_selected,
                      width=128, height=34, corner_radius=CONTROL_RADIUS,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=WHITE, font=(ui_font(), 11, 'bold')).pack(
                          side='right', padx=(8, 0))
        ctk.CTkButton(actions, text=T('add_to_queue'), command=self._add_selected_to_queue,
                      width=104, height=34, corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER,
                      text_color=TEXT_PRI, font=(ui_font(), 11)).pack(side='right')
        ctk.CTkButton(actions, text=T('select_all_btn'), command=self._select_all_on_page,
                      width=92, height=34, corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER,
                      text_color=TEXT_PRI, font=(ui_font(), 11)).pack(
                          side='right', padx=(0, 8))

        # ── Content area: sidebar + grid ────────────────────────────
        content = ctk.CTkFrame(tab, fg_color=BG_DARK, corner_radius=0)
        content.pack(fill='both', expand=True)

        # Sidebar
        self._sidebar = ctk.CTkScrollableFrame(
            content, width=176, fg_color=BG_SIDEBAR,
            corner_radius=0, scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        self._sidebar.pack(side='left', fill='y')

        # Video grid area
        grid_area = ctk.CTkFrame(content, fg_color=BG_DARK, corner_radius=0)
        grid_area.pack(side='left', fill='both', expand=True)

        self._grid_scroll = ctk.CTkScrollableFrame(
            grid_area, fg_color=BG_DARK, corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        self._grid_scroll.pack(fill='both', expand=True)

        # ── Navigation bar ──────────────────────────────────────────
        nav = ctk.CTkFrame(tab, fg_color=BG_HEADER, corner_radius=0, height=44)
        nav.pack(fill='x')
        nav.pack_propagate(False)

        nav_inner = ctk.CTkFrame(nav, fg_color='transparent')
        nav_inner.pack(pady=6)

        ctk.CTkButton(nav_inner, text=T('first_page'), width=64, height=30,
                      corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=lambda: self._goto_page(1)).pack(side='left', padx=3)
        ctk.CTkButton(nav_inner, text=T('prev_page'), width=74, height=30,
                      corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=lambda: self._goto_page(self._page - 1)
                      ).pack(side='left', padx=3)
        self._page_lbl = ctk.CTkLabel(nav_inner, text=T('page_n', n=1), text_color=TEXT_PRI,
                                       font=(ui_font(), 12, 'bold'),
                                       width=80)
        self._page_lbl.pack(side='left', padx=10)
        ctk.CTkButton(nav_inner, text=T('next_page'), width=74, height=30,
                      corner_radius=8,
                      fg_color=ACCENT,
                      hover_color=ACCENT_HOVER,
                      text_color=('#FFFFFF', '#FFFFFF'),
                      command=lambda: self._goto_page(self._page + 1)
                      ).pack(side='left', padx=3)

        # Page jump input
        ctk.CTkFrame(nav_inner, width=1, fg_color=BORDER).pack(
            side='left', fill='y', pady=4, padx=10)
        self._page_jump_var = ctk.StringVar(value='')
        page_entry = ctk.CTkEntry(nav_inner, textvariable=self._page_jump_var,
                                   width=50, height=30, corner_radius=8,
                                   fg_color=BG_INPUT, border_color=BORDER,
                                   border_width=1, text_color=TEXT_PRI,
                                   placeholder_text='#',
                                   justify='center')
        page_entry.pack(side='left', padx=3)
        page_entry.bind('<Return>', lambda e: self._jump_to_page())
        ctk.CTkButton(nav_inner, text=T('go_btn'), width=40, height=30,
                      corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._jump_to_page).pack(side='left', padx=3)

        self._rebuild_sidebar()

    # ── Download Tab ─────────────────────────────────────────────────
    def _build_download_tab(self):
        tab = self._tab_frames['download']

        # ── Input section ───────────────────────────────────────────
        input_frame = ctk.CTkFrame(tab, fg_color=BG_SECTION, corner_radius=0)
        input_frame.pack(fill='x')

        # Save location
        row1 = ctk.CTkFrame(input_frame, fg_color='transparent')
        row1.pack(fill='x', padx=20, pady=(14, 5))
        ctk.CTkLabel(row1, text=T('save_location'), text_color=TEXT_SEC, width=86,
                     font=(ui_font(), 11, 'bold'), anchor='w').pack(side='left')
        self._dest_var = ctk.StringVar(value=self._dest)
        ctk.CTkEntry(row1, textvariable=self._dest_var,
                     height=38, corner_radius=CONTROL_RADIUS,
                     fg_color=BG_INPUT, border_color=BORDER, border_width=1,
                     text_color=TEXT_PRI, font=(ui_font(), 11)).pack(side='left', fill='x',
                                               expand=True, padx=10)
        ctk.CTkButton(row1, text=T('browse_folder'), width=72, height=38, corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._pick_dest).pack(side='left')
        ctk.CTkButton(row1, text=T('open_btn'), width=60, height=38, corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._open_dest_folder).pack(side='left', padx=(6, 0))

        # Download URL
        row2 = ctk.CTkFrame(input_frame, fg_color='transparent')
        row2.pack(fill='x', padx=20, pady=(0, 14))
        ctk.CTkLabel(row2, text=T('url_label'), text_color=TEXT_SEC, width=86,
                     font=(ui_font(), 11, 'bold'), anchor='w').pack(side='left')
        self._dl_url_var = ctk.StringVar(value=self._url_input)
        ctk.CTkEntry(row2, textvariable=self._dl_url_var,
                     height=38, corner_radius=CONTROL_RADIUS,
                     fg_color=BG_INPUT, border_color=BORDER, border_width=1,
                     text_color=TEXT_PRI, font=('Consolas', 10)).pack(side='left', fill='x',
                                               expand=True, padx=10)

        # Separator
        ctk.CTkFrame(tab, height=1, fg_color=BORDER, corner_radius=0).pack(fill='x')

        # ── Action bar ──────────────────────────────────────────────
        bar = ctk.CTkFrame(tab, fg_color=BG_HEADER, corner_radius=0, height=58)
        bar.pack(fill='x')
        bar.pack_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        actions_left = ctk.CTkFrame(bar, fg_color='transparent')
        actions_left.grid(row=0, column=0, padx=(16, 8), pady=10, sticky='w')
        ctk.CTkButton(actions_left, text=T('download_btn'), width=112, height=38,
                      corner_radius=CONTROL_RADIUS,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=WHITE,
                      font=(ui_font(), 11, 'bold'),
                      command=self._download_url).pack(side='left')
        ctk.CTkButton(actions_left, text=T('download_all_btn'), width=126, height=38,
                      corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._download_all).pack(side='left', padx=(8, 0))

        speed = ctk.CTkFrame(bar, fg_color='transparent')
        speed.grid(row=0, column=1, pady=10)
        ctk.CTkLabel(speed, text=T('speed_limit'), text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(side='left', padx=(0, 8))
        self._speed_var = ctk.StringVar(value=self._speed_label())
        ctk.CTkOptionMenu(
            speed, values=self._speed_values(), variable=self._speed_var,
            command=self._on_speed_change, width=118, height=38,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            button_color=BORDER_HOVER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER, dropdown_text_color=TEXT_PRI,
            font=(ui_font(), 10), dropdown_font=(ui_font(), 10)).pack(side='left')

        destructive = ctk.CTkFrame(bar, fg_color='transparent')
        destructive.grid(row=0, column=2, padx=(8, 16), pady=10, sticky='e')
        ctk.CTkButton(destructive, text=T('cancel_all'), width=88, height=38,
                      corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=ERROR_C,
                      command=self._cancel_all).pack(side='left')
        ctk.CTkButton(destructive, text=T('clear_list'), width=70, height=38,
                      corner_radius=CONTROL_RADIUS,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
                      command=self._clear_queue).pack(side='left', padx=(8, 0))

        # Separator under action bar
        ctk.CTkFrame(tab, height=1, fg_color=BORDER, corner_radius=0).pack(fill='x')

        # ── Download list ───────────────────────────────────────────
        self._dl_scroll = ctk.CTkScrollableFrame(
            tab, fg_color=BG_DARK, corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        self._dl_scroll.pack(fill='both', expand=True)

    def _build_update_card(self, content):
        upd = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                           border_width=1, border_color=BORDER_CARD)
        upd.pack(fill='x', pady=(0, 16))

        upd_hdr = ctk.CTkFrame(upd, fg_color='transparent')
        upd_hdr.pack(fill='x', padx=20, pady=(16, 12))
        ctk.CTkLabel(upd_hdr, text=T('update_card_title'),
                     font=(ui_font(), 15, 'bold'),
                     text_color=TEXT_PRI).pack(side='left')

        ctk.CTkFrame(upd, height=1, fg_color=BORDER).pack(fill='x', padx=20)

        row_info = ctk.CTkFrame(upd, fg_color='transparent')
        row_info.pack(fill='x', padx=20, pady=(14, 16))

        row_actions = ctk.CTkFrame(row_info, fg_color='transparent')
        row_actions.pack(side='right', anchor='e')

        left_info = ctk.CTkFrame(row_info, fg_color='transparent')
        left_info.pack(side='left', fill='x', expand=True, padx=(0, 16))
        ctk.CTkLabel(left_info, text=T('update_current', version=APP_VERSION),
                     font=(ui_font(), 12, 'bold'),
                     text_color=TEXT_PRI).pack(anchor='w')

        self._update_status_lbl = ctk.CTkLabel(
            left_info, text='', text_color=TEXT_DIM, font=(ui_font(), 11))
        self._update_status_lbl.pack(anchor='w', pady=(3, 0))

        self._update_note_lbl = ctk.CTkLabel(
            left_info, text='', text_color=TEXT_DIM, font=(ui_font(), 10),
            wraplength=620, justify='left')
        self._update_note_lbl.pack(anchor='w', pady=(1, 0))

        self._update_check_btn = ctk.CTkButton(
            row_actions, text=T('update_check_btn'), width=138, height=38,
            corner_radius=CONTROL_RADIUS, fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI,
            command=lambda: self._start_update_check(manual=True))
        self._update_check_btn.pack(side='left')
        self._update_now_btn = ctk.CTkButton(
            row_actions, text=T('update_now_btn'), width=118, height=38,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=WHITE,
            command=self._start_update_install)
        self._refresh_update_ui()

    # ── Settings Tab ─────────────────────────────────────────────────
    def _build_settings_tab(self):
        tab = self._tab_frames['settings']

        outer = ctk.CTkScrollableFrame(
            tab, fg_color=BG_DARK, corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        outer.pack(fill='both', expand=True)

        # Content container
        content = ctk.CTkFrame(outer, fg_color='transparent')
        content.pack(fill='x', padx=64, pady=28)

        # ── Page title ──────────────────────────────────────────────
        title_row = ctk.CTkFrame(content, fg_color='transparent')
        title_row.pack(fill='x', pady=(0, 22))
        ctk.CTkLabel(title_row, text=T('settings_title'),
                     font=(ui_font(), 22, 'bold'),
                     text_color=TEXT_PRI).pack(anchor='w')
        ctk.CTkLabel(title_row, text=T('settings_desc'),
                     font=(ui_font(), 11),
                     text_color=TEXT_DIM).pack(anchor='w', pady=(4, 0))

        self._build_update_card(content)

        # ── Download Settings Card ──────────────────────────────────
        grp = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                            border_width=1, border_color=BORDER_CARD)
        grp.pack(fill='x', pady=(0, 16))

        # Card header
        grp_hdr = ctk.CTkFrame(grp, fg_color='transparent')
        grp_hdr.pack(fill='x', padx=20, pady=(16, 12))
        ctk.CTkLabel(grp_hdr, text=T('download_settings'),
                     font=(ui_font(), 15, 'bold'),
                     text_color=TEXT_PRI).pack(side='left')

        ctk.CTkFrame(grp, height=1, fg_color=BORDER).pack(fill='x', padx=20)

        # Save location
        row_dest = ctk.CTkFrame(grp, fg_color='transparent')
        row_dest.pack(fill='x', padx=20, pady=(16, 2))
        ctk.CTkLabel(row_dest, text=T('save_location_setting'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        ctk.CTkEntry(row_dest, textvariable=self._dest_var,
                     height=38, corner_radius=CONTROL_RADIUS,
                     fg_color=BG_INPUT, border_color=BORDER, border_width=1,
                     text_color=TEXT_PRI).pack(side='left', fill='x',
                                               expand=True, padx=10)
        ctk.CTkButton(row_dest, text=T('browse_folder'), width=60, height=34, corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._pick_dest).pack(side='left')
        ctk.CTkLabel(grp, text=T('save_location_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=(136, 0), pady=(0, 10))

        # Speed limit
        row_speed = ctk.CTkFrame(grp, fg_color='transparent')
        row_speed.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(row_speed, text=T('speed_limit_setting'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        ctk.CTkOptionMenu(row_speed, values=self._speed_values(),
                          variable=self._speed_var,
                          command=self._on_speed_change, width=130, height=34,
                          corner_radius=8,
                          fg_color=BG_INPUT, button_color=BORDER_HOVER,
                          button_hover_color=ACCENT, text_color=TEXT_PRI,
                          dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
                          dropdown_text_color=TEXT_PRI).pack(side='left', padx=10)
        ctk.CTkLabel(grp, text=T('speed_limit_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=(136, 0), pady=(0, 10))

        # Concurrent downloads
        row_conc = ctk.CTkFrame(grp, fg_color='transparent')
        row_conc.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(row_conc, text=T('concurrent_setting'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        self._conc_var = ctk.StringVar(value=str(self._dlmgr.max_concurrent))
        self._conc_entry = ctk.CTkEntry(
            row_conc, textvariable=self._conc_var, width=80, height=34,
            corner_radius=8, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, justify='center')
        self._conc_entry.pack(side='left', padx=10)
        self._conc_entry.bind('<Return>', self._on_conc_change)
        self._conc_entry.bind('<FocusOut>', self._on_conc_change)
        ctk.CTkLabel(row_conc, text=T('max_n', n=MAX_CONCURRENT),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(side='left')
        ctk.CTkLabel(grp, text=T('concurrent_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=(136, 0), pady=(0, 10))

        # Segment workers used inside each individual video download
        row_workers = ctk.CTkFrame(grp, fg_color='transparent')
        row_workers.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(
            row_workers, text=T('max_workers_per_video_setting'),
            text_color=TEXT_PRI, font=(ui_font(), 12, 'bold'),
            width=116, anchor='w').pack(side='left')
        self._workers_var = ctk.StringVar(
            value=str(config.get_max_workers_per_video()))
        self._workers_entry = ctk.CTkEntry(
            row_workers, textvariable=self._workers_var, width=80, height=34,
            corner_radius=8, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, justify='center')
        self._workers_entry.pack(side='left', padx=10)
        self._workers_entry.bind('<Return>', self._on_workers_change)
        self._workers_entry.bind('<FocusOut>', self._on_workers_change)
        ctk.CTkLabel(
            row_workers,
            text=T('max_n', n=config.MAX_WORKERS_PER_VIDEO),
            text_color=TEXT_DIM, font=(ui_font(), 10)).pack(side='left')
        ctk.CTkLabel(
            grp,
            text=T(
                'max_workers_per_video_desc',
                n=config.MAX_WORKERS_PER_VIDEO),
            text_color=TEXT_DIM, font=(ui_font(), 10),
            wraplength=SETTINGS_INLINE_HELP_WRAP,
            justify='left', anchor='w').pack(
                anchor='w', padx=(136, 20), pady=(0, 10))

        # Resolution preference
        row_res = ctk.CTkFrame(grp, fg_color='transparent')
        row_res.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(row_res, text=T('resolution_setting'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        self._res_var = ctk.StringVar(value=self._resolution_label())
        ctk.CTkOptionMenu(row_res,
                          values=self._resolution_values(),
                          variable=self._res_var,
                          command=self._on_res_change, width=180, height=34,
                          corner_radius=8,
                          fg_color=BG_INPUT, button_color=BORDER_HOVER,
                          button_hover_color=ACCENT, text_color=TEXT_PRI,
                          dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
                          dropdown_text_color=TEXT_PRI).pack(side='left', padx=10)
        ctk.CTkLabel(grp, text=T('resolution_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=(136, 0), pady=(0, 10))

        # Shared output filename policy (Browser and Watcher).
        row_filename = ctk.CTkFrame(grp, fg_color='transparent')
        row_filename.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(
            row_filename, text=T('filename_mode_setting'),
            text_color=TEXT_PRI, font=(ui_font(), 12, 'bold'),
            width=116, anchor='w').pack(side='left')
        self._filename_mode_var = ctk.StringVar(
            value=self._filename_mode_label())
        ctk.CTkOptionMenu(
            row_filename, values=self._filename_mode_values(),
            variable=self._filename_mode_var,
            command=self._on_filename_mode_change, width=230, height=34,
            corner_radius=8, fg_color=BG_INPUT,
            button_color=BORDER_HOVER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI).pack(side='left', padx=10)
        ctk.CTkLabel(
            grp, text=T('filename_mode_desc'), text_color=TEXT_DIM,
            font=(ui_font(), 10), wraplength=SETTINGS_INLINE_HELP_WRAP,
            justify='left', anchor='w').pack(
                anchor='w', padx=(136, 20), pady=(0, 10))

        # Selectable sidecar subtitles generated after each completed video
        row_subtitle = ctk.CTkFrame(grp, fg_color='transparent')
        row_subtitle.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(row_subtitle, text=T('subtitle_setting'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        self._subtitle_var = ctk.StringVar(value=self._subtitle_label())
        ctk.CTkOptionMenu(
            row_subtitle, values=self._subtitle_values(),
            variable=self._subtitle_var, command=self._on_subtitle_change,
            width=230, height=34, corner_radius=8,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI).pack(side='left', padx=10)

        row_recognition = ctk.CTkFrame(grp, fg_color='transparent')
        row_recognition.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(
            row_recognition, text=T('recognition_quality_setting'),
            text_color=TEXT_PRI, font=(ui_font(), 12, 'bold'),
            width=116, anchor='w').pack(side='left')
        self._recognition_quality_var = ctk.StringVar(
            value=self._recognition_quality_label())
        ctk.CTkOptionMenu(
            row_recognition, values=self._recognition_quality_values(),
            variable=self._recognition_quality_var,
            command=self._on_recognition_quality_change,
            width=230, height=34, corner_radius=8,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI).pack(side='left', padx=10)
        ctk.CTkLabel(
            grp, text=T('recognition_quality_desc'),
            text_color=TEXT_DIM, font=(ui_font(), 9),
            wraplength=SETTINGS_INLINE_HELP_WRAP,
            justify='left', anchor='w').pack(
                anchor='w', padx=(136, 20), pady=(0, 4))

        row_translation = ctk.CTkFrame(grp, fg_color='transparent')
        row_translation.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(
            row_translation, text=T('translation_provider_setting'),
            text_color=TEXT_PRI, font=(ui_font(), 12, 'bold'),
            width=116, anchor='w').pack(side='left')
        self._translation_provider_status_lbl = ctk.CTkLabel(
            row_translation, text=translation_provider_summary(),
            text_color=TEXT_SEC, font=(ui_font(), 10),
            anchor='w')
        self._translation_provider_status_lbl.pack(
            side='left', fill='x', expand=True, padx=10)
        ctk.CTkButton(
            row_translation, text=T('translation_provider_configure'),
            width=86, height=34, corner_radius=8,
            fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(ui_font(), 10, 'bold'),
            command=self._open_translation_settings).pack(side='right')
        ctk.CTkLabel(
            grp, text=T('subtitle_desc'), text_color=TEXT_DIM,
            font=(ui_font(), 10),
            wraplength=SETTINGS_INLINE_HELP_WRAP,
            justify='left', anchor='w').pack(
                anchor='w', padx=(136, 20), pady=(0, 22))

        # App-scoped network proxy
        proxy = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                             border_width=1, border_color=BORDER_CARD)
        proxy.pack(fill='x', pady=(0, 16))

        proxy_hdr = ctk.CTkFrame(proxy, fg_color='transparent')
        proxy_hdr.pack(fill='x', padx=20, pady=(16, 4))
        ctk.CTkLabel(proxy_hdr, text=T('proxy_card_title'),
                     font=(ui_font(), 15, 'bold'),
                     text_color=TEXT_PRI).pack(side='left')
        ctk.CTkLabel(proxy, text=T('proxy_card_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=20, pady=(0, 12))

        ctk.CTkFrame(proxy, height=1, fg_color=BORDER).pack(fill='x', padx=20)

        self._proxy_var = ctk.StringVar(value=config.get_proxy_url())
        row_proxy = ctk.CTkFrame(proxy, fg_color='transparent')
        row_proxy.pack(fill='x', padx=20, pady=(16, 2))
        ctk.CTkLabel(row_proxy, text=T('proxy_url_label'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        ctk.CTkEntry(
            row_proxy, textvariable=self._proxy_var,
            placeholder_text=T('proxy_url_placeholder'),
            height=34, corner_radius=8,
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
            text_color=TEXT_PRI).pack(side='left', fill='x', expand=True, padx=10)

        proxy_actions = ctk.CTkFrame(proxy, fg_color='transparent')
        proxy_actions.pack(fill='x', padx=20, pady=(10, 2))
        ctk.CTkButton(
            proxy_actions, text=T('proxy_save'), width=70, height=34,
            corner_radius=8, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=WHITE, command=self._on_proxy_save).pack(
                side='left', padx=(126, 6))
        ctk.CTkButton(
            proxy_actions, text=T('proxy_windows'), width=86, height=34,
            corner_radius=8, fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI,
            command=self._on_proxy_windows).pack(side='left', padx=(0, 6))
        ctk.CTkButton(
            proxy_actions, text=T('proxy_clear'), width=70, height=34,
            corner_radius=8, fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, command=self._on_proxy_clear).pack(side='left')
        self._proxy_status_lbl = ctk.CTkLabel(
            proxy_actions, text='', text_color=TEXT_SEC, font=(ui_font(), 10))
        self._proxy_status_lbl.pack(side='left', padx=12)
        self._refresh_proxy_status()

        ctk.CTkLabel(proxy, text=T('proxy_help'),
                     text_color=TEXT_DIM, font=(ui_font(), 9),
                     wraplength=720, justify='left').pack(
                         anchor='w', padx=20, pady=(6, 18))

        # Cloudflare bypass
        cf = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                          border_width=1, border_color=BORDER_CARD)
        cf.pack(fill='x', pady=(0, 16))

        cf_hdr = ctk.CTkFrame(cf, fg_color='transparent')
        cf_hdr.pack(fill='x', padx=20, pady=(16, 4))
        ctk.CTkLabel(cf_hdr, text=T('cf_card_title'),
                     font=(ui_font(), 15, 'bold'),
                     text_color=TEXT_PRI).pack(side='left')
        ctk.CTkLabel(cf, text=T('cf_card_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=20, pady=(0, 12))

        ctk.CTkFrame(cf, height=1, fg_color=BORDER).pack(fill='x', padx=20)

        hosts = sorted({h for mirrors in config.MIRRORS.values() for h in mirrors})
        default_host = hosts[0] if hosts else ''
        self._cf_host_var = ctk.StringVar(value=default_host)
        self._cf_cookie_var = ctk.StringVar()
        self._cf_ua_var = ctk.StringVar()

        row_host = ctk.CTkFrame(cf, fg_color='transparent')
        row_host.pack(fill='x', padx=20, pady=(16, 2))
        ctk.CTkLabel(row_host, text=T('cf_host_label'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        ctk.CTkOptionMenu(row_host, values=hosts,
                          variable=self._cf_host_var,
                          command=self._on_cf_host_change, width=220, height=34,
                          corner_radius=8,
                          fg_color=BG_INPUT, button_color=BORDER_HOVER,
                          button_hover_color=ACCENT, text_color=TEXT_PRI,
                          dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
                          dropdown_text_color=TEXT_PRI).pack(side='left', padx=10)

        row_cookie = ctk.CTkFrame(cf, fg_color='transparent')
        row_cookie.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(row_cookie, text=T('cf_cookie_label'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        ctk.CTkEntry(row_cookie, textvariable=self._cf_cookie_var,
                     height=34, corner_radius=8,
                     fg_color=BG_INPUT, border_color=BORDER, border_width=1,
                     text_color=TEXT_PRI).pack(side='left', fill='x',
                                               expand=True, padx=10)

        row_ua = ctk.CTkFrame(cf, fg_color='transparent')
        row_ua.pack(fill='x', padx=20, pady=(8, 2))
        ctk.CTkLabel(row_ua, text=T('cf_ua_label'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        ctk.CTkEntry(row_ua, textvariable=self._cf_ua_var,
                     height=34, corner_radius=8,
                     fg_color=BG_INPUT, border_color=BORDER, border_width=1,
                     text_color=TEXT_PRI).pack(side='left', fill='x',
                                               expand=True, padx=10)

        row_actions = ctk.CTkFrame(cf, fg_color='transparent')
        row_actions.pack(fill='x', padx=20, pady=(10, 2))
        ctk.CTkButton(row_actions, text=T('cf_save'), width=70, height=34, corner_radius=8,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=WHITE,
                      command=self._on_cf_save).pack(side='left', padx=(126, 6))
        ctk.CTkButton(row_actions, text=T('cf_clear'), width=70, height=34, corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._on_cf_clear).pack(side='left')

        self._cf_status_lbl = ctk.CTkLabel(cf, text='', text_color=TEXT_SEC,
                                           font=(ui_font(), 10))
        self._cf_status_lbl.pack(anchor='w', padx=(146, 20), pady=(6, 4))

        ctk.CTkLabel(cf, text=T('cf_help'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 9),
                     wraplength=720,
                     justify='left').pack(anchor='w', padx=20, pady=(4, 18))

        self._on_cf_host_change(default_host)
        self._refresh_cf_status()

        # Saved download queue
        queue = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                             border_width=1, border_color=BORDER_CARD)
        queue.pack(fill='x', pady=(0, 16))

        queue_hdr = ctk.CTkFrame(queue, fg_color='transparent')
        queue_hdr.pack(fill='x', padx=20, pady=(16, 4))
        ctk.CTkLabel(queue_hdr, text=T('queue_card_title'),
                     font=(ui_font(), 15, 'bold'),
                     text_color=TEXT_PRI).pack(side='left')
        ctk.CTkLabel(queue, text=T('queue_card_desc'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', padx=20, pady=(0, 12))

        ctk.CTkFrame(queue, height=1, fg_color=BORDER).pack(fill='x', padx=20)

        row_queue_path = ctk.CTkFrame(queue, fg_color='transparent')
        row_queue_path.pack(fill='x', padx=20, pady=(16, 2))
        ctk.CTkLabel(row_queue_path, text=T('queue_path_label'), text_color=TEXT_PRI,
                     font=(ui_font(), 12, 'bold'), width=116,
                     anchor='w').pack(side='left')
        queue_path_entry = ctk.CTkEntry(
            row_queue_path, height=34, corner_radius=8,
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
            text_color=TEXT_PRI)
        queue_path_entry.pack(side='left', fill='x', expand=True, padx=10)
        queue_path_entry.insert(0, config.queue_csv_path())
        queue_path_entry.configure(state='readonly')

        row_queue_actions = ctk.CTkFrame(queue, fg_color='transparent')
        row_queue_actions.pack(fill='x', padx=20, pady=(10, 18))
        ctk.CTkButton(row_queue_actions, text=T('open_queue_folder'),
                      width=110, height=34, corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                      command=self._open_queue_folder).pack(side='left', padx=(126, 6))
        ctk.CTkButton(row_queue_actions, text=T('clear_saved_queue'),
                      width=140, height=34, corner_radius=8,
                      fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                      hover_color=BG_CARD_HOVER, text_color=ERROR_C,
                      command=self._clear_saved_queue).pack(side='left')

        # ── About Card ──────────────────────────────────────────────
        about = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                              border_width=1, border_color=BORDER_CARD)
        about.pack(fill='x', pady=(0, 16))

        about_hdr = ctk.CTkFrame(about, fg_color='transparent')
        about_hdr.pack(fill='x', padx=20, pady=(16, 12))
        ctk.CTkLabel(about_hdr, text=T('about'),
                     font=(ui_font(), 15, 'bold'),
                     text_color=TEXT_PRI).pack(side='left')

        ctk.CTkFrame(about, height=1, fg_color=BORDER).pack(fill='x', padx=20)

        about_body = ctk.CTkFrame(about, fg_color='transparent')
        about_body.pack(fill='x', padx=20, pady=16)

        ctk.CTkLabel(about_body, text=PRODUCT_NAME,
                     text_color=TEXT_PRI,
                     font=(ui_font(), 15, 'bold')).pack(anchor='w')
        ctk.CTkLabel(
            about_body,
            text='UAV Browser  ·  JableTV / MissAV / SupJav / Hanime1',
                     text_color=ACCENT,
                     font=(ui_font(), 12)).pack(anchor='w', pady=(6, 0))

        # Version badge
        ver_badge = ctk.CTkFrame(about_body, fg_color=BG_BADGE, corner_radius=4)
        ver_badge.pack(anchor='w', pady=(10, 0))
        ctk.CTkLabel(ver_badge, text=f'v{APP_VERSION}',
                     text_color=TEXT_SEC,
                     font=('Consolas', 10)).pack(padx=10, pady=4)

        ctk.CTkLabel(about_body, text=T('disclaimer'),
                     text_color=TEXT_DIM,
                     font=(ui_font(), 10)).pack(anchor='w', pady=(10, 0))

    # ── Browse logic ─────────────────────────────────────────────────
    def _load_categories(self):
        if self._is_closing:
            return
        _crumb("load_categories: site=%s" % self._site_key)
        self._page_req += 1
        my_req = self._page_req
        my_gen = self._build_gen
        site_key = self._site_key
        browser = SITES[site_key]['browser']
        missav_lang = T('missav_lang')
        supjav_lang = T('supjav_lang')

        def _fetch():
            failed = False
            try:
                if site_key == 'MissAV':
                    cats = browser.fetch_categories(lang=missav_lang)
                elif site_key == 'SupJav':
                    cats = browser.fetch_categories(lang=supjav_lang)
                else:
                    cats = browser.fetch_categories()
            except Exception:
                failed = True
                cats = []
            if not cats and hasattr(browser, 'HOMEPAGE_SECTIONS'):
                cats = [{'name': site_i18n.loc(site_i18n.CATEGORY_I18N, url, name),
                         'url': url, 'count': 0, 'section': True}
                        for name, url in browser.HOMEPAGE_SECTIONS]

            def _apply():
                if self._is_closing or my_req != self._page_req or my_gen != self._build_gen:
                    return
                self._categories = cats
                if cats and not failed:
                    self._current_base_url = cats[0]['url']
                    self._page = 1
                    self._last_loaded_page = 1
                    self._has_next = True
                    self._browse_blocked = False
                    self._browse_empty_message = ''
                    self._update_cat_menu([c['name'] for c in cats])
                    self._load_page()
                    return
                if cats:
                    self._current_base_url = cats[0]['url']
                    self._update_cat_menu([c['name'] for c in cats])
                else:
                    self._current_base_url = ''
                    self._cat_menu.configure(values=[])
                    self._cat_var.set('')
                self._videos = []
                self._has_next = False
                self._browse_blocked = False
                self._browse_empty_message = T('category_load_failed')
                self._status_lbl.configure(text=T('category_load_failed'))
                self._refresh_grid()

            self._ui(_apply, gen=my_gen)

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_cat_menu(self, names: list[str]):
        self._cat_menu.configure(values=names)
        if names:
            self._cat_var.set(names[0])

    def _load_page(self):
        if not self._current_base_url:
            return
        self._page_req += 1
        my_req = self._page_req
        my_gen = self._build_gen
        site_key = self._site_key
        browser = SITES[site_key]['browser']
        base = self._current_base_url
        page_snapshot = self._page
        if site_key == 'JableTV':
            if '?' in base:
                url = f'{base}&from={page_snapshot}'
            else:
                url = f'{base.rstrip("/")}/?from={page_snapshot}'
        else:
            url = browser.page_url(base, page_snapshot)

        def _fetch():
            blocked = False
            try:
                data = fetch_page_data(browser, url)
                videos = data.get('videos', [])
            except MirrorsBlockedError:
                blocked = True
                videos = []
            self._ui(
                lambda: self._apply_page(my_req, videos, page_snapshot, blocked, my_gen),
                gen=my_gen)

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_page(self, req: int, videos: list[dict], page_snapshot: int,
                    blocked: bool = False, gen: int | None = None):
        if self._is_closing or req != self._page_req:
            return
        if gen is not None and gen != self._build_gen:
            return
        if not videos and page_snapshot > 1 and not blocked:
            self._page = self._last_loaded_page
            self._has_next = False
            self._page_lbl.configure(text=T('page_n', n=self._page))
            return
        self._videos = videos
        self._browse_blocked = blocked
        self._browse_empty_message = ''
        self._has_next = bool(videos)
        if videos:
            self._last_loaded_page = page_snapshot
            self._page = page_snapshot
        _crumb("apply_page: %d videos -> refresh_grid" % len(videos))
        self._refresh_grid()
        _crumb("apply_page: grid refreshed")
        self._page_lbl.configure(text=T('page_n', n=self._page))

    def _video_version_badge(self, url: str, title: str):
        url_l = (url or '').lower()
        title_l = (title or '').lower()
        path = url_l.split('?', 1)[0].rstrip('/')
        if ('uncensored-leak' in url_l or '無碼' in title_l or
                '无码' in title_l or 'uncensored' in title_l):
            return '無碼', '#C2410C'
        if ('chinese-subtitle' in url_l or path.endswith('-c') or
                '-c/' in url_l or '中文字幕' in title_l or '中字' in title_l):
            return '中字', '#0E7490'
        return None

    def _refresh_grid(self):
        try:
            for w in self._grid_scroll.winfo_children():
                w.destroy()
        except (AttributeError, tk.TclError):
            return
        self._card_widgets = {}
        self._grid_gen += 1
        gen = self._grid_gen
        build_gen = self._build_gen

        if not self._videos:
            if self._browse_blocked:
                msg = T('mirrors_blocked')
            else:
                msg = self._browse_empty_message or T('no_results')
            ctk.CTkLabel(self._grid_scroll, text=msg,
                         text_color=TEXT_DIM,
                         font=(ui_font(), 14)).pack(pady=40)
            return

        # Responsive card density: 2 compact / 3 default / 4 wide.
        columns = max(2, self._grid_columns)
        try:
            logical_width = self.winfo_width() / max(self._get_window_scaling(), 1.0)
        except Exception:
            logical_width = self.winfo_width()
        estimated_card_width = max(
            240, int((max(logical_width, 980) - 240) / columns) - 24)
        title_wrap = max(180, min(330, estimated_card_width - 34))
        row_frame = None
        for i, v in enumerate(self._videos):
            if i % columns == 0:
                row_frame = ctk.CTkFrame(self._grid_scroll, fg_color='transparent')
                row_frame.pack(fill='x', padx=14, pady=7)

            url = v.get('url', '')
            title = v.get('title', '')
            dur = v.get('duration', '')
            thumb_url = v.get('thumbnail', '')
            is_sel = url in self._selected_urls

            card = ctk.CTkFrame(row_frame, fg_color=ACCENT_DIM if is_sel else BG_CARD,
                                corner_radius=CARD_RADIUS,
                                border_width=2 if is_sel else 1,
                                border_color=ACCENT if is_sel else BORDER_CARD)
            card.pack(side='left', padx=7, pady=7, fill='x', expand=True)

            # Thumbnail placeholder (16:9)
            thumb_holder = ctk.CTkFrame(card, fg_color=BG_SIDEBAR,
                                         height=_THUMB_SIZE[1], corner_radius=6)
            thumb_holder.pack(fill='x', padx=10, pady=(10, 0))
            thumb_holder.pack_propagate(False)
            thumb_lbl = ctk.CTkLabel(thumb_holder, text=T('loading_browse'),
                                      text_color=TEXT_DIM,
                                      fg_color='transparent',
                                      font=(ui_font(), 11))
            thumb_lbl.pack(expand=True)

            # Duration badge
            if dur:
                dur_lbl = ctk.CTkLabel(thumb_holder, text=f' {dur} ',
                                        text_color='#FFFFFF',
                                        fg_color='#000000',
                                        corner_radius=4,
                                        font=('Consolas', 9, 'bold'))
                dur_lbl.place(relx=1.0, rely=1.0, anchor='se', x=-6, y=-6)

            # Title
            title_text = title[:72] + '...' if len(title) > 72 else title
            title_row = ctk.CTkFrame(card, fg_color='transparent')
            title_row.pack(fill='x', padx=12, pady=(10, 5))
            version_badge = self._video_version_badge(url, title)
            wrap = title_wrap - 44 if version_badge else title_wrap
            if version_badge:
                badge_text, badge_color = version_badge
                ctk.CTkLabel(title_row, text=badge_text,
                             text_color='#FFFFFF', fg_color=badge_color,
                             corner_radius=4, width=34, height=18,
                             font=(ui_font(), 9, 'bold')).pack(
                    side='left', padx=(0, 6), anchor='n')
            ctk.CTkLabel(title_row, text=title_text, text_color=TEXT_PRI,
                         font=(ui_font(), 11),
                         wraplength=wrap, justify='left').pack(
                side='left', fill='x', expand=True, anchor='w')

            # Bottom row
            bottom = ctk.CTkFrame(card, fg_color='transparent')
            bottom.pack(fill='x', padx=12, pady=(0, 12))

            sel_text = ('✓ ' + T('selected')) if is_sel else T('select')
            sel_btn = ctk.CTkButton(
                bottom, text=sel_text, width=76, height=30,
                corner_radius=CONTROL_RADIUS,
                fg_color=ACCENT if is_sel else 'transparent',
                border_width=0 if is_sel else 1,
                border_color=BORDER_HOVER,
                hover_color=ACCENT_HOVER if is_sel else BG_CARD_HOVER,
                text_color=('#FFFFFF', '#FFFFFF') if is_sel else TEXT_PRI,
                font=(ui_font(), 10, 'bold') if is_sel else (ui_font(), 10),
                command=lambda u=url: self._toggle_select(u)
            )
            sel_btn.pack(side='right')

            self._card_widgets[url] = {'card': card, 'sel_btn': sel_btn}

            # Clickable card
            def _bind_click(widget, video_url=url):
                widget.bind('<Button-1>', lambda e, u=video_url: self._toggle_select(u))
                widget.configure(cursor='hand2')
            _bind_click(card)
            _bind_click(thumb_holder)
            _bind_click(thumb_lbl)

            # Background thumbnail load
            if thumb_url:
                self._load_thumb_async(
                    thumb_url, thumb_lbl, gen, build_gen, self._site_key)
            else:
                thumb_lbl.configure(text=T('no_thumbnail'))

    def _load_thumb_async(self, thumb_url: str, label: ctk.CTkLabel,
                          gen: int, build_gen: int, site_key: str = ''):
        """Fetch thumbnail in a background thread; marshal result back to the
        main thread via .after() so Tk widget updates stay thread-safe.
        The gen counter prevents stale thumbs from polluting a newer page."""
        def _worker():
            if self._is_closing or gen != self._grid_gen or build_gen != self._build_gen:
                return
            img = _fetch_thumbnail(thumb_url, site_key)
            if img is None:
                def _apply_missing():
                    if (self._is_closing or gen != self._grid_gen or
                            build_gen != self._build_gen):
                        return
                    try:
                        if label.winfo_exists():
                            label.configure(text=T('no_thumbnail'), image=None)
                    except Exception:
                        pass
                self._ui(_apply_missing, gen=build_gen)
                return
            # Only apply if this label is still part of the current page.
            def _apply():
                if self._is_closing or gen != self._grid_gen or build_gen != self._build_gen:
                    return
                try:
                    if not label.winfo_exists():
                        return
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                            size=img.size)
                    label.configure(image=ctk_img, text='')
                    # Keep a reference on the widget so GC doesn't reclaim it
                    label._ctk_img_ref = ctk_img
                except Exception:
                    pass
            self._ui(_apply, gen=build_gen)
        try:
            self._thumb_executor.submit(_worker)
        except RuntimeError:
            pass

    def _source_subtitle_evidence_for_video(self, video: dict):
        candidate = dict(video or {})
        candidate['_site'] = self._site_key
        candidate['_source_listing_url'] = self._current_base_url
        return trusted_chinese_subtitle_evidence(candidate)

    def _toggle_select(self, url: str):
        if url in self._selected_urls:
            self._selected_urls.discard(url)
            self._selected_source_subtitle_evidence.pop(url, None)
        else:
            self._selected_urls.add(url)
            video = next(
                (item for item in self._videos
                 if item.get('url', '') == url),
                {'url': url},
            )
            evidence = self._source_subtitle_evidence_for_video(video)
            if evidence:
                self._selected_source_subtitle_evidence[url] = evidence
        # Update the specific card in-place (no full grid rebuild)
        w = self._card_widgets.get(url)
        if w:
            is_sel = url in self._selected_urls
            try:
                w['card'].configure(
                    fg_color=ACCENT_DIM if is_sel else BG_CARD,
                    border_width=2 if is_sel else 1,
                    border_color=ACCENT if is_sel else BORDER_CARD)
                w['sel_btn'].configure(
                    text=('✓ ' + T('selected')) if is_sel else T('select'),
                    fg_color=ACCENT if is_sel else 'transparent',
                    border_width=0 if is_sel else 1,
                    hover_color=ACCENT_HOVER if is_sel else BG_CARD_HOVER,
                    text_color=WHITE if is_sel else TEXT_PRI,
                    font=(ui_font(), 10, 'bold') if is_sel else (ui_font(), 10))
            except Exception:
                pass
        self._update_selection_count()

    def _update_selection_count(self):
        n = len(self._selected_urls)
        self._sel_lbl.configure(
            text=f'{n} {T("selected")}',
            text_color=ACCENT if n else TEXT_SEC)

    def _set_card_selected(self, url: str, is_sel: bool):
        w = self._card_widgets.get(url)
        if not w:
            return
        try:
            w['card'].configure(
                fg_color=ACCENT_DIM if is_sel else BG_CARD,
                border_width=2 if is_sel else 1,
                border_color=ACCENT if is_sel else BORDER_CARD)
            w['sel_btn'].configure(
                text=('✓ ' + T('selected')) if is_sel else T('select'),
                fg_color=ACCENT if is_sel else 'transparent',
                border_width=0 if is_sel else 1,
                hover_color=ACCENT_HOVER if is_sel else BG_CARD_HOVER,
                text_color=WHITE if is_sel else TEXT_PRI,
                font=(ui_font(), 10, 'bold') if is_sel else (ui_font(), 10))
        except Exception:
            pass

    def _clear_selection_in_place(self):
        selected = list(self._selected_urls)
        self._selected_urls.clear()
        self._selected_source_subtitle_evidence.clear()
        for url in selected:
            self._set_card_selected(url, False)
        self._update_selection_count()

    def _goto_page(self, p: int):
        if p < 1:
            return
        if p > self._page and not self._has_next:
            return
        self._page = p
        self._load_page()

    def _jump_to_page(self):
        """Jump to page number entered in the page-jump field."""
        try:
            p = int(self._page_jump_var.get().strip())
            if p >= 1 and not (p > self._page and not self._has_next):
                self._goto_page(p)
        except (ValueError, TypeError):
            pass
        self._page_jump_var.set('')

    def _select_all_on_page(self):
        """Select all videos currently displayed on the page."""
        for v in self._videos:
            url = v.get('url', '')
            if url:
                self._selected_urls.add(url)
                evidence = self._source_subtitle_evidence_for_video(v)
                if evidence:
                    self._selected_source_subtitle_evidence[url] = evidence
                self._set_card_selected(url, True)
        self._update_selection_count()

    def _hanime_filters_active(self) -> bool:
        return any(
            self._hanime_filter_state.get(key)
            for key in ('query', 'genre', 'sort', 'date', 'duration', 'tags')
        )

    def _refresh_hanime_filter_button(self):
        button = getattr(self, '_hanime_filter_btn', None)
        if button is None:
            return
        active = self._hanime_filters_active()
        try:
            button.configure(
                text=(T('hanime_filter_active') if active else
                      T('hanime_filter_button')),
                fg_color=ACCENT_DIM if active else 'transparent',
                border_color=ACCENT if active else BORDER_HOVER,
                text_color=ACCENT if active else TEXT_PRI,
            )
        except tk.TclError:
            pass

    def _sync_hanime_filter_button_visibility(self):
        box = getattr(self, '_hanime_filter_box', None)
        search_entry = getattr(self, '_search_entry', None)
        if box is None or search_entry is None:
            return
        try:
            if self._site_key == 'Hanime1':
                if not box.winfo_manager():
                    box.pack(
                        side='left', padx=(10, 0), before=search_entry)
                self._refresh_hanime_filter_button()
            else:
                box.pack_forget()
        except tk.TclError:
            pass

    def _reset_hanime_filter_state(self):
        self._hanime_filter_state = {
            'query': '', 'genre': '', 'sort': '', 'date': '',
            'duration': '', 'tags': (),
        }
        self._refresh_hanime_filter_button()

    def _apply_hanime_filter_state(self):
        state = self._hanime_filter_state
        self._current_base_url = Hanime1Browser.filter_url(
            query=state.get('query', ''),
            genre=state.get('genre', ''),
            sort=state.get('sort', ''),
            date=state.get('date', ''),
            duration=state.get('duration', ''),
            tags=state.get('tags', ()),
        )
        self._page = 1
        self._last_loaded_page = 1
        self._has_next = True
        self._browse_blocked = False
        self._browse_empty_message = ''
        self._selected_urls.clear()
        self._selected_source_subtitle_evidence.clear()
        self._update_selection_count()
        self._search_var.set(state.get('query', ''))
        self._cat_var.set(T('hanime_filter_result'))
        self._refresh_hanime_filter_button()
        self._load_page()

    def _open_hanime_filters(self):
        if self._site_key != 'Hanime1':
            return
        existing = self._hanime_filter_popup
        try:
            if existing is not None and existing.winfo_exists():
                existing.focus_force()
                return
        except tk.TclError:
            pass

        catalog = Hanime1Browser.filter_catalog()
        popup = ctk.CTkToplevel(self)
        self._hanime_filter_popup = popup
        popup.title(T('hanime_filter_title'))
        popup.geometry('900x720')
        popup.minsize(760, 560)
        popup.transient(self)
        popup.configure(fg_color=BG_DARK)
        popup.grid_columnconfigure(0, weight=1)
        popup.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(popup, fg_color=BG_CARD, corner_radius=0)
        header.grid(row=0, column=0, sticky='ew')
        ctk.CTkLabel(
            header, text=T('hanime_filter_title'), text_color=TEXT_PRI,
            font=(ui_font(), 18, 'bold')).pack(
                anchor='w', padx=22, pady=(18, 4))
        ctk.CTkLabel(
            header, text=T('hanime_filter_desc'), text_color=TEXT_DIM,
            font=(ui_font(), 10), justify='left', wraplength=840).pack(
                anchor='w', padx=22, pady=(0, 16))

        fields = ctk.CTkFrame(
            popup, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        fields.grid(row=1, column=0, padx=18, pady=(16, 6), sticky='ew')
        for column in (1, 3):
            fields.grid_columnconfigure(column, weight=1)

        any_value = T('hanime_filter_any')
        state = self._hanime_filter_state
        query_var = tk.StringVar(
            value=state.get('query') or self._search_var.get().strip())
        genre_var = tk.StringVar(value=state.get('genre') or any_value)
        sort_var = tk.StringVar(value=state.get('sort') or any_value)
        date_var = tk.StringVar(value=state.get('date') or any_value)
        duration_var = tk.StringVar(value=state.get('duration') or any_value)

        def add_entry(row, column, label_key, variable):
            ctk.CTkLabel(
                fields, text=T(label_key), text_color=TEXT_SEC,
                font=(ui_font(), 10, 'bold'), anchor='w').grid(
                    row=row, column=column, padx=(14, 8), pady=7,
                    sticky='w')
            entry = ctk.CTkEntry(
                fields, textvariable=variable, height=34,
                corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
                border_color=BORDER, border_width=1,
                text_color=TEXT_PRI, font=(ui_font(), 10))
            entry.grid(
                row=row, column=column + 1, padx=(0, 14), pady=7,
                sticky='ew')
            return entry

        def add_menu(row, column, label_key, variable, values):
            ctk.CTkLabel(
                fields, text=T(label_key), text_color=TEXT_SEC,
                font=(ui_font(), 10, 'bold'), anchor='w').grid(
                    row=row, column=column, padx=(14, 8), pady=7,
                    sticky='w')
            menu = ctk.CTkOptionMenu(
                fields, variable=variable, values=[any_value, *values],
                height=34, corner_radius=CONTROL_RADIUS,
                fg_color=BG_INPUT, button_color=BORDER_HOVER,
                button_hover_color=ACCENT, text_color=TEXT_PRI,
                dropdown_fg_color=BG_CARD,
                dropdown_hover_color=BG_CARD_HOVER,
                dropdown_text_color=TEXT_PRI,
                font=(ui_font(), 9), dropdown_font=(ui_font(), 9))
            menu.grid(
                row=row, column=column + 1, padx=(0, 14), pady=7,
                sticky='ew')
            return menu

        query_entry = add_entry(
            0, 0, 'hanime_filter_query', query_var)
        add_menu(0, 2, 'hanime_filter_genre', genre_var,
                 list(catalog['genres']))
        add_menu(1, 0, 'hanime_filter_sort', sort_var,
                 list(catalog['sorts']))
        add_menu(1, 2, 'hanime_filter_date', date_var,
                 list(catalog['dates']))
        add_menu(2, 0, 'hanime_filter_duration', duration_var,
                 list(catalog['durations']))

        tags_card = ctk.CTkFrame(
            popup, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        tags_card.grid(row=2, column=0, padx=18, pady=6, sticky='nsew')
        tags_card.grid_columnconfigure(0, weight=1)
        tags_card.grid_rowconfigure(1, weight=1)

        tags_header = ctk.CTkFrame(tags_card, fg_color='transparent')
        tags_header.grid(row=0, column=0, padx=12, pady=(10, 6), sticky='ew')
        ctk.CTkLabel(
            tags_header, text=T('hanime_filter_tags'), text_color=TEXT_PRI,
            font=(ui_font(), 12, 'bold')).pack(side='left')
        selected_count = ctk.CTkLabel(
            tags_header, text='', text_color=ACCENT,
            font=(ui_font(), 9, 'bold'))
        selected_count.pack(side='left', padx=(10, 0))
        tag_search_var = tk.StringVar()
        ctk.CTkEntry(
            tags_header, textvariable=tag_search_var,
            placeholder_text=T('hanime_filter_tag_search'), width=260,
            height=32, corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1, text_color=TEXT_PRI,
            font=(ui_font(), 10)).pack(side='right')

        tag_scroll = ctk.CTkScrollableFrame(
            tags_card, fg_color='transparent', corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        tag_scroll.grid(row=1, column=0, padx=8, pady=(0, 8), sticky='nsew')

        selected_tags = set(state.get('tags') or ())
        tag_vars = {
            tag: tk.BooleanVar(value=tag in selected_tags)
            for tag in catalog['tags']
        }
        group_key = {
            'features': 'st_hanime_tags_features',
            'relationships': 'st_hanime_tags_relationships',
            'roles': 'st_hanime_tags_roles',
            'characters': 'st_hanime_tags_characters',
            'appearance': 'st_hanime_tags_appearance',
            'clothing': 'st_hanime_tags_clothing',
            'locations': 'st_hanime_tags_locations',
            'themes': 'st_hanime_tags_themes',
            'acts': 'st_hanime_tags_acts',
        }
        grouped_names = {
            tag for _group, tags in HANIME1_FILTER_TAG_GROUPS for tag in tags
        }
        tag_groups = list(HANIME1_FILTER_TAG_GROUPS)
        extra_tags = tuple(
            tag for tag in catalog['tags'] if tag not in grouped_names)
        if extra_tags:
            tag_groups.append(('other', extra_tags))
        render_after = [None]

        def update_selected_count():
            count = sum(var.get() for var in tag_vars.values())
            selected_count.configure(
                text=T('hanime_filter_selected_tags', count=count))

        def render_tags(*_args):
            render_after[0] = None
            needle = tag_search_var.get().strip().casefold()
            for child in tag_scroll.winfo_children():
                child.destroy()
            for group_id, tags in tag_groups:
                visible = [
                    tag for tag in tags
                    if tag in tag_vars and
                    (not needle or needle in tag.casefold())
                ]
                if not visible:
                    continue
                group = ctk.CTkFrame(
                    tag_scroll, fg_color=BG_SECTION, corner_radius=8,
                    border_width=1, border_color=BORDER_CARD)
                group.pack(fill='x', padx=3, pady=4)
                ctk.CTkLabel(
                    group,
                    text=(T(group_key[group_id]) if group_id in group_key
                          else T('hanime_filter_other_tags')),
                    text_color=TEXT_SEC,
                    font=(ui_font(), 10, 'bold')).pack(
                        anchor='w', padx=10, pady=(8, 4))
                grid = ctk.CTkFrame(group, fg_color='transparent')
                grid.pack(fill='x', padx=8, pady=(0, 8))
                for column in range(3):
                    grid.grid_columnconfigure(column, weight=1)
                for index, tag in enumerate(visible):
                    ctk.CTkCheckBox(
                        grid, text=tag, variable=tag_vars[tag],
                        height=26, checkbox_width=17, checkbox_height=17,
                        corner_radius=4, border_width=1,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        border_color=BORDER_HOVER, checkmark_color=WHITE,
                        text_color=TEXT_SEC, font=(ui_font(), 9),
                        command=update_selected_count).grid(
                            row=index // 3, column=index % 3, sticky='w',
                            padx=6, pady=2)

        def schedule_render(*_args):
            if render_after[0] is not None:
                try:
                    popup.after_cancel(render_after[0])
                except tk.TclError:
                    pass
            render_after[0] = popup.after(120, render_tags)

        tag_search_var.trace_add('write', schedule_render)
        render_tags()
        update_selected_count()

        footer = ctk.CTkFrame(popup, fg_color=BG_CARD, corner_radius=0)
        footer.grid(row=3, column=0, sticky='ew')

        def close_popup():
            try:
                popup.destroy()
            except tk.TclError:
                pass
            self._hanime_filter_popup = None

        def clear_form():
            query_var.set('')
            genre_var.set(any_value)
            sort_var.set(any_value)
            date_var.set(any_value)
            duration_var.set(any_value)
            tag_search_var.set('')
            for variable in tag_vars.values():
                variable.set(False)
            update_selected_count()

        def selected_value(variable):
            value = variable.get().strip()
            return '' if value == any_value else value

        def apply_filter():
            self._hanime_filter_state = {
                'query': query_var.get().strip(),
                'genre': selected_value(genre_var),
                'sort': selected_value(sort_var),
                'date': selected_value(date_var),
                'duration': selected_value(duration_var),
                'tags': tuple(
                    tag for tag, variable in tag_vars.items()
                    if variable.get()),
            }
            close_popup()
            self._apply_hanime_filter_state()

        ctk.CTkButton(
            footer, text=T('hanime_filter_apply'), width=116, height=38,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=WHITE,
            font=(ui_font(), 10, 'bold'), command=apply_filter).pack(
                side='right', padx=(8, 18), pady=12)
        ctk.CTkButton(
            footer, text=T('hanime_filter_reset'), width=92, height=38,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
            font=(ui_font(), 10), command=clear_form).pack(
                side='right', pady=12)
        ctk.CTkButton(
            footer, text=T('st_calendar_cancel'), width=92, height=38,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(ui_font(), 10), command=close_popup).pack(
                side='right', padx=(0, 8), pady=12)

        popup.protocol('WM_DELETE_WINDOW', close_popup)
        query_entry.focus_set()
        popup.update_idletasks()
        x = self.winfo_rootx() + max(
            0, (self.winfo_width() - popup.winfo_width()) // 2)
        y = self.winfo_rooty() + max(
            0, (self.winfo_height() - popup.winfo_height()) // 3)
        popup.geometry(f'+{x}+{y}')

    def _on_site_change(self, val):
        self._site_key = val
        self._reset_hanime_filter_state()
        self._sync_hanime_filter_button_visibility()
        self._categories.clear()
        self._selected_urls.clear()
        self._selected_source_subtitle_evidence.clear()
        self._update_selection_count()
        self._rebuild_sidebar()
        self._load_categories()

    def _on_cat_change(self, val):
        idx = next((i for i, c in enumerate(self._categories)
                    if c['name'] == val), -1)
        if idx < 0:
            return
        if self._site_key == 'Hanime1':
            self._reset_hanime_filter_state()
        self._current_base_url = self._categories[idx]['url']
        self._page = 1
        self._last_loaded_page = 1
        self._has_next = True
        self._browse_blocked = False
        self._browse_empty_message = ''
        self._selected_urls.clear()
        self._selected_source_subtitle_evidence.clear()
        self._update_selection_count()
        self._load_page()

    def _on_search(self):
        q = self._search_var.get().strip()
        if not q:
            return
        from urllib.parse import quote
        if self._site_key == 'JableTV':
            # JableTV does not expose language-specific listing/search variants.
            self._current_base_url = f'https://jable.tv/search/?q={quote(q, safe="")}'
        elif self._site_key == 'SupJav':
            self._current_base_url = SupJavBrowser.search_url(q, lang=T('supjav_lang'))
        elif self._site_key == 'Hanime1':
            self._hanime_filter_state['query'] = q
            self._apply_hanime_filter_state()
            return
        else:
            lang = T('missav_lang')
            eq = quote(q, safe='')
            if lang:
                self._current_base_url = f'https://missav.ai/{lang}/search/{eq}'
            else:
                self._current_base_url = f'https://missav.ai/search/{eq}'
        self._page = 1
        self._last_loaded_page = 1
        self._has_next = True
        self._browse_blocked = False
        self._browse_empty_message = ''
        self._selected_urls.clear()
        self._selected_source_subtitle_evidence.clear()
        self._update_selection_count()
        self._load_page()

    def _on_tag_click(self, url: str, name: str):
        self._current_base_url = url
        self._page = 1
        self._last_loaded_page = 1
        self._has_next = True
        self._browse_blocked = False
        self._browse_empty_message = ''
        self._selected_urls.clear()
        self._selected_source_subtitle_evidence.clear()
        self._update_selection_count()
        self._cat_var.set(f'🏷 {name}')
        self._load_page()

    # ── Sidebar ──────────────────────────────────────────────────────
    def _rebuild_sidebar(self):
        for w in self._sidebar.winfo_children():
            w.destroy()

        ctk.CTkLabel(self._sidebar, text=T('sidebar_title'),
                     text_color=ACCENT,
                     font=(ui_font(), 14, 'bold')).pack(
            anchor='w', padx=14, pady=(14, 10))

        # Subtle divider
        ctk.CTkFrame(self._sidebar, height=1,
                     fg_color=BORDER).pack(fill='x', padx=8, pady=(0, 6))

        if self._site_key != 'JableTV':
            ctk.CTkLabel(self._sidebar, text=T('tags_jable_only'),
                         text_color=TEXT_DIM,
                         font=(ui_font(), 10)).pack(pady=20)
            return

        tags = JableTVBrowser.SIDEBAR_TAGS
        for group_name, tag_list in tags.items():
            expanded = self._sidebar_expanded.get(group_name, False)
            display_group_name = site_i18n.loc(site_i18n.TAG_GROUPS, group_name, group_name)

            # Group header button
            arrow = '▾' if expanded else '▸'
            hdr = ctk.CTkButton(
                self._sidebar,
                text=f'{arrow} {display_group_name} ({len(tag_list)})',
                fg_color='transparent', hover_color=BG_CARD_HOVER,
                text_color=TEXT_SEC, anchor='w',
                font=(ui_font(), 10, 'bold'),
                height=30, corner_radius=8,
                command=lambda g=group_name: self._toggle_group(g))
            hdr.pack(fill='x', padx=6, pady=1)

            if expanded:
                for name, slug in tag_list:
                    tag_url = JableTVBrowser.tag_url(slug)
                    display_name = site_i18n.loc(site_i18n.TAGS, slug, name)
                    btn = ctk.CTkButton(
                        self._sidebar, text=display_name,
                        fg_color='transparent', hover_color=BG_CARD_HOVER,
                        text_color=TEXT_SEC, anchor='w',
                        font=(ui_font(), 10),
                        height=26, corner_radius=8,
                        command=lambda u=tag_url, n=display_name: self._on_tag_click(u, n))
                    btn.pack(fill='x', padx=(18, 6), pady=0)

    def _toggle_group(self, group: str):
        self._sidebar_expanded[group] = not self._sidebar_expanded.get(group, False)
        self._rebuild_sidebar()

    # ── Download actions ─────────────────────────────────────────────
    def _add_selected_to_queue(self):
        dest = self._dest_var.get() or 'download'
        for url in list(self._selected_urls):
            if M3U8Sites.VaildateUrl(url):
                self._dlmgr.add_item(
                    url, state='等待中', dest=dest,
                    source_subtitle_evidence=(
                        self._selected_source_subtitle_evidence.get(
                            url, ())))
        n = len(self._selected_urls)
        self._clear_selection_in_place()
        print(f'已加入 {n} 部到清單')

    def _download_selected(self):
        dest = self._dest_var.get() or 'download'
        for url in list(self._selected_urls):
            if M3U8Sites.VaildateUrl(url):
                self._dlmgr.add_item(
                    url, state='等待中', dest=dest,
                    source_subtitle_evidence=(
                        self._selected_source_subtitle_evidence.get(
                            url, ())))
                self._dlmgr.enqueue(url, dest)
        n = len(self._selected_urls)
        self._clear_selection_in_place()
        print(f'{n} 部開始下載')

    def _download_url(self):
        url = self._dl_url_var.get().strip()
        if not url:
            return
        # Direct video URL
        if M3U8Sites.VaildateUrl(url):
            dest = self._dest_var.get() or 'download'
            self._dlmgr.add_item(url, state='等待中', dest=dest)
            self._dlmgr.enqueue(url, dest)
            self._dl_url_var.set('')
            return
        # Listing / actress / category URL — crawl all videos
        if self._is_listing_url(url):
            self._dl_url_var.set('')
            self._status_lbl.configure(text=T('crawling_url'))
            dest = self._dest_var.get() or 'download'
            threading.Thread(target=self._crawl_listing, args=(url, dest),
                             daemon=True).start()
            return
        self._status_lbl.configure(text=T('url_not_supported'))
        print(T('url_not_supported') + f': {url}')

    def _is_listing_url(self, url: str) -> bool:
        """Check if URL is a supported listing/category/actress page."""
        if re.match(r'https://(?:www\.)?supjav\.com/(?:(?:zh|ja)/)?\d+\.html$', url):
            return False
        if SiteHanime1.validate_url(url):
            return False
        return (bool(re.match(r'https://(?:www\.)?(?:jable\.tv|fs1\.app)/', url)) or
                bool(re.match(r'https://(?:www\.)?(?:missav\.(?:ai|ws|live)|missav123\.com)/', url)) or
                bool(re.match(r'https://(?:www\.)?supjav\.com/', url)) or
                bool(re.match(r'https://(?:www\.)?hanime1\.me/', url)))

    def _crawl_listing(self, url: str, dest: str):
        """Crawl a listing URL across all pages; add every video to the queue."""
        gen = self._build_gen
        seen: set[str] = set()
        is_jable = bool(re.match(r'https://(?:www\.)?(?:jable\.tv|fs1\.app)/', url))
        is_supjav = bool(re.match(r'https://(?:www\.)?supjav\.com/', url))
        is_hanime1 = bool(re.match(r'https://(?:www\.)?hanime1\.me/', url))
        max_pages = 50

        for page in range(1, max_pages + 1):
            if self._is_closing:
                return
            try:
                if is_jable:
                    if page == 1:
                        page_url = url
                    elif '?' in url:
                        page_url = f'{url}&from={page}'
                    else:
                        page_url = f'{url.rstrip("/")}/?from={page}'
                    videos = JableTVBrowser.fetch_page(page_url)
                elif is_supjav:
                    page_url = SupJavBrowser.page_url(url, page)
                    videos = SupJavBrowser.fetch_page(page_url)
                elif is_hanime1:
                    page_url = Hanime1Browser.page_url(url, page)
                    videos = Hanime1Browser.fetch_page(page_url)
                else:
                    page_url = MissAVBrowser.page_url(url, page)
                    videos = MissAVBrowser.fetch_page(page_url)
            except MirrorsBlockedError as e:
                print(f'[crawl] page {page} blocked: {e}')
                self._ui(lambda: self._status_lbl.configure(text=T('mirrors_blocked')),
                         gen=gen)
                return
            except Exception as e:
                print(f'[crawl] page {page} error: {e}')
                break

            if not videos:
                if page == 1:
                    print(f'[crawl] No videos found on first page: {url}')
                break

            new_count = 0
            for v in videos:
                video_url = v.get('url', '')
                if video_url and video_url not in seen and M3U8Sites.VaildateUrl(video_url):
                    seen.add(video_url)
                    new_count += 1
                    name = v.get('title', '')
                    candidate = dict(v)
                    candidate['_site'] = (
                        'JableTV' if is_jable
                        else 'SupJav' if is_supjav
                        else 'Hanime1' if is_hanime1
                        else 'MissAV')
                    candidate['_source_listing_url'] = url
                    self._dlmgr.add_item(
                        video_url, name=name, state='等待中', dest=dest,
                        source_subtitle_evidence=(
                            trusted_chinese_subtitle_evidence(candidate)))
                    self._dlmgr.enqueue(video_url, dest)

            if new_count == 0:
                break  # No new videos on this page, stop

            self._ui(lambda n=len(seen): self._status_lbl.configure(
                text=T('crawling_url') + f' ({n})'), gen=gen)

        n = len(seen)
        self._ui(lambda: self._status_lbl.configure(
            text=T('crawl_added', n=n)), gen=gen)

    def _download_all(self):
        # If the URL field has a listing URL, crawl it first
        url = self._dl_url_var.get().strip()
        if url:
            if M3U8Sites.VaildateUrl(url):
                dest = self._dest_var.get() or 'download'
                self._dlmgr.add_item(url, state='等待中', dest=dest)
                self._dlmgr.enqueue(url, dest)
                self._dl_url_var.set('')
            elif self._is_listing_url(url):
                self._dl_url_var.set('')
                self._status_lbl.configure(text=T('crawling_url'))
                dest = self._dest_var.get() or 'download'
                threading.Thread(target=self._crawl_listing, args=(url, dest),
                                 daemon=True).start()
                return
        dest = self._dest_var.get() or 'download'
        count = 0
        for item in self._dlmgr.get_items():
            # Skip items that are already active or completed; queued ('等待中')
            # items still need enqueue() to (re)start them.
            if item.state in (
                    '已下載', '未偵測到日語語音', '下載中', '準備中'):
                continue
            self._dlmgr.enqueue(item.url, item.dest or dest)
            count += 1
        if count:
            print(f'已加入 {count} 個下載任務')

    def _retry_download(self, url: str):
        item = next((i for i in self._dlmgr.get_items() if i.url == url), None)
        if item is None:
            return
        dest = item.dest or self._dest_var.get() or 'download'
        restart = getattr(self._dlmgr, 'restart', None)
        if callable(restart) and restart(url, dest):
            return
        item.progress = 0
        item.speed = ''
        item.error = ''
        self._dlmgr.enqueue(url, dest)

    def _cancel_all(self):
        self._dlmgr.cancel_all()

    def _clear_queue(self):
        self._dlmgr.clear_all()
        self._dl_gen += 1
        self._last_download_save_sig = None
        try:
            self._dlmgr.save_csv(CSV_PATH)
        except Exception as e:
            print(f'[clear queue save failed] {e}', flush=True)
        self._refresh_downloads(schedule=False)

    def _on_cf_host_change(self, host):
        ov = config.get_cf_override(host) or {}
        self._cf_cookie_var.set(ov.get('cookie', ''))
        self._cf_ua_var.set(ov.get('ua', ''))

    def _refresh_proxy_status(self, saved=False):
        mode = config.get_proxy_mode()
        if mode == 'manual' and config.get_proxy_url():
            text, color = T('proxy_enabled'), SUCCESS
        elif mode == 'system':
            _display_url, status = config.refresh_system_proxy()
            if status == 'detected':
                text, color = T('proxy_windows_enabled'), SUCCESS
            elif status == 'pac':
                text, color = T('proxy_windows_pac'), WARNING
            elif status == 'invalid':
                text, color = T('proxy_windows_invalid'), ERROR_C
            else:
                text, color = T('proxy_windows_missing'), WARNING
        else:
            text, color = T('proxy_disabled'), TEXT_DIM
        if saved:
            text = f"{T('proxy_saved')} · {text}"
        self._proxy_status_lbl.configure(text=text, text_color=color)

    def _on_proxy_save(self):
        try:
            value = config.set_proxy_url(self._proxy_var.get())
        except (OSError, ValueError):
            self._proxy_status_lbl.configure(
                text=T('proxy_invalid'), text_color=ERROR_C)
            return
        self._proxy_var.set(value)
        self._refresh_proxy_status(saved=True)

    def _on_proxy_windows(self):
        try:
            config.set_proxy_mode('system')
        except OSError:
            self._proxy_status_lbl.configure(
                text=T('proxy_invalid'), text_color=ERROR_C)
            return
        self._refresh_proxy_status(saved=True)

    def _on_proxy_clear(self):
        try:
            config.set_proxy_url('')
        except OSError:
            self._proxy_status_lbl.configure(
                text=T('proxy_invalid'), text_color=ERROR_C)
            return
        self._proxy_var.set('')
        self._refresh_proxy_status()

    def _on_cf_save(self):
        host = self._cf_host_var.get()
        config.set_cf_override(host, self._cf_cookie_var.get(), self._cf_ua_var.get())
        self._refresh_cf_status()
        current = self._cf_status_lbl.cget('text')
        self._cf_status_lbl.configure(text=f"{T('cf_saved')} | {current}")

    def _on_cf_clear(self):
        host = self._cf_host_var.get()
        config.clear_cf_override(host)
        self._cf_cookie_var.set('')
        self._cf_ua_var.set('')
        self._refresh_cf_status()

    def _refresh_cf_status(self):
        hosts = config.cf_override_hosts()
        if hosts:
            self._cf_status_lbl.configure(text=T('cf_status', hosts=', '.join(hosts)))
        else:
            self._cf_status_lbl.configure(text=T('cf_status_none'))

    def _on_speed_change(self, val):
        from uav_downloader.sites.base import speed_limiter
        val = str(val)
        if val == T('unlimited') or not val[:1].isdigit():
            self._speed_mbps = 0
            speed_limiter.set_limit(0)
            return
        try:
            mbps = float(val.split()[0])
        except (ValueError, IndexError):
            return
        self._speed_mbps = mbps
        speed_limiter.set_limit(mbps)

    def _on_res_change(self, val):
        from uav_downloader.sites.base import set_resolution_pref
        pref = self._resolution_pref_from_label(val)
        set_resolution_pref(pref)
        config.set_resolution_pref(pref)

    def _on_filename_mode_change(self, val):
        mode = config.set_filename_mode(
            self._filename_mode_from_label(val))
        self._filename_mode_var.set(self._filename_mode_label(mode))

    def _on_subtitle_change(self, val):
        config.set_subtitle_pref(self._subtitle_pref_from_label(val))

    def _on_recognition_quality_change(self, val):
        quality = self._recognition_quality_from_label(val)
        quality = config.set_recognition_quality(quality)
        self._recognition_quality_var.set(
            self._recognition_quality_label(quality))

    def _on_conc_change(self, _event=None):
        try:
            requested = int(self._conc_var.get().strip())
        except (AttributeError, TypeError, ValueError):
            self._conc_var.set(str(self._dlmgr.max_concurrent))
            return
        value = config.set_download_concurrency(requested)
        self._dlmgr.max_concurrent = value
        self._conc_var.set(str(value))

    def _commit_workers_preference(self):
        current = config.get_max_workers_per_video()
        var = self.__dict__.get('_workers_var')
        if var is None:
            return current
        try:
            requested = int(var.get().strip())
        except (AttributeError, TypeError, ValueError):
            value = current
        else:
            value = config.set_max_workers_per_video(requested)
        var.set(str(value))
        return value

    def _on_workers_change(self, _event=None):
        self._commit_workers_preference()

    def _pick_dest(self):
        d = filedialog.askdirectory()
        if d:
            self._dest_var.set(d)

    def _open_dest_folder(self):
        import subprocess, platform
        dest = self._dest_var.get() or 'download'
        folder = os.path.abspath(dest)
        if not os.path.isdir(folder):
            messagebox.showerror(T('open_folder_failed_title'), folder)
            return
        system = platform.system()
        try:
            if system == 'Windows':
                os.startfile(folder)
            elif system == 'Darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        except OSError as e:
            messagebox.showerror(T('open_folder_failed_title'), str(e))

    def _open_queue_folder(self):
        import subprocess, platform
        folder = os.path.dirname(config.queue_csv_path())
        system = platform.system()
        try:
            os.makedirs(folder, exist_ok=True)
            if system == 'Windows':
                os.startfile(folder)
            elif system == 'Darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])
        except OSError as e:
            messagebox.showerror(T('open_folder_failed_title'), str(e))

    def _clear_saved_queue(self):
        if not messagebox.askyesno(T('clear_saved_queue'),
                                   T('clear_saved_queue_confirm')):
            return
        self._dlmgr.clear_all()
        self._dl_gen += 1
        self._last_download_save_sig = None
        try:
            self._dlmgr.save_csv(CSV_PATH)
            if getattr(self, '_status_lbl', None) is not None:
                self._status_lbl.configure(text=T('clear_saved_queue_done'))
            messagebox.showinfo(T('clear_saved_queue'), T('clear_saved_queue_done'))
        except Exception as e:
            messagebox.showerror(T('clear_saved_queue_failed'), str(e))
        finally:
            self._refresh_downloads(schedule=False)

    # ── Download list refresh (incremental — no destroy/rebuild storm) ──
    _STATE_COLORS = {
        '下載中': ACCENT, '準備中': WARNING, '等待中': WARNING,
        '字幕準備中': ACCENT, '字幕辨識中': ACCENT,
        '字幕翻譯中': ACCENT,
        '已下載': SUCCESS, '未偵測到日語語音': TEXT_SEC,
        '未完成': WARNING, '已取消': TEXT_DIM,
        '網址錯誤': ERROR_C, '封鎖/解析失敗': ERROR_C,
    }
    _STATE_BACKGROUNDS = {
        '下載中': ACCENT_DIM, '準備中': WARNING_DIM, '等待中': WARNING_DIM,
        '字幕準備中': ACCENT_DIM, '字幕辨識中': ACCENT_DIM,
        '字幕翻譯中': ACCENT_DIM,
        '已下載': SUCCESS_DIM, '未偵測到日語語音': BG_BADGE,
        '未完成': WARNING_DIM, '已取消': BG_BADGE,
        '網址錯誤': ERROR_DIM, '封鎖/解析失敗': ERROR_DIM,
    }

    def _sync_dl_footer(self, hidden: int):
        if hidden > 0:
            text = T('dl_list_more_not_shown', n=hidden)
            if self._dl_footer_lbl is None:
                self._dl_footer_lbl = ctk.CTkLabel(
                    self._dl_scroll, text=text, text_color=TEXT_DIM,
                    font=(ui_font(), 11))
            else:
                self._dl_footer_lbl.configure(text=text)
            try:
                self._dl_footer_lbl.pack_forget()
            except Exception:
                pass
            self._dl_footer_lbl.pack(fill='x', padx=12, pady=(4, 16))
        elif self._dl_footer_lbl is not None:
            try:
                self._dl_footer_lbl.destroy()
            except Exception:
                pass
            self._dl_footer_lbl = None

    def _build_visible_rows(self, visible: list[DownloadItem]) -> bool:
        built = 0
        more_to_build = False
        for item in visible:
            widgets = self._dl_rows.get(item.url)
            if widgets is not None:
                self._update_dl_row(widgets, item)
                continue
            if built >= ROW_BUILD_BUDGET:
                more_to_build = True
                continue
            try:
                self._dl_rows[item.url] = self._build_dl_row(item)
                built += 1
            except Exception as e:
                print(f'[download row build failed] {item.url}: {e}', flush=True)
        return more_to_build

    def _arm_dl_drain(self):
        if (self._dl_drain_id is not None or self._is_closing
                or self._rebuilding):
            return
        gen = self._dl_gen
        try:
            self._dl_drain_id = self.after(
                20, lambda g=gen: self._drain_dl_rows(g))
        except tk.TclError:
            self._dl_drain_id = None

    def _drain_dl_rows(self, gen: int):
        self._dl_drain_id = None
        if (self._is_closing or self._rebuilding or gen != self._dl_gen
                or getattr(self, '_dl_scroll', None) is None):
            return
        try:
            items = self._dlmgr.get_items()
            visible = _visible_window(items, MAX_VISIBLE_ROWS)
            visible_set = {i.url for i in visible}

            for url in list(self._dl_rows.keys()):
                if url not in visible_set:
                    widgets = self._dl_rows.pop(url)
                    try:
                        widgets['row'].destroy()
                    except Exception:
                        pass

            if not items:
                self._sync_dl_footer(0)
                return

            more_to_build = self._build_visible_rows(visible)
            self._sync_dl_footer(len(items) - len(visible))
            if more_to_build:
                self._arm_dl_drain()
        except tk.TclError:
            pass

    def _refresh_downloads(self, schedule: bool = True):
        if self._is_closing:
            return
        if (self._rebuilding or getattr(self, '_status_lbl', None) is None
                or getattr(self, '_dl_scroll', None) is None):
            if schedule:
                try:
                    self.after(1000, self._refresh_downloads)
                except tk.TclError:
                    pass
            return
        try:
            items = self._dlmgr.get_items()
            visible = _visible_window(items, MAX_VISIBLE_ROWS)
            visible_set = {i.url for i in visible}

            # Remove rows outside the bounded visible window.
            for url in list(self._dl_rows.keys()):
                if url not in visible_set:
                    widgets = self._dl_rows.pop(url)
                    try:
                        widgets['row'].destroy()
                    except Exception:
                        pass

            # Toggle empty placeholder
            if not items:
                if self._dl_empty_lbl is None:
                    self._dl_empty_lbl = ctk.CTkLabel(
                        self._dl_scroll, text=T('dl_list_empty'),
                        text_color=TEXT_DIM,
                        font=(ui_font(), 13))
                    self._dl_empty_lbl.pack(pady=40)
            else:
                if self._dl_empty_lbl is not None:
                    try:
                        self._dl_empty_lbl.destroy()
                    except Exception:
                        pass
                    self._dl_empty_lbl = None

                more_to_build = self._build_visible_rows(visible)
                self._sync_dl_footer(len(items) - len(visible))
                if more_to_build:
                    self._arm_dl_drain()
            if not items:
                self._sync_dl_footer(0)

            # Update status bar
            a = self._dlmgr.active_count
            p = self._dlmgr.pending_count
            parts = []
            if a:
                parts.append(f'{state_label("下載中")} {a}/{self._dlmgr.max_concurrent}')
            if p:
                parts.append(f'{state_label("等待中")} {p}')
            subtitle_active = self._dlmgr.subtitle_active_count
            subtitle_pending = self._dlmgr.subtitle_pending_count
            if subtitle_active or subtitle_pending:
                parts.append(T(
                    'subtitle_queue_status',
                    active=subtitle_active, pending=subtitle_pending))
            done = sum(
                1 for i in items
                if i.state in {'已下載', '未偵測到日語語音'})
            if done:
                parts.append(f'{state_label("已下載")} {done}')
            self._status_lbl.configure(text='  |  '.join(parts) if parts else T('status_ready'))
            self._autosave_downloads(items)
        except Exception as e:
            print(f'[download refresh failed] {e}', flush=True)
        finally:
            if schedule and not self._is_closing:
                try:
                    self.after(1000, self._refresh_downloads)
                except tk.TclError:
                    pass

    def _autosave_downloads(self, items: list[DownloadItem]):
        self._download_autosave_ticks += 1
        if self._download_autosave_ticks < 10:
            return
        self._download_autosave_ticks = 0
        sig = tuple((
            i.url, i.name, i.state, i.progress, i.dest,
            i.source_subtitle_evidence,
        ) for i in items)
        if sig == self._last_download_save_sig:
            return
        try:
            self._dlmgr.save_csv(CSV_PATH)
            self._last_download_save_sig = sig
        except Exception:
            pass

    def _build_dl_row(self, item: DownloadItem) -> dict:
        """Build one download row once; return widget handles for in-place updates."""
        color = self._STATE_COLORS.get(item.state, TEXT_SEC)
        row = None
        try:
            row = ctk.CTkFrame(
                self._dl_scroll, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                border_width=1, border_color=BORDER_CARD, height=76)
            row.pack(fill='x', padx=16, pady=7)
            row.pack_propagate(False)

            state_holder = ctk.CTkFrame(
                row,
                width=210 if item.state == '未偵測到日語語音' else 92,
                height=32, corner_radius=6,
                fg_color=self._STATE_BACKGROUNDS.get(item.state, BG_BADGE))
            state_holder.pack(side='left', padx=(14, 10))
            state_holder.pack_propagate(False)
            state_lbl = ctk.CTkLabel(
                state_holder, text=state_label(item.state) if item.state else '—',
                text_color=color, font=(ui_font(), 10, 'bold'))
            state_lbl.pack(fill='both', expand=True, padx=6)

            remove_btn = ctk.CTkButton(
                row, text='✕', width=32, height=32,
                corner_radius=CONTROL_RADIUS,
                fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                hover_color=BG_CARD_HOVER,
                text_color=TEXT_DIM, font=('Consolas', 12),
                command=lambda u=item.url: self._dlmgr.remove_item(u))
            remove_btn.pack(side='right', padx=(6, 14))

            retry_btn = ctk.CTkButton(
                row, text='', width=82, height=32,
                corner_radius=CONTROL_RADIUS,
                fg_color='transparent', border_width=1, border_color=BORDER_HOVER,
                hover_color=BG_CARD_HOVER,
                text_color=ACCENT, font=(ui_font(), 10, 'bold'),
                command=lambda u=item.url: self._retry_download(u))

            metrics = ctk.CTkFrame(row, fg_color='transparent')
            metrics.pack(side='right', padx=(6, 2))

            # Progress widgets (created once, packed/unpacked dynamically)
            pb = ctk.CTkProgressBar(metrics, width=150, height=8,
                                    corner_radius=5,
                                    fg_color=BG_INPUT,
                                    progress_color=ACCENT)
            pb.set(max(0.0, min(1.0, item.progress / 100)))
            pct_lbl = ctk.CTkLabel(
                metrics, text='', text_color=TEXT_SEC,
                font=('Consolas', 10, 'bold'), width=46)
            spd_lbl = ctk.CTkLabel(
                metrics, text='', text_color=TEXT_SEC,
                font=('Consolas', 9), width=76)

            text_stack = ctk.CTkFrame(row, fg_color='transparent')
            text_stack.pack(side='left', fill='both', expand=True, padx=(0, 10), pady=10)
            name_lbl = ctk.CTkLabel(
                text_stack, text=item.name or item.url, text_color=TEXT_PRI,
                font=(ui_font(), 11, 'bold'), anchor='w')
            name_lbl.pack(fill='x', anchor='w')
            detail_lbl = ctk.CTkLabel(
                text_stack, text=item.url, text_color=TEXT_DIM,
                font=('Consolas', 9), anchor='w')
            detail_lbl.pack(fill='x', anchor='w', pady=(3, 0))

            widgets = {
                'row': row, 'state_holder': state_holder,
                'state_lbl': state_lbl, 'name_lbl': name_lbl,
                'detail_lbl': detail_lbl, 'metrics': metrics,
                'pb': pb, 'pct_lbl': pct_lbl, 'spd_lbl': spd_lbl,
                'retry_btn': retry_btn, '_before_remove': remove_btn,
                'pb_visible': False, 'pct_visible': False, 'spd_visible': False,
                'retry_visible': False,
                'last_retry_action': None,
                'last_state': None, 'last_name': None, 'last_detail': None,
                'last_progress': -1, 'last_speed': None,
            }
            self._update_dl_row(widgets, item)
            return widgets
        except Exception:
            if row is not None:
                try:
                    row.destroy()
                except Exception:
                    pass
            raise

    def _update_dl_row(self, w: dict, item: DownloadItem):
        """Update an existing row's fields in place without rebuilding widgets."""
        # State text + color
        if w['last_state'] != item.state:
            color = self._STATE_COLORS.get(item.state, TEXT_SEC)
            try:
                w['state_holder'].configure(
                    fg_color=self._STATE_BACKGROUNDS.get(item.state, BG_BADGE),
                    width=(
                        210 if item.state == '未偵測到日語語音'
                        else 92))
                w['state_lbl'].configure(
                    text=state_label(item.state) if item.state else '—',
                    text_color=color)
            except Exception:
                return
            w['last_state'] = item.state

        # Name and supporting detail (error or source URL) are separate levels.
        display_name = item.name or item.url
        detail = item.url
        detail_color = TEXT_DIM
        if item.error and item.state in ('未完成', '封鎖/解析失敗', '已下載'):
            err_text = T('blocked_vpn_hint') if item.error == ERR_BLOCKED else item.error
            err = err_text.replace('\n', ' ').strip()
            if len(err) > 110:
                err = err[:107] + '...'
            detail = err
            detail_color = WARNING if item.state == '已下載' else ERROR_C
        if w['last_name'] != display_name:
            try:
                w['name_lbl'].configure(text=display_name)
            except Exception:
                return
            w['last_name'] = display_name
        if w['last_detail'] != detail:
            try:
                w['detail_lbl'].configure(text=detail, text_color=detail_color)
            except Exception:
                return
            w['last_detail'] = detail

        retry_action = _download_row_action(item)
        if w['last_retry_action'] != retry_action:
            if retry_action:
                label = T(
                    'requeue_short' if retry_action == 'requeue'
                    else 'retry_short')
                try:
                    w['retry_btn'].configure(text=f'↻ {label}')
                except Exception:
                    return
            w['last_retry_action'] = retry_action
        if retry_action and not w['retry_visible']:
            w['retry_btn'].pack(side='right', padx=(2, 0), before=w['_before_remove'])
            w['retry_visible'] = True
        elif not retry_action and w['retry_visible']:
            try:
                w['retry_btn'].pack_forget()
            except Exception:
                pass
            w['retry_visible'] = False

        # Progress bar: show only while downloading
        is_downloading = (item.state in {
            '下載中', '字幕準備中', '字幕辨識中', '字幕翻譯中'
        } and item.progress > 0)
        if is_downloading:
            if not w['pb_visible']:
                w['pb'].pack(side='left', padx=(0, 4))
                w['pb_visible'] = True
            if w['last_progress'] != item.progress:
                w['pb'].set(max(0.0, min(1.0, item.progress / 100)))
                w['last_progress'] = item.progress
            pct_text = f'{item.progress}%'
            if not w['pct_visible']:
                w['pct_lbl'].pack(side='left')
                w['pct_visible'] = True
            if w['pct_lbl'].cget('text') != pct_text:
                w['pct_lbl'].configure(text=pct_text)
        else:
            if w['pb_visible']:
                try: w['pb'].pack_forget()
                except Exception: pass
                w['pb_visible'] = False
            if w['pct_visible']:
                try: w['pct_lbl'].pack_forget()
                except Exception: pass
                w['pct_visible'] = False

        # Speed
        if is_downloading and item.speed:
            if not w['spd_visible']:
                w['spd_lbl'].pack(side='left', padx=4)
                w['spd_visible'] = True
            if w['last_speed'] != item.speed:
                w['spd_lbl'].configure(text=item.speed)
                w['last_speed'] = item.speed
        else:
            if w['spd_visible']:
                try: w['spd_lbl'].pack_forget()
                except Exception: pass
                w['spd_visible'] = False
                w['last_speed'] = None

    # ── Clipboard monitor (main-thread safe) ─────────────────────────
    def _clipboard_poll(self):
        if self._is_closing:
            return
        if self._rebuilding:
            try:
                self.after(800, self._clipboard_poll)
            except tk.TclError:
                pass
            return
        try:
            clp = self.clipboard_get()
            if clp != self._clp_text:
                self._clp_text = clp
                for m in re.finditer(r'https?://\S+', clp):
                    url = m.group(0).rstrip('.,;)\'"')
                    if len(url) > 2048:      # no real video URL is this long; bounds validation cost
                        continue
                    if M3U8Sites.VaildateUrl(url):
                        existing = {i.url for i in self._dlmgr.get_items()}
                        if url not in existing:
                            self._dlmgr.add_item(url)
                            print(f'[剪貼簿] {url}')
        except (tk.TclError, Exception):
            pass
        finally:
            if not self._is_closing:
                try:
                    self.after(800, self._clipboard_poll)
                except tk.TclError:
                    pass

    # ── Close ────────────────────────────────────────────────────────
    def _on_close(self):
        self._is_closing = True
        if self._dl_drain_id:
            try:
                self.after_cancel(self._dl_drain_id)
            except Exception:
                pass
            self._dl_drain_id = None
        try:
            self._thumb_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            with self._dlmgr._lock:
                for item in self._dlmgr._items.values():
                    if item.state in ('準備中', '下載中', '等待中'):
                        item.state = '未完成'
                        item.speed = ''
                self._dlmgr.save_csv(CSV_PATH)
        except Exception:
            pass
        self._dlmgr.cancel_all(cleanup=False)
        self.destroy()


def gui_modern_main(url: str = '', dest: str = 'download', lang: str = 'en'):
    _crumb("gui_modern_main: constructing ModernApp")
    app = ModernApp(url=url, dest=dest, lang=lang)
    _crumb("gui_modern_main: app constructed, entering mainloop")
    app.mainloop()
    _crumb("gui_modern_main: mainloop returned (normal exit)")
