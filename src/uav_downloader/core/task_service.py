"""Serial, non-GUI command boundary for desktop task queues."""
import os
import queue
import threading
from uav_downloader.core.service_errors import StopTimeoutError
from uav_downloader.core.stop import StopReason
import time
import shutil
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import asdict
from uav_downloader.apps.downloads import (
    ACTIVE_STATES, QUEUED_STATES, QueueAddSummary, export_items,
    next_schedule_epoch, storage_preflight,
)

from uav_downloader.core.queue_store import QueueSnapshotWriter
from uav_downloader.core.task_queue import DownloadManager, _download_row_action
from uav_downloader.core.video_identity import trusted_chinese_subtitle_evidence
from uav_downloader.core import config, preferences
from uav_downloader.sites.base import set_resolution_pref
from uav_downloader.core.browse_service import listing_site, fetch_listing_page
from uav_downloader.core.listing_pool import ListingPool
from uav_downloader.core.url_import import FileImporter, input_urls
from uav_downloader.core.storage import StorageWriteError


class TaskService:
    """Disk access and potentially slow cancel/cleanup never run on the GUI thread.

    Commands are ordered, including the final close barrier. Workers communicate
    through Futures/error messages only; no GUI objects are held by this class.
    """
    def __init__(self, path, manager=None, listing_fetcher=fetch_listing_page,
                 initialize_resolution=True, migrate_legacy_queue=False):
        self.manager = manager or DownloadManager()
        self._owns_manager = manager is None
        self._initialize_resolution = initialize_resolution
        self._migrate_legacy_queue = migrate_legacy_queue
        self.path = path
        self.errors = queue.SimpleQueue()
        self._commands = queue.Queue()
        self._writer = QueueSnapshotWriter(path)
        self._accepting = True
        self._abandoned = threading.Event()
        self._last_snapshot = None
        self._pending_save = None
        self._final_snapshot = None
        self._close_future = None
        self._failed_writes = {}
        self.stop_timeout = 10.0
        self.listings = ListingPool(lambda request, batch: self._put('listing_batch', request, batch),
                                    lambda request, error: self._put('listing_error', request, error), listing_fetcher,
                                    wait_dispatch=None)
        self.files = FileImporter(lambda request, batch: self._put('import_batch', request, batch),
                                  lambda request, error: self._put('listing_error', request, error),
                                  wait_dispatch=None)
        self._next_save = time.monotonic() + 5
        self.ready = self._put('load')
        self._thread = threading.Thread(target=self._run, name='task-commands', daemon=True)
        self._thread.start()

    def _put(self, action, *args):
        future = Future()
        self._commands.put((action, args, future))
        return future

    def submit(self, action, *args):
        if not self._accepting:
            raise RuntimeError('Task service is closing')
        if action not in {'enqueue', 'enqueue_video', 'add_video', 'enqueue_urls', 'queue_urls', 'import_files', 'paste_urls',
                          'local_subtitles', 'download_all', 'retry', 'remove', 'folder_path', 'cancel_all', 'clear_all', 'save',
                          'pause', 'resume', 'pause_all', 'add_batch', 'export',
                          'get_preferences', 'set_preferences', 'get_system_proxy',
                          'get_translation', 'set_translation', 'get_cf', 'set_cf'}:
            raise ValueError(f'Unknown task command: {action}')
        return self._put(action, *args)

    def close(self):
        self._accepting = False
        self.listings.stop_all(close=True)
        self.files.stop_all(close=True)
        if (self._close_future is None or
                (self._close_future.done() and self._close_future.exception() is not None)):
            self._close_future = self._put('close', self._close_future is not None)
        return self._close_future

    @staticmethod
    def _write_key(action, args):
        if action not in {'set_preferences', 'set_translation', 'set_cf'}:
            return None
        values = args[0] if args and isinstance(args[0], dict) else {}
        resource = str(values.get('provider' if action == 'set_translation' else 'host', ''))
        return action, resource.strip().casefold().rstrip('.') if action == 'set_cf' else resource

    def abandon(self):
        """Explicit user choice to exit without waiting for blocked work/save."""
        self._accepting = False
        self._abandoned.set()
        self.listings.stop_all(close=True)
        self.files.stop_all(close=True)
        self._writer.close(discard=True)
        # Complete any unpublished batch Futures so stopped network workers
        # cannot remain waiting on an actor that has already been abandoned.
        while True:
            try:
                action, args, future = self._commands.get_nowait()
            except queue.Empty:
                break
            if not future.done():
                future.set_exception(RuntimeError('Task service was abandoned'))

    def _save(self, wait=False):
        snapshot = self.manager.snapshot_csv()
        if wait or snapshot != self._last_snapshot:
            future = self._writer.submit(snapshot)
            self._last_snapshot = snapshot
            self._pending_save = future
            if wait:
                try:
                    return future.result()
                except Exception:
                    self._last_snapshot = None
                    raise
                finally:
                    self._pending_save = None  # Command Future reports this result.

    def _check_save(self):
        if self._pending_save is not None and self._pending_save.done():
            future, self._pending_save = self._pending_save, None
            try:
                future.result()
            except Exception as exc:
                self._last_snapshot = None
                self.errors.put(str(exc))

    @staticmethod
    def _prepare_destination(destination):
        try:
            os.makedirs(destination, exist_ok=True)
        except OSError as error:
            raise StorageWriteError(destination, error) from error
        result = storage_preflight(destination, minimum_free_bytes=int(config.get_minimum_free_gb() * 1024**3))
        if not result.ok:
            raise StorageWriteError(destination, OSError(result.error))

    def _execute(self, action, args):
        if action == 'load':
            if self._owns_manager:
                self.manager.max_concurrent = config.get_download_concurrency()
                if self._initialize_resolution:
                    set_resolution_pref(config.get_resolution_pref())
            legacy = os.path.abspath('JableTV.csv')
            if (self._migrate_legacy_queue and not os.path.exists(self.path)
                    and os.path.isfile(legacy) and os.path.abspath(self.path) != legacy):
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
                shutil.copy2(legacy, self.path)
            self.manager.load_csv(self.path)
            if self._owns_manager:
                from uav_downloader.sites.base import speed_limiter
                speed_limiter.set_limit(config.get_speed_limit_mbps())
        elif action == 'get_preferences':
            return preferences.read_preferences()
        elif action == 'get_system_proxy':
            endpoint, status = config.refresh_system_proxy()
            return dict(endpoint=endpoint, status=status)
        elif action == 'folder_path':
            from uav_downloader.i18n.locales import T
            folder = os.path.abspath(args[0] or 'download')
            if not os.path.isdir(folder):
                from uav_downloader.i18n.qt_strings import translate as QT
                raise OSError(QT('qt_directory_missing', path=folder))
            return folder
        elif action == 'set_preferences':
            return preferences.save_preferences(args[0], self.manager)
        elif action == 'get_translation':
            return preferences.read_translation(*args)
        elif action == 'set_translation':
            return preferences.save_translation(args[0])
        elif action == 'get_cf':
            return preferences.read_cloudflare(*args)
        elif action == 'set_cf':
            return preferences.save_cloudflare(args[0])
        elif action == 'add_batch':
            videos, dest, start, options = args
            resolution = str(options.get('resolution') or config.get_resolution_pref())
            template = str(options.get('filename_template') or config.get_filename_template())
            if resolution not in config.VALID_RESOLUTION_PREFS:
                raise ValueError('Invalid batch options')
            template = config.normalize_filename_template(template)
            if start:
                self._prepare_destination(dest)
                config.set_download_directory(os.path.abspath(dest))
            due = next_schedule_epoch(config.get_download_schedule()) if start else 0.0
            added = unsupported = started = 0
            duplicate_urls = []
            from uav_downloader import sites
            for video in videos:
                url = video.get('url', '')
                if not sites.VaildateUrl(url):
                    unsupported += 1
                    continue
                if self.manager.contains(url):
                    duplicate_urls.append(url)
                else:
                    added += 1
                self.manager.add_item(url, name=video.get('title', ''), state='等待中', dest=dest,
                    source_subtitle_evidence=trusted_chinese_subtitle_evidence(video),
                    resolution=resolution, filename_template=template, thumbnail=video.get('thumbnail', ''),
                    not_before=due)
                if start:
                    started += self.manager.enqueue(url, dest, not_before=due) is not False
            summary = QueueAddSummary(len(videos), added, len(duplicate_urls), unsupported, started)
            return dict(asdict(summary), duplicate_urls=duplicate_urls)
        elif action == 'pause':
            return self.manager.pause(*args)
        elif action == 'resume':
            return self.manager.resume(*args)
        elif action == 'pause_all':
            return self.manager.pause_all()
        elif action == 'export':
            return export_items(self.manager.snapshot_items(), args[0])
        elif action in {'enqueue_urls', 'queue_urls'}:
            text, dest = args
            urls = input_urls(text)
            if action == 'queue_urls' and any(listing_site(url) for url in urls):
                from uav_downloader.i18n.qt_strings import translate as QT
                raise ValueError(QT('qt_link_listing_start'))
            for url in urls:
                self._execute('enqueue' if action == 'enqueue_urls' else 'add_video',
                              (url if action == 'enqueue_urls' else dict(url=url), dest))
            return len(urls)
        elif action == 'import_files':
            paths, dest = args
            return self.files.start(paths, dest, self.manager.cancellation_epoch)
        elif action == 'paste_urls':
            from uav_downloader.apps.downloads import extract_urls
            from uav_downloader import sites
            text, dest = args
            added = duplicates = unsupported = 0
            for url in extract_urls(text):
                if not sites.VaildateUrl(url):
                    unsupported += 1
                elif self.manager.contains(url):
                    duplicates += 1
                else:
                    self.manager.add_item(url, state='等待中', dest=dest)
                    added += 1
            return dict(added=added, duplicates=duplicates, unsupported=unsupported)
        elif action == 'local_subtitles':
            from uav_downloader.i18n.locales import T
            mode = config.get_subtitle_pref()
            if mode == 'none':
                raise ValueError(T('subtitle_local_need_mode'))
            return sum(self.manager.enqueue_local_subtitles(path, mode) for path in dict.fromkeys(args[0]))
        elif action in {'enqueue', 'enqueue_video', 'add_video'}:
            value, dest = args
            video = {'url': value} if action == 'enqueue' else value
            url = video.get('url', '')
            if action == 'enqueue':
                input_urls(url)
            if not url.startswith(('https://', 'http://')):
                raise ValueError('An HTTP(S) video URL is required')
            if video.get('_clipboard_import') and any(item.url == url for item in self.manager.get_items()):
                return False
            if any(item.url == url and item.state in ACTIVE_STATES
                   for item in self.manager.get_items()):
                return False
            if action != 'add_video':
                self._prepare_destination(dest)
                config.set_download_directory(os.path.abspath(dest))
            site = listing_site(url) if action == 'enqueue' else None
            due = next_schedule_epoch(config.get_download_schedule())
            if site is not None:
                return self.listings.start(site, url, dest, self.manager.cancellation_epoch,
                                           not_before=due, resolution=config.get_resolution_pref(),
                                           filename_template=config.get_filename_template())
            self.manager.add_item(url, name=video.get('title', ''), state='等待中', dest=dest,
                                  source_subtitle_evidence=trusted_chinese_subtitle_evidence(video),
                                  thumbnail=video.get('thumbnail', ''),
                                  resolution=config.get_resolution_pref(), filename_template=config.get_filename_template(),
                                  not_before=due if action != 'add_video' else 0)
            if action != 'add_video':
                self.manager.enqueue(url, dest)
        elif action == 'download_all':
            from uav_downloader import sites
            dest = args[0] or 'download'
            self._prepare_destination(dest)
            config.set_download_directory(os.path.abspath(dest))
            try:
                supplied = input_urls(args[1]) if len(args) > 1 else []
            except ValueError:
                supplied = []
            due = next_schedule_epoch(config.get_download_schedule())
            for url in supplied:
                result = self._execute('enqueue', (url, dest))
                if listing_site(url) and not sites.VaildateUrl(url):
                    return result
            for item in self.manager.get_items():
                if item.local or item.url in supplied:
                    continue
                with self.manager._lock:
                    if item.state in {'已下載', '未偵測到日語語音', '下載中', '準備中'}:
                        continue
                    destination = item.dest or dest
                self.manager.enqueue(item.url, destination, not_before=due)
        elif action == 'retry':
            url, = args
            item = next((item for item in self.manager.get_items() if item.url == url), None)
            if item is not None and _download_row_action(item):
                if item.local:
                    from uav_downloader.i18n.locales import T
                    mode = config.get_subtitle_pref()
                    if mode == 'none':
                        raise ValueError(T('subtitle_local_need_mode'))
                    if not self.manager.enqueue_local_subtitles(url, mode):
                        raise FileNotFoundError(T('subtitle_local_missing', path=url))
                    return True
                dest = item.dest or 'download'
                self._prepare_destination(dest)
                if not self.manager.restart(url, dest):
                    self.manager.enqueue(url, dest)
        elif action == 'remove':
            self.manager.remove_item(*args)
        elif action == 'cancel_all':
            self.listings.stop_all()
            self.files.stop_all()
            self.manager.cancel_all()
        elif action == 'clear_all':
            self.listings.stop_all()
            self.files.stop_all()
            self.manager.clear_all()
            return self._save(wait=True)
        elif action == 'save':
            return self._save(wait=True)
        elif action == 'listing_batch':
            request, batch = args
            if not self._accepting or request.stopped.is_set():
                return 0
            return sum(self.manager.enqueue_listing_video(video, request.destination, request.epoch,
                       not_before=request.not_before, resolution=request.resolution,
                       filename_template=request.filename_template)
                       for video in batch if not request.stopped.is_set())
        elif action == 'import_batch':
            request, batch = args
            added = 0
            with self.manager._lock:
                if not self._accepting or request.stopped.is_set() or request.epoch != self.manager.cancellation_epoch:
                    return 0
                for url in batch:
                    if url not in self.manager._items:
                        self.manager.add_item(url, state='等待中', dest=request.destination)
                        added += 1
            return added
        elif action == 'listing_error':
            request, error = args
            if self._accepting and not request.stopped.is_set() and request.epoch == self.manager.cancellation_epoch:
                self.errors.put(str(error))
        elif action == 'close':
            if self._final_snapshot is None:
                # Capture recoverable states before cancellation changes them.
                rows = self.manager.snapshot_csv()
                self._final_snapshot = (rows[0],) + tuple(
                    ('未完成', row[1], row[2], '', *row[4:])
                    if row[0] in ACTIVE_STATES or row[0] in QUEUED_STATES else row
                    for row in rows[1:])
            self.manager.cancel_all(cleanup=False, reason=StopReason.SHUTDOWN)
            if self._abandoned.is_set():
                raise RuntimeError('Close was abandoned')
            deadline = time.monotonic() + self.stop_timeout
            for owner in (self.manager, self.listings, self.files):
                if not owner.wait_stopped(timeout=max(0.0, deadline-time.monotonic())):
                    raise StopTimeoutError('Work is still stopping; wait for I/O or subprocesses to exit, then retry closing')
            if self._failed_writes:
                if not args or not args[0]:
                    raise next(iter(self._failed_writes.values()))[2]
                # An explicit close retry must actually retry failed writes.
                # Keep the actor alive after a failed close until this succeeds
                # or the user chooses to abandon shutdown.
                for key, (command, values, _) in tuple(self._failed_writes.items()):
                    self._execute(command, values)
                    self._failed_writes.pop(key)
            result = self._writer.submit(self._final_snapshot).result()
            self._writer.close()
            return result
        return True

    def _run(self):
        while not self._abandoned.is_set():
            try:
                action, args, future = self._commands.get(timeout=.2)
            except queue.Empty:
                self._check_save()
                if self._accepting and time.monotonic() >= self._next_save:
                    self._next_save = time.monotonic() + 5
                    try:
                        self._save()
                    except Exception as exc:
                        self.errors.put(str(exc))
                continue
            if not future.set_running_or_notify_cancel():
                continue
            write_key = self._write_key(action, args)
            try:
                result = self._execute(action, args)
            except Exception as exc:
                if isinstance(exc, StorageWriteError):
                    self.listings.stop_all()
                    self.files.stop_all()
                    self.manager.cancel_all(cleanup=False)
                if write_key is not None:
                    self._failed_writes[write_key] = action, deepcopy(args), exc
                future.set_exception(exc)
            else:
                if write_key is not None:
                    self._failed_writes.pop(write_key, None)
                future.set_result(result)
                if action == 'close':
                    return
