"""Ordered background commands for the shared Watcher worker."""
from concurrent.futures import Future
import queue
import threading
from copy import deepcopy

from uav_downloader.apps.watch_categories import SITES, find_target
from uav_downloader.core import watcher
from uav_downloader.core.watcher_store import WatcherStore
from uav_downloader.core.storage import StorageWriteError
from uav_downloader.core.service_errors import StopTimeoutError


class WatcherService:
    def __init__(self, directory, worker_factory=watcher.SmallToolWorker, apply_resolution=False):
        self._schedule_lock = threading.Lock()
        self._schedule_config = {}
        self.store = WatcherStore(directory, self._observe_config)
        self.worker = None
        self.events = queue.SimpleQueue()
        self._worker_factory = worker_factory
        self.apply_resolution = apply_resolution
        self._commands = queue.Queue()
        self._accepting = True
        self._close_future = None
        self._failed_save = None
        self.stop_timeout = 10.0
        self.ready = self._put('load')
        self._thread = threading.Thread(target=self._run, name='watcher-commands', daemon=True)
        self._thread.start()

    def _observe_config(self, cfg):
        with self._schedule_lock:
            self._schedule_config = {key: deepcopy(cfg[key]) for key in ('scan_schedule', 'last_check_iso', 'last_daily_slot') if key in cfg}

    def schedule_snapshot(self):
        with self._schedule_lock:
            return deepcopy(self._schedule_config)

    def _put(self, action, *args):
        future = Future()
        self._commands.put((action, args, future))
        return future

    def submit(self, action, *args):
        if not self._accepting:
            raise RuntimeError('Watcher is closing')
        if action not in {'save', 'monitor', 'check', 'stop'}:
            raise ValueError('Unknown Watcher command')
        return self._put(action, *args)

    def close(self):
        self._accepting = False
        if self._close_future is None or (self._close_future.done() and self._close_future.exception()):
            self._close_future = self._put('close', self._close_future is not None)
        return self._close_future

    def abandon(self):
        self._accepting = False
        if self.worker is not None:
            self.worker.stop()  # Notification only; cancellation stays on the command thread.
        self._put('abandon')

    def _status(self, key, color=None, generation=None):
        self.events.put(('status', key, generation))

    def _save(self, patch):
        try:
            result = self._save_impl(patch)
        except Exception as error:
            self._failed_save = deepcopy(patch), error
            raise
        else:
            self._failed_save = None
            return result

    def _save_impl(self, patch):
        patch = dict(patch)
        allowed = {'output_folder', 'baseline_date', 'site_settings', 'selected_targets',
                   'version_preference', 'subtitle_mode', 'scan_schedule', 'resolution'}
        if patch.keys() - allowed:
            raise ValueError('Unknown Watcher setting')
        if 'baseline_date' in patch and not watcher._valid_baseline_date(patch['baseline_date']):
            raise ValueError('Baseline date must use YYYY-MM-DD')
        if 'output_folder' in patch and not str(patch['output_folder']).strip():
            raise ValueError('An output folder is required')
        if 'version_preference' in patch and patch['version_preference'] not in {*watcher.VALID_VERSION_PREFERENCES, 'all'}:
            raise ValueError('Invalid version preference')
        if ('version_preference' in patch and
                patch['version_preference'] != self.store.load_config()['version_preference']):
            patch['first_run_done'] = False
        if 'subtitle_mode' in patch and patch['subtitle_mode'] not in watcher._VALID_SUBTITLE_MODES:
            raise ValueError('Invalid subtitle mode')
        if 'resolution' in patch and patch['resolution'] not in watcher._VALID_RESOLUTION_PREFS:
            raise ValueError('Invalid resolution')
        if 'scan_schedule' in patch:
            schedule = dict(patch['scan_schedule'])
            if watcher._normalize_scan_schedule(schedule) != schedule:
                raise ValueError('Invalid scan schedule')
        if 'selected_targets' in patch:
            targets, keys = [], set()
            for raw in patch['selected_targets']:
                site, identifier = raw.get('site'), raw.get('id')
                target = find_target(site, identifier) if site in SITES else None
                if not target:
                    raise ValueError('Unknown Watcher category')
                if (site, identifier) not in keys:
                    targets.append(dict(site=site, id=identifier, category=target['name']))
                    keys.add((site, identifier))
            patch['selected_targets'] = targets
        if 'site_settings' in patch:
            for site, values in patch['site_settings'].items():
                if site not in SITES or set(values) - {'baseline_date', 'output_folder'}:
                    raise ValueError('Invalid site override')
                date = values.get('baseline_date')
                if date and not watcher._valid_baseline_date(date):
                    raise ValueError('Site baseline date must use YYYY-MM-DD')
        cfg = self.store.update_config(patch)
        if self.apply_resolution and 'resolution' in patch:
            from uav_downloader.sites.base import set_resolution_pref
            set_resolution_pref(patch['resolution'])
        self.worker.notify_schedule_changed()
        return cfg

    def _execute(self, action, args):
        if action == 'load':
            self.worker = self._worker_factory(
                lambda line: self.events.put(('log', str(line), None)),
                self._status, storage=self.store)
            cfg = self.store.load_config()
            if self.apply_resolution:
                from uav_downloader.sites.base import set_resolution_pref
                set_resolution_pref(watcher._normalize_resolution_pref(cfg))
            return cfg
        if action == 'save':
            return self._save(args[0])
        if action in {'monitor', 'check'}:
            if action == 'check' and self.worker.is_monitoring():
                result = self.worker.request_scan_now()
                return dict(config=self.store.load_config(), result=result)
            cfg = self._save(args[0]) if args else self.store.load_config()
            if not cfg['selected_targets']:
                raise ValueError('Select at least one Watcher category')
            for site in {target['site'] for target in cfg['selected_targets']}:
                from pathlib import Path
                destination = Path(watcher._resolve_site_settings(cfg, site)['output_folder'])
                try:
                    destination.mkdir(parents=True, exist_ok=True)
                except OSError as error:
                    failure = StorageWriteError(destination, error)
                    self.worker._halt_for_storage_error(failure)
                    raise failure from error
            result = self.worker.start_monitoring() if action == 'monitor' else self.worker.start_once()
            return dict(config=cfg, result=result)
        if action in {'stop', 'close', 'abandon'}:
            if self.worker is not None:
                if action == 'close':
                    from uav_downloader.core.stop import StopReason
                    self.worker.stop(StopReason.SHUTDOWN, cleanup=False)
                else:
                    self.worker.stop()
                self.worker.cancel_active_download()
                if action == 'close':
                    if not self.worker.wait_until_stopped(timeout=self.stop_timeout):
                        raise StopTimeoutError('Watcher is still stopping; wait for I/O or subprocesses to exit, then retry closing')
            if action == 'close' and self._failed_save is not None:
                patch, error = self._failed_save
                if not args or not args[0]:
                    raise error
                self._save(patch)
            return True

    def _run(self):
        while True:
            action, args, future = self._commands.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(self._execute(action, args))
            except Exception as error:
                future.set_exception(error)
            else:
                if action in {'close', 'abandon'}:
                    return
