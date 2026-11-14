"""Shared Watcher scheduling, persistence and download worker without GUI imports."""
from uav_downloader.core.stop import StopContext, StopReason, DownloadStopped, current_stop_context, execution_scope
import calendar
from concurrent.futures import CancelledError
import json
import os
import re
import shutil
import sys
import threading
import time
from itertools import count
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from uav_downloader import sites as M3U8Sites
from uav_downloader.sites.jabletv import JableTVBrowser
from uav_downloader.sites.missav import MissAVBrowser
from uav_downloader.sites.supjav import SupJavBrowser
from uav_downloader.sites.base import DownloadIncompleteError, MirrorsBlockedError, fetch_with_mirrors
from uav_downloader.core import config
from uav_downloader.core.storage import StorageWriteError
from uav_downloader.core import paths as product_paths
from uav_downloader.i18n.locales import T, get_lang
from uav_downloader.apps.watch_categories import SITES, find_target, group_label, iter_targets, selection_key, target_label
from uav_downloader.core.video_identity import (
    DEFAULT_VERSION_PREFERENCE, VALID_VERSION_PREFERENCES, dedupe_video_candidates,
    normalize_source_subtitle_evidence, normalize_version_preference, site_from_url,
    trusted_chinese_subtitle_evidence, url_slug, video_code, video_versions,
)
from uav_downloader.subtitles.errors import translation_failure_message
from uav_downloader.ui.theme import TEXT_DIM, WARNING, SUCCESS, ACCENT, ERROR_C
try:
    import cloudscraper
    from bs4 import BeautifulSoup
except ImportError:
    cloudscraper = None
    BeautifulSoup = None

_VALID_SUBTITLE_MODES = {'none', 'ja', 'en', 'zh', 'all'}


def normalize_subtitle_mode(value) -> str:
    mode = str(value or 'none').strip().lower()
    return mode if mode in _VALID_SUBTITLE_MODES else 'none'


def generate_subtitles(*args, **kwargs):
    from uav_downloader.subtitles.engine import generate_subtitles as _generate_subtitles
    return _generate_subtitles(*args, **kwargs)

_yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

DEFAULT_BASELINE_DATE = _yesterday.strftime('%Y-%m-%d')

DEFAULT_BASELINE_DT = datetime(_yesterday.year, _yesterday.month, _yesterday.day, tzinfo=timezone.utc)

PER_VIDEO_FETCH_DELAY_SEC = 0.3

PER_LISTING_FETCH_DELAY_SEC = 0.3

SCAN_RETRY_BACKOFF_SEC = 10 * 60

MAX_SCAN_PAGES = None

DAILY_SCAN_PAGES = None

MAX_CONCURRENT = 2

DEFAULT_SCAN_INTERVAL_HOURS = 24

DEFAULT_DAILY_SCAN_TIME = '18:00'

MIN_SCAN_INTERVAL_HOURS = 1

MAX_SCAN_INTERVAL_HOURS = 168

def _format_elapsed(seconds: float) -> str:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError, OverflowError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}'

@dataclass(frozen=True)
class ScanSchedulePlan:
    due: bool
    delay_seconds: float
    target_local: datetime
    daily_slot: Optional[str] = None

def _normalize_scan_schedule(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    mode = str(raw.get('mode') or 'interval').strip().lower()
    if mode not in {'interval', 'daily'}:
        mode = 'interval'

    try:
        interval_hours = int(raw.get(
            'interval_hours', DEFAULT_SCAN_INTERVAL_HOURS))
    except (TypeError, ValueError):
        interval_hours = DEFAULT_SCAN_INTERVAL_HOURS
    if not MIN_SCAN_INTERVAL_HOURS <= interval_hours <= MAX_SCAN_INTERVAL_HOURS:
        interval_hours = DEFAULT_SCAN_INTERVAL_HOURS

    daily_time = str(
        raw.get('daily_time') or DEFAULT_DAILY_SCAN_TIME).strip()
    match = re.fullmatch(r'(\d{2}):(\d{2})', daily_time)
    if (not match or int(match.group(1)) > 23 or
            int(match.group(2)) > 59):
        daily_time = DEFAULT_DAILY_SCAN_TIME

    return {
        'mode': mode,
        'interval_hours': interval_hours,
        'daily_time': daily_time,
    }

def _parse_utc_timestamp(value) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value or ''))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _plan_next_scan(
        cfg: dict,
        *,
        now_utc: Optional[datetime] = None,
        now_local: Optional[datetime] = None) -> ScanSchedulePlan:
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    now_local = now_local or datetime.now().astimezone()
    if now_local.tzinfo is None:
        now_local = now_local.astimezone()

    schedule = _normalize_scan_schedule(cfg.get('scan_schedule'))
    if schedule['mode'] == 'interval':
        last_check = _parse_utc_timestamp(cfg.get('last_check_iso'))
        if last_check is None:
            return ScanSchedulePlan(True, 0, now_local)
        target_utc = last_check + timedelta(
            hours=schedule['interval_hours'])
        delay = max(0.0, (target_utc - now_utc).total_seconds())
        return ScanSchedulePlan(
            delay <= 0,
            delay,
            target_utc.astimezone(now_local.tzinfo),
        )

    hour, minute = map(int, schedule['daily_time'].split(':'))
    today_target = now_local.replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    today_slot = (
        f'daily|{schedule["daily_time"]}|{now_local.date().isoformat()}')
    if cfg.get('last_daily_slot') == today_slot:
        target = today_target + timedelta(days=1)
        slot = (
            f'daily|{schedule["daily_time"]}|{target.date().isoformat()}')
        return ScanSchedulePlan(
            False, max(0.0, (target - now_local).total_seconds()),
            target, slot)
    if now_local >= today_target:
        return ScanSchedulePlan(True, 0, today_target, today_slot)
    return ScanSchedulePlan(
        False, max(0.0, (today_target - now_local).total_seconds()),
        today_target, today_slot)

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    # Moving the worker must not move an existing source checkout's state.
    APP_DIR = str(Path(__file__).resolve().parents[1] / 'apps')

def _default_output_folder() -> str:
    """Portable fallback used when the user has not selected a folder."""
    return os.path.join(APP_DIR, 'tmp')

def _months_before(base_date: date, months: int) -> date:
    """Return a calendar-month offset, clamping to the target month's last day."""
    if months < 0:
        raise ValueError('months must be non-negative')
    month_index = base_date.year * 12 + base_date.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def _missav_video_code(video: dict) -> str:
    candidate = dict(video)
    candidate['_site'] = 'MissAV'
    return video_code(candidate)

def _missav_video_version(video: dict) -> str:
    candidate = dict(video)
    candidate['_site'] = 'MissAV'
    versions = video_versions(candidate)
    if 'chinese-subtitle' in versions:
        return 'chinese-subtitle'
    if 'uncensored' in versions:
        return 'uncensored'
    return 'standard'

def _dedupe_missav_candidates(
        videos: list[dict],
        preference: str = DEFAULT_VERSION_PREFERENCE):
    """Backward-compatible wrapper for older tests and integrations."""
    return dedupe_video_candidates(videos, preference)

def _fallback_state_dir() -> str:
    product_root = product_paths.product_data_dir()
    current = product_root / 'watch'
    # product_data_dir() first imports the old v2 tree as "smalltool".  Copy
    # that state into its v3 name without deleting the original recovery copy.
    product_paths.migrate_directory(product_root / 'smalltool', current)
    return str(current)

def _state_dir_is_readable(path: str) -> bool:
    try:
        return os.path.isdir(path) and os.access(path, os.R_OK)
    except Exception:
        return False

def _state_dir_is_writable(path: str) -> bool:
    probe = os.path.join(path, f'.write_test_{os.getpid()}')
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, 'w', encoding='utf-8') as f:
            f.write('ok')
        os.remove(probe)
        return True
    except Exception:
        try:
            if os.path.exists(probe):
                os.remove(probe)
        except Exception:
            pass
        return False

def _select_state_dir() -> str:
    portable = str(product_paths.select_portable_state_dir(APP_DIR))
    try:
        if _state_dir_is_readable(portable):
            return portable
        if _state_dir_is_writable(portable):
            return portable
    except Exception:
        pass
    try:
        return _fallback_state_dir()
    except Exception:
        return portable

STATE_DIR = _select_state_dir()

CONFIG_PATH = os.path.join(STATE_DIR, 'config.json')

SEEN_PATH = os.path.join(STATE_DIR, 'seen.json')

_VALID_RESOLUTION_PREFS = {'highest', 'lowest', '1080', '720', '480', '360'}

def _normalize_version_pref(cfg: dict) -> str:
    value = cfg.get(
        'version_preference', cfg.get('missav_version_preference'))
    return normalize_version_preference(value)

def _targets_for_scan(targets: list[dict], version_preference: str) -> list[dict]:
    """Add MissAV's category×subtitle view before the normal fallback view."""
    expanded = []
    for target in targets:
        target_id = str(target.get('id') or '')
        if (target.get('site') == 'MissAV' and
                version_preference == 'chinese-subtitle' and
                target_id.startswith(('genres:', 'makers:', 'provider:'))):
            preferred = dict(target)
            preferred['_missav_filter'] = 'chinese-subtitle'
            expanded.append(preferred)
        expanded.append(target)
    return expanded

_CONFIG_LOCK = threading.RLock()

def _ensure_state_dir() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except Exception:
        pass

def _atomic_write(path: str, text: str) -> None:
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

def _default_config() -> dict:
    return {
        'output_folder': _default_output_folder(),
        'baseline_date': DEFAULT_BASELINE_DATE,
        # Optional per-site overrides.  Missing fields inherit the two global
        # values above so existing configurations keep their exact behaviour.
        'site_settings': {},
        'version_preference': DEFAULT_VERSION_PREFERENCE,
        'subtitle_mode': 'none',
        'scan_schedule': _normalize_scan_schedule(None),
        'first_run_done': False,
        # list of {"site": "JableTV", "id": "category:chinese-subtitle", ...}
        'selected_targets': [],
    }

def _valid_baseline_date(value) -> bool:
    try:
        datetime.strptime(str(value or '').strip(), '%Y-%m-%d')
    except (TypeError, ValueError):
        return False
    return True

def _normalize_site_settings(value) -> dict:
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for site_name, raw in value.items():
        if site_name not in SITES or not isinstance(raw, dict):
            continue
        item = {}
        folder = str(raw.get('output_folder') or '').strip()
        if folder:
            item['output_folder'] = folder
        baseline = str(raw.get('baseline_date') or '').strip()
        if _valid_baseline_date(baseline):
            item['baseline_date'] = baseline
        if item:
            normalized[site_name] = item
    return normalized

def _resolve_site_settings(cfg: dict, site_name: str) -> dict:
    global_folder = (
        str(cfg.get('output_folder') or '').strip() or
        _default_output_folder()
    )
    global_date = str(cfg.get('baseline_date') or '').strip()
    if not _valid_baseline_date(global_date):
        global_date = DEFAULT_BASELINE_DATE
    site_settings = _normalize_site_settings(cfg.get('site_settings'))
    override = site_settings.get(site_name, {})
    return {
        'output_folder': override.get('output_folder', global_folder),
        'baseline_date': override.get('baseline_date', global_date),
    }

def _normalize_loaded_config(cfg: dict) -> dict:
    if not str(cfg.get('output_folder') or '').strip():
        cfg['output_folder'] = _default_output_folder()
    if not _valid_baseline_date(cfg.get('baseline_date')):
        cfg['baseline_date'] = DEFAULT_BASELINE_DATE
    cfg['site_settings'] = _normalize_site_settings(
        cfg.get('site_settings'))
    cfg['version_preference'] = _normalize_version_pref(cfg)
    cfg['subtitle_mode'] = normalize_subtitle_mode(
        cfg.get('subtitle_mode'))
    cfg['scan_schedule'] = _normalize_scan_schedule(
        cfg.get('scan_schedule'))
    cfg.pop('missav_version_preference', None)
    return cfg

def _load_config_unlocked() -> dict:
    _ensure_state_dir()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return _normalize_loaded_config(cfg)
        except Exception:
            pass
    return _default_config()

def load_config() -> dict:
    with _CONFIG_LOCK:
        return _load_config_unlocked()

def save_config(cfg: dict) -> None:
    with _CONFIG_LOCK:
        _ensure_state_dir()
        normalized = _normalize_loaded_config(dict(cfg))
        _atomic_write(
            CONFIG_PATH,
            json.dumps(normalized, indent=2, ensure_ascii=False),
        )

def update_config(patch: dict, *, remove: tuple[str, ...] = ()) -> dict:
    """Atomically merge preferences/runtime state without stale overwrites."""
    with _CONFIG_LOCK:
        cfg = _load_config_unlocked()
        cfg.update(dict(patch))
        for key in remove:
            cfg.pop(key, None)
        cfg = _normalize_loaded_config(cfg)
        _atomic_write(
            CONFIG_PATH,
            json.dumps(cfg, indent=2, ensure_ascii=False),
        )
        return cfg

def _normalize_resolution_pref(cfg: dict) -> str:
    pref = cfg.get('resolution')
    if isinstance(pref, str):
        pref = pref.strip().lower()
        if pref in _VALID_RESOLUTION_PREFS:
            return pref
    if 'resolution' not in cfg:
        return 'lowest' if cfg.get('prefer_lowest_res', False) else 'highest'
    return 'highest'

def load_seen() -> dict:
    _ensure_state_dir()
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, 'r', encoding='utf-8') as f:
                seen = json.load(f)
            return seen if isinstance(seen, dict) else {}
        except Exception:
            pass
    return {}

def save_seen(seen: dict) -> None:
    _ensure_state_dir()
    _atomic_write(SEEN_PATH, json.dumps(seen, indent=2, ensure_ascii=False))

class SmallToolWorker:
    """Background worker that scans selected site/category combos and downloads new videos."""

    def __init__(self, log_fn, status_fn=None, storage=None):
        self._storage = storage
        self._log = log_fn
        self._status = status_fn
        self._run_stop = StopContext(managed=True)
        self._stop = self._run_stop.requested
        self._thread: Optional[threading.Thread] = None
        self._control = threading.Condition(threading.RLock())
        self._run_generation = 0
        self._thread_local = threading.local()
        self._mode: Optional[str] = None
        self._scan_active = False
        self._manual_requested = False
        self._schedule_revision = 0
        self._seen = load_seen()
        self._seen_lock = threading.Lock()
        self._progress = None  # (done, total, speed_bps, title) or None
        self._progress_lock = threading.Lock()
        self._scan_state = None  # (site, category, page, eligible_count) or None
        self._scan_state_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._active_site = None
        self._active_site_lock = threading.Lock()
        self._subtitle_mode = 'none'

    def _set_status(self, key: str, color: str = TEXT_DIM):
        if not self._status:
            return
        generation = getattr(self._thread_local, 'generation', None)
        with self._control:
            if (generation is not None and
                    generation != self._run_generation):
                return
        try:
            self._status(key, color, generation)
        except TypeError:
            # Backward compatibility for integrations using the former
            # two-argument callback.
            self._status(key, color)

    def get_progress(self):
        with self._progress_lock:
            return self._progress

    def get_scan_state(self):
        with self._scan_state_lock:
            return self._scan_state

    def _set_scan_state(self, site=None, category='', page=0, eligible_count=0):
        with self._scan_state_lock:
            self._scan_state = (
                (site, category, page, eligible_count) if site else None)

    def _halt_for_storage_error(self, error):
        # Keep the current video pending and preserve validated segment files.
        # A damaged/full destination requires an explicit user retry, never a
        # scheduled retry that could repeatedly write to the failing device.
        self._log(f'[STORAGE-ERR] {error}')
        with self._control:
            self._run_stop.request(StopReason.STORAGE_ERROR)
            self._manual_requested = False
            self._control.notify_all()
        self._set_status('st_storage_failed', ERROR_C)

    @property
    def run_generation(self) -> int:
        with self._control:
            return self._run_generation

    def _start(self, mode: str) -> bool:
        with self._control:
            if ((self._thread and self._thread.is_alive()) or
                    self._scan_lock.locked()):
                return False
            context = self._run_stop = StopContext(managed=True)
            self._stop = context.requested
            self._manual_requested = False
            self._run_generation += 1
            generation = self._run_generation
            self._mode = mode
            target = self._run if mode == 'monitor' else self._run_once
            self._thread = threading.Thread(
                target=self._execute_run, args=(target, generation, context), daemon=True)
            thread = self._thread
            # Register and start under the same lock: another start/stop must
            # not mistake a registered, not-yet-started thread for a dead run.
            try:
                thread.start()
            except BaseException:
                context.finish()
                self._thread = None
                self._mode = None
                raise
        return True

    def _execute_run(self, target, generation, context):
        try:
            with execution_scope(context):
                context.check()
                target(generation)
        except DownloadStopped:
            pass
        finally:
            self._finish_run()
            context.finish()

    def start_monitoring(self) -> bool:
        return self._start('monitor')

    def start_once(self) -> bool:
        return self._start('once')

    def start(self):
        """Backward-compatible alias for continuous monitoring."""
        return self.start_monitoring()

    def stop(self, reason=StopReason.CANCEL, *, cleanup=True):
        with self._control:
            self._run_stop.request(reason, cleanup=cleanup)
            self._manual_requested = False
            # Invalidate callbacks already queued by the old run.
            self._run_generation += 1
            self._control.notify_all()
        self._set_scan_state()

    def cancel_active_download(self):
        with self._active_site_lock:
            site_obj = self._active_site
        if site_obj and hasattr(site_obj, 'cancel_download'):
            try:
                context = self._run_stop
                if hasattr(site_obj, 'request_stop'):
                    site_obj.request_stop(context.reason or StopReason.CANCEL,
                                          cleanup=context.cleanup_requested)
                site_obj.cancel_download(cleanup=context.cleanup_requested)
            except Exception:
                pass

    def is_running(self) -> bool:
        with self._control:
            return self._thread is not None and self._thread.is_alive()

    def is_stopping(self) -> bool:
        with self._control:
            return bool(self._stop.is_set() and self._thread is not None and self._thread.is_alive())

    def is_monitoring(self) -> bool:
        with self._control:
            return (
                self._mode == 'monitor' and self._thread is not None and
                self._thread.is_alive())

    def is_scanning(self) -> bool:
        with self._control:
            return self._scan_active

    def wait_until_stopped(self, timeout: Optional[float] = None) -> bool:
        with self._control:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return thread is None or not thread.is_alive()

    def request_scan_now(self) -> str:
        with self._control:
            if self._thread is not None and self._thread.is_alive():
                if self._scan_active:
                    return 'running'
            if (self._mode != 'monitor' or self._thread is None or
                    not self._thread.is_alive()):
                return 'stopped'
            self._manual_requested = True
            self._control.notify_all()
            return 'queued'

    def notify_schedule_changed(self):
        with self._control:
            self._schedule_revision += 1
            self._control.notify_all()

    def _finish_run(self):
        with self._control:
            self._scan_active = False
            if self._thread is threading.current_thread():
                self._mode = None
            self._control.notify_all()

    def _wait_for_control(
            self, delay_seconds: float, generation: int,
            schedule_revision: int):
        with self._control:
            self._control.wait_for(
                lambda: (
                    self._stop.is_set()
                    or generation != self._run_generation
                    or self._manual_requested
                    or schedule_revision != self._schedule_revision
                ),
                timeout=max(0.01, min(float(delay_seconds), 60.0)),
            )

    def _perform_scan(self, generation: int) -> bool:
        with self._control:
            if (self._stop.is_set() or
                    generation != self._run_generation):
                return False
            self._scan_active = True
        try:
            # Always load immediately before the scan. Preferences may have
            # changed while the monitor was waiting.
            cfg = (self._storage.load_config if self._storage is not None else load_config)()
            self._thread_local.config_loader = load_config
            try:
                # Keep the historical one-argument call contract so existing
                # integrations that replace this private scan hook continue to
                # work. The real implementation reads the thread-local loader.
                scan_ok = self._scan_and_download(cfg)
            finally:
                self._thread_local.config_loader = None
            if scan_ok and not self._stop.is_set():
                self._record_scan_success(cfg)
            return scan_ok
        finally:
            with self._control:
                self._scan_active = False
                self._control.notify_all()

    def _record_scan_success(
            self,
            cfg: dict,
            *,
            now_utc: Optional[datetime] = None,
            now_local: Optional[datetime] = None):
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        else:
            now_utc = now_utc.astimezone(timezone.utc)
        now_local = now_local or datetime.now().astimezone()
        if now_local.tzinfo is None:
            now_local = now_local.astimezone()

        latest = (self._storage.load_config if self._storage is not None else load_config)()
        schedule = _normalize_scan_schedule(latest.get('scan_schedule'))
        patch = {
            'first_run_done': True,
            'last_check_iso': now_utc.isoformat(),
        }
        if schedule['mode'] == 'daily':
            hour, minute = map(int, schedule['daily_time'].split(':'))
            target = now_local.replace(
                hour=hour, minute=minute, second=0, microsecond=0)
            if now_local >= target:
                patch['last_daily_slot'] = (
                    f'daily|{schedule["daily_time"]}|'
                    f'{now_local.date().isoformat()}')
        (self._storage.update_config if self._storage is not None else update_config)(patch)
        cfg.update(patch)

    # ── Main loop ────────────────────────────────────────────────────
    def _run_once(self, generation: int):
        self._thread_local.generation = generation
        try:
            scan_ok = self._perform_scan(generation)
            if (not self._stop.is_set() and
                    generation == self.run_generation):
                self._set_status(
                    'st_idle' if scan_ok else 'st_detect_failed',
                    TEXT_DIM if scan_ok else WARNING)
        except DownloadStopped:
            pass
        except Exception as e:
            self._log(f'[ERROR] scan failed: {e}')
            self._set_status('st_detect_failed', WARNING)
        finally:
            self._finish_run()

    def _run(self, generation: int):
        self._thread_local.generation = generation
        self._log(T('st_worker_started'))
        retry_deadline = None
        try:
            while not self._stop.is_set():
                with self._control:
                    if generation != self._run_generation:
                        break
                    manual = self._manual_requested
                    self._manual_requested = False
                    schedule_revision = self._schedule_revision

                due = manual
                delay = 0.0
                if not due and retry_deadline is not None:
                    delay = max(0.0, retry_deadline - time.monotonic())
                    due = delay <= 0
                elif not due:
                    plan = _plan_next_scan((self._storage.load_config if self._storage is not None else load_config)())
                    due = plan.due
                    delay = plan.delay_seconds

                if not due:
                    self._set_status('st_waiting_schedule', SUCCESS)
                    self._wait_for_control(
                        delay, generation, schedule_revision)
                    continue

                try:
                    scan_ok = self._perform_scan(generation)
                except DownloadStopped:
                    scan_ok = False
                except Exception as e:
                    scan_ok = False
                    self._log(f'[ERROR] scan failed: {e}')
                if (self._stop.is_set() or
                        generation != self.run_generation):
                    break
                if scan_ok:
                    retry_deadline = None
                    self._set_status('st_running', SUCCESS)
                else:
                    retry_deadline = (
                        time.monotonic() + SCAN_RETRY_BACKOFF_SEC)
                    self._set_status('st_detect_failed', WARNING)
        finally:
            self._log(T('st_worker_stopped'))
            self._finish_run()

    # Chinese numerals → int
    _CN_NUMS = {
        '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
    }

    @classmethod
    def _parse_cn_number(cls, s: str) -> Optional[int]:
        if not s:
            return None
        if '十' in s:
            parts = s.split('十')
            left = parts[0]
            right = parts[1] if len(parts) > 1 else ''
            tens = cls._CN_NUMS.get(left, 1) if left else 1
            ones = cls._CN_NUMS.get(right, 0) if right else 0
            return tens * 10 + ones
        if len(s) == 1 and s in cls._CN_NUMS:
            return cls._CN_NUMS[s]
        return None

    @classmethod
    def _parse_relative_date(cls, rel_text: str, now: Optional[datetime] = None) -> Optional[datetime]:
        if not rel_text:
            return None
        if now is None:
            now = datetime.now(timezone.utc)
        text = rel_text.strip()

        def _apply_delta(n: int, unit: str) -> Optional[datetime]:
            if unit in ('秒', 'second'):
                delta = timedelta(seconds=n)
            elif unit in ('分鐘', '分', 'minute'):
                delta = timedelta(minutes=n)
            elif unit in ('小時', '時間', 'hour'):
                delta = timedelta(hours=n)
            elif unit in ('天', '日', 'day'):
                delta = timedelta(days=n)
            elif unit in ('星期', '週', '周', '週間', 'week'):
                delta = timedelta(weeks=n)
            elif unit in ('月', '個月', 'ヶ月', 'か月', 'カ月', 'month'):
                delta = timedelta(days=n * 30)
            elif unit in ('年', '個年', 'year'):
                delta = timedelta(days=n * 365)
            else:
                return None
            return now - delta

        m = re.match(
            r'\s*(\d+|[一二兩三四五六七八九十]+)'
            r'\s*(個)?\s*'
            r'(分鐘|小時|天|星期|週|周|個?月|個?年)\s*前',
            text,
        )
        if m:
            num_raw = m.group(1)
            if num_raw.isdigit():
                n = int(num_raw)
            else:
                n = cls._parse_cn_number(num_raw)
                if n is None:
                    return None
            return _apply_delta(n, m.group(3))

        low = text.lower()
        if low in ('just now', 'today'):
            return now
        if low == 'yesterday':
            return now - timedelta(days=1)
        if low == 'a minute ago':
            return now - timedelta(minutes=1)
        if low == 'an hour ago':
            return now - timedelta(hours=1)
        m = re.match(
            r'\s*(\d+)\s*'
            r'(second|minute|hour|day|week|month|year)s?\s+ago',
            low,
            re.I,
        )
        if m:
            return _apply_delta(int(m.group(1)), m.group(2).lower())

        if text == '今日':
            return now
        if text == '昨日':
            return now - timedelta(days=1)
        m = re.match(r'\s*(\d+)\s*(秒|分|時間|週間|日|ヶ月|か月|カ月|年)\s*前', text)
        if m:
            return _apply_delta(int(m.group(1)), m.group(2))
        return None

    def _fetch_video_date(self, vurl: str) -> tuple[Optional[datetime], str]:
        """Fetch a video detail page and extract its post datetime (JableTV only)."""
        if cloudscraper is None or BeautifulSoup is None:
            return (None, '')
        try:
            scraper = JableTVBrowser._get_scraper()
            def _validate(resp):
                s = BeautifulSoup(resp.content, 'html.parser')
                return bool(s.find(class_='info-header'))
            r, host, reason = fetch_with_mirrors(scraper, vurl, 'jable', _validate, timeout=30)
            if reason == 'blocked':
                return (None, 'BLOCKED')
            if reason != 'ok':
                return (None, '')
            soup = BeautifulSoup(r.content, 'html.parser')
            info = soup.find(class_='info-header')
            if not info:
                return (None, '')
            span = info.find('span', class_='mr-3')
            if not span:
                return (None, '')
            rel_text = span.get_text(strip=True)
            return (self._parse_relative_date(rel_text), rel_text)
        except Exception as e:
            return (None, f'ERR:{type(e).__name__}')

    def _fetch_missav_video_date(self, vurl: str) -> tuple[Optional[datetime], str]:
        """Fetch a MissAV video page and extract its release date."""
        if BeautifulSoup is None:
            return (None, '')
        try:
            scraper = MissAVBrowser._get_scraper()
            def _validate(resp):
                s = BeautifulSoup(resp.content, 'html.parser')
                page_text = s.get_text(' ', strip=True)
                return bool(re.search(r'(発売日|發售日|配信開始日|Release\s*Date|上架日期|更新)', page_text, re.I) or
                            s.find('meta', property='og:title') or 'og:title' in resp.text)
            r, host, reason = fetch_with_mirrors(scraper, vurl, 'missav', _validate, timeout=30)
            if reason == 'blocked':
                return (None, 'BLOCKED')
            if reason != 'ok':
                return (None, '')

            soup = BeautifulSoup(r.content, 'html.parser')
            page_text = soup.get_text(' ', strip=True)

            # Method 1: date near known keywords (発売日, Release Date, etc.)
            for pat in [
                r'(?:発売日|發售日|配信開始日|Release\s*Date|上架日期|更新)\s*[:：]?\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})',
                r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*(?:発売|release|上架)',
            ]:
                m = re.search(pat, page_text, re.I)
                if m:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if 2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                        return (datetime(y, mo, d, tzinfo=timezone.utc),
                                f'{y}-{mo:02d}-{d:02d}')

            # Method 2: meta tags (og / video release_date)
            for meta in soup.find_all('meta'):
                prop = (meta.get('property') or meta.get('name') or '').lower()
                if any(k in prop for k in ('release', 'date', 'published')):
                    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', meta.get('content', ''))
                    if m:
                        return (datetime(int(m.group(1)), int(m.group(2)),
                                         int(m.group(3)), tzinfo=timezone.utc),
                                m.group(0))

            # Method 3: <time> element
            time_el = soup.find('time', attrs={'datetime': True})
            if time_el:
                m = re.match(r'(\d{4})-(\d{2})-(\d{2})', time_el['datetime'])
                if m:
                    return (datetime(int(m.group(1)), int(m.group(2)),
                                     int(m.group(3)), tzinfo=timezone.utc),
                            m.group(0))

            return (None, '')
        except Exception as e:
            return (None, f'ERR:{type(e).__name__}')

    def _fetch_hanime1_video_date(self, vurl: str) -> tuple[Optional[datetime], str]:
        """Confirm a Hanime1 date when a compact genre card omits it."""
        try:
            date_text = SITES['Hanime1']['browser'].fetch_video_date(vurl)
            if not date_text:
                return (None, '')
            parsed = datetime.strptime(date_text, '%Y-%m-%d').replace(
                tzinfo=timezone.utc)
            return (parsed, date_text)
        except Exception as exc:
            return (None, f'ERR:{type(exc).__name__}')

    @staticmethod
    def _parse_huangguo_listing_date(value: str) -> Optional[datetime]:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime((value or '').strip(), '%Y-%m-%d').replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _fetch_huangguo_video_date(self, vurl: str) -> tuple[Optional[datetime], str]:
        """Never infer a series/episode date from its title or numeric URL."""
        try:
            date_text = SITES['Huangguo']['browser'].fetch_video_date(
                vurl, cancel_check=self._stop.is_set)
            return self._parse_huangguo_listing_date(date_text), date_text
        except CancelledError:
            raise
        except Exception as exc:
            return None, f'ERR:{type(exc).__name__}'

    @staticmethod
    def _parse_supjav_listing_date(value: str) -> Optional[datetime]:
        try:
            return datetime.strptime((value or '').strip(), '%Y/%m/%d').replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    def _fetch_page_for_site(self, site_name: str, url: str) -> list:
        """Fetch a listing page using the appropriate browser for the site."""
        browser = SITES[site_name]['browser']
        # The scan loop distinguishes a real empty page from a failed request.
        # Converting failures to [] would incorrectly complete the first scan.
        if site_name == 'Huangguo':
            return browser.fetch_page(url, cancel_check=self._stop.is_set)
        return browser.fetch_page(url)

    def _category_fetch_url(self, site_name: str, cat_name: str, cat_url: str) -> str:
        if site_name == 'MissAV':
            lang = T('missav_lang')
            if lang:
                # Insert the language segment after /dm{N}/ (same scheme as MissAVBrowser),
                # independent of localized display names.
                new_url = re.sub(r'(/dm\d+/)', rf'\1{lang}/', cat_url)
                return new_url
            return cat_url
        if site_name == 'SupJav':
            return SupJavBrowser._with_lang(cat_url, T('supjav_lang'))
        # JableTV does not expose language-specific listing variants.
        return cat_url

    def _build_page_url(self, site_name: str, base_url: str, page: int) -> str:
        """Build paginated URL for the given site."""
        if page <= 1:
            return base_url
        if site_name == 'JableTV':
            # JableTV uses ?sort_by=post_date&from=N
            if '?' in base_url:
                return f'{base_url}&from={page}'
            return f'{base_url}?from={page}'
        return SITES[site_name]['browser'].page_url(base_url, page)

    def _scan_and_download(self, cfg: dict, *, config_loader=None):
        if not self._scan_lock.acquire(blocking=False):
            self._log(f'[WAIT] {T("st_scan_running")}')
            return False
        if config_loader is None:
            config_loader = getattr(
                self._thread_local, 'config_loader', None)
        try:
            return self._scan_and_download_locked(
                cfg, config_loader=config_loader)
        except StorageWriteError as exc:
            self._halt_for_storage_error(exc)
            return False
        finally:
            self._set_scan_state()
            self._scan_lock.release()

    def _scan_and_download_locked(self, cfg: dict, *, config_loader=None):
        dest = str(cfg.get('output_folder') or '').strip() or _default_output_folder()
        cfg['output_folder'] = dest
        if not _valid_baseline_date(cfg.get('baseline_date')):
            cfg['baseline_date'] = DEFAULT_BASELINE_DATE
        cfg['site_settings'] = _normalize_site_settings(
            cfg.get('site_settings'))
        version_preference = _normalize_version_pref(cfg)
        cfg['version_preference'] = version_preference
        subtitle_mode = normalize_subtitle_mode(cfg.get('subtitle_mode'))
        cfg['subtitle_mode'] = subtitle_mode
        self._subtitle_mode = subtitle_mode
        cfg.pop('missav_version_preference', None)
        targets = cfg.get('selected_targets', [])
        if not targets:
            self._log(f'[WAIT] {T("st_no_targets_selected")}')
            return True

        site_runtime = {
            site_name: _resolve_site_settings(cfg, site_name)
            for site_name in {
                target.get('site') for target in targets
                if target.get('site') in SITES
            }
        }
        for settings in site_runtime.values():
            try:
                os.makedirs(settings['output_folder'], exist_ok=True)
            except OSError as exc:
                raise StorageWriteError(settings['output_folder'], exc) from exc

        first_run = not cfg.get('first_run_done', False)
        is_jable_site = any(t['site'] == 'JableTV' for t in targets)

        self._set_status('st_scanning', ACCENT)
        date_summary = ', '.join(
            f'{site_name} {settings["baseline_date"]}'
            for site_name, settings in site_runtime.items()
        )
        self._log(f'{"First run" if first_run else "Daily check"} — '
                  f'{len(targets)} target(s), baseline {date_summary}')

        all_new_videos = []
        scan_blocked = False
        scan_failed = False
        scan_had_success = False

        for target in _targets_for_scan(targets, version_preference):
            if self._stop.is_set():
                return False
            site_name = target['site']
            if site_name not in SITES:
                self._log(f'[WARN] site not found: {site_name}')
                continue
            settings = site_runtime[site_name]
            baseline_str = settings['baseline_date']
            baseline_dt = datetime.strptime(
                baseline_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            site_dest = settings['output_folder']
            cat = find_target(site_name, target.get('id'), target.get('category'))
            if not cat:
                cat_name = target.get('category') or target.get('id') or '?'
                self._log(f'[WARN] category not found: {site_name}/{cat_name}')
                continue
            cat_name = target_label(cat)
            cat_url = cat['url']

            self._log(f'── {site_name} / {cat_name} ──')

            base_url = self._category_fetch_url(site_name, cat_name, cat_url)
            preferred_filter = target.get('_missav_filter')
            if preferred_filter:
                sep = '&' if '?' in base_url else '?'
                base_url = f'{base_url}{sep}filters={preferred_filter}'
                cat_name = f'{cat_name} · {T("st_pref_chinese")}'
            if site_name == 'JableTV' and 'sort_by=' not in base_url:
                sep = '&' if '?' in base_url else '?'
                base_url = f'{base_url}{sep}sort_by=post_date'

            max_pages = MAX_SCAN_PAGES if first_run else DAILY_SCAN_PAGES
            reached_baseline = False
            consecutive_skips = 0
            listing_fingerprints = set()

            pages = count(1) if max_pages is None else range(1, max_pages + 1)
            for page in pages:
                if self._stop.is_set():
                    return False
                if reached_baseline:
                    break
                if page > 1 and self._stop.wait(PER_LISTING_FETCH_DELAY_SEC):
                    return False

                page_url = self._build_page_url(site_name, base_url, page)
                self._set_scan_state(
                    site_name, cat_name, page, len(all_new_videos))
                self._log(f'  Page {page}: {page_url}')
                try:
                    videos = self._fetch_page_for_site(site_name, page_url)
                except CancelledError:
                    return False
                except MirrorsBlockedError:
                    scan_blocked = True
                    self._log(f'  [BLOCKED] Cloudflare blocked all mirrors: {page_url}')
                    break
                except StorageWriteError:
                    raise
                except Exception as exc:
                    scan_failed = True
                    self._log(f'  [ERR] {site_name} listing fetch failed: {exc}')
                    break
                scan_had_success = True
                if not videos:
                    self._log(f'  Page {page}: no videos — end.')
                    break
                fingerprint = frozenset(
                    str(video.get('url') or '').strip() for video in videos
                    if video.get('url'))
                if not fingerprint or fingerprint in listing_fingerprints:
                    self._log(f'  Page {page}: repeated listing or no usable URLs — end.')
                    break
                listing_fingerprints.add(fingerprint)

                target_id = str(cat.get('id') or target.get('id') or '')
                for video in videos:
                    video['_site'] = site_name
                    video['_target_id'] = target_id
                    video['_category'] = cat_name
                    video['_download_dest'] = site_dest

                self._log(f'  Page {page}: {len(videos)} video(s)')
                page_all_seen = True

                for v in videos:
                    if self._stop.is_set():
                        return False
                    vurl = v.get('url', '')
                    if not vurl:
                        continue
                    with self._seen_lock:
                        seen_entry = self._seen.get(vurl)
                    if isinstance(seen_entry, dict):
                        known_versions = set(video_versions(v))
                        stored_versions = seen_entry.get('versions', [])
                        if isinstance(stored_versions, str):
                            stored_versions = [stored_versions]
                        if isinstance(stored_versions, (list, tuple, set)):
                            known_versions.update(
                                value for value in stored_versions
                                if value in VALID_VERSION_PREFERENCES)
                        reconsider_preferred = (
                            seen_entry.get('reason') == 'duplicate-version' and
                            (version_preference == 'all' or
                             version_preference in known_versions))
                        reconsider_older_baseline = False
                        if seen_entry.get('reason') == 'before-baseline':
                            release_date = seen_entry.get('release_date')
                            if release_date:
                                try:
                                    release_dt = datetime.strptime(
                                        release_date, '%Y-%m-%d').replace(
                                            tzinfo=timezone.utc)
                                    reconsider_older_baseline = (
                                        release_dt >= baseline_dt)
                                except (TypeError, ValueError):
                                    reconsider_older_baseline = True
                        if not (reconsider_preferred or
                                reconsider_older_baseline):
                            continue
                    elif seen_entry:
                        continue
                    page_all_seen = False

                    # Date check for JableTV (has detail pages with relative dates)
                    if site_name == 'JableTV':
                        video_dt, rel_text = self._fetch_video_date(vurl)
                        time.sleep(PER_VIDEO_FETCH_DELAY_SEC)
                        if rel_text == 'BLOCKED':
                            self._log(f'    [BLOCKED] defer date check: {vurl}')
                            continue
                        if video_dt is None:
                            self._log(f'    [SKIP] no date ({rel_text!r}): {vurl}')
                            consecutive_skips += 1
                            if consecutive_skips >= 10:
                                self._log(f'  10 consecutive skips — moving to next category.')
                                reached_baseline = True
                                break
                            continue
                        if video_dt < baseline_dt:
                            v['_release_date'] = video_dt.date().isoformat()
                            slug = vurl.rstrip('/').split('/')[-1]
                            self._log(f'    [STOP] {slug} — {rel_text} (before {baseline_str})')
                            self._mark_seen(
                                vurl, v.get('title', ''), skipped=True,
                                reason='before-baseline', video=v)
                            reached_baseline = True
                            break
                        consecutive_skips = 0
                        self._log(f'    [KEEP] {vurl.rstrip("/").split("/")[-1]} — {rel_text}')
                    elif site_name == 'MissAV':
                        # MissAV: fetch detail page for release date.
                        video_dt, rel_text = self._fetch_missav_video_date(vurl)
                        time.sleep(PER_VIDEO_FETCH_DELAY_SEC)
                        if rel_text == 'BLOCKED':
                            self._log(f'    [BLOCKED] defer date check: {vurl}')
                            continue
                        if video_dt is None:
                            self._log(f'    [SKIP] no confirmed date ({rel_text!r}): {vurl}')
                            consecutive_skips += 1
                            if consecutive_skips >= 10:
                                self._log(f'  10 consecutive skips — moving to next category.')
                                reached_baseline = True
                                break
                            continue
                        if video_dt is not None and video_dt < baseline_dt:
                            v['_release_date'] = video_dt.date().isoformat()
                            slug = vurl.rstrip('/').split('/')[-1]
                            self._log(f'    [SKIP] {slug} — {rel_text} (before {baseline_str})')
                            self._mark_seen(
                                vurl, v.get('title', ''), skipped=True,
                                reason='before-baseline', video=v)
                            consecutive_skips += 1
                            if consecutive_skips >= 10:
                                self._log(f'  10 consecutive skips — moving to next category.')
                                reached_baseline = True
                                break
                            continue
                        consecutive_skips = 0
                        self._log(f'    [KEEP] {vurl.rstrip("/").split("/")[-1]} — {rel_text}')
                    else:
                        # SupJav exposes an absolute listing date.  Most
                        # Hanime1 cards expose a relative date, while the
                        # compact 裏番/泡麵番 grids require detail confirmation.
                        # Huangguo uses ISO dates; missing dates need the same
                        # conservative confirmation, never a guessed "today".
                        rel_text = v.get('date', '')
                        video_dt = (
                            self._parse_relative_date(rel_text)
                            if site_name == 'Hanime1'
                            else self._parse_huangguo_listing_date(rel_text)
                            if site_name == 'Huangguo'
                            else self._parse_supjav_listing_date(rel_text))
                        if site_name == 'Hanime1' and video_dt is None:
                            video_dt, rel_text = self._fetch_hanime1_video_date(
                                vurl)
                            time.sleep(PER_VIDEO_FETCH_DELAY_SEC)
                        if site_name == 'Huangguo' and video_dt is None:
                            try:
                                video_dt, rel_text = self._fetch_huangguo_video_date(vurl)
                            except CancelledError:
                                return False
                            if rel_text.startswith('ERR:'):
                                scan_failed = True
                            if self._stop.wait(PER_VIDEO_FETCH_DELAY_SEC):
                                return False
                        if video_dt is None:
                            self._log(f'    [SKIP] no confirmed date ({rel_text!r}): {vurl}')
                            consecutive_skips += 1
                            if consecutive_skips >= 10:
                                self._log('  10 consecutive skips — moving to next category.')
                                reached_baseline = True
                                break
                            continue
                        if video_dt < baseline_dt:
                            v['_release_date'] = video_dt.date().isoformat()
                            slug = vurl.rstrip('/').split('/')[-1]
                            self._log(f'    [SKIP] {slug} — {rel_text} (before {baseline_str})')
                            self._mark_seen(
                                vurl, v.get('title', ''), skipped=True,
                                reason='before-baseline', video=v)
                            consecutive_skips += 1
                            if consecutive_skips >= 10:
                                self._log('  10 consecutive skips — moving to next category.')
                                reached_baseline = True
                                break
                            continue
                        consecutive_skips = 0
                        self._log(f'    [KEEP] {vurl.rstrip("/").split("/")[-1]} — {rel_text}')

                    all_new_videos.append(v)

                self._set_scan_state(
                    site_name, cat_name, page, len(all_new_videos))

                if not first_run and page_all_seen and not reached_baseline:
                    self._log('  All seen on this page — stopping.')
                    break

        if not all_new_videos:
            self._log(f'No new videos found.')
            if scan_blocked or scan_failed or not scan_had_success:
                self._log('[WARN] Scan incomplete — will retry before marking first run done.')
                return False
            cfg['first_run_done'] = True
            return True

        candidate_codes = {
            code for code in (video_code(video) for video in all_new_videos)
            if code
        }
        prior_downloads = self._successful_seen_candidates(candidate_codes)
        kept_candidates, dedupe_decisions = dedupe_video_candidates(
            prior_downloads + all_new_videos, version_preference)
        for dropped, kept, code in dedupe_decisions:
            dropped_slug = video_code(dropped) or url_slug(dropped.get('url', ''))
            kept_slug = video_code(kept) or url_slug(kept.get('url', ''))
            self._log(
                f'  [DEDUP] {code}: keep {kept_slug}; skip {dropped_slug} '
                f'(preference: {version_preference})')
            dropped_url = dropped.get('url', '')
            if (not dropped.get('_already_seen') and dropped_url and
                    dropped_url != kept.get('url', '')):
                self._mark_seen(
                    dropped_url, dropped.get('title', ''), skipped=True,
                    reason='duplicate-version', video=dropped)

        all_new_videos = [
            video for video in kept_candidates
            if not video.get('_already_seen')
        ]

        if not all_new_videos:
            self._log('No new videos remain after cross-site deduplication.')
            if scan_blocked or scan_failed or not scan_had_success:
                self._log('[WARN] Scan incomplete — will retry before marking first run done.')
                return False
            cfg['first_run_done'] = True
            return True

        self._set_scan_state()
        self._set_status('st_downloading', ACCENT)
        self._log(f'Found {len(all_new_videos)} new video(s). Downloading...')
        download_blocked = False
        download_incomplete = False
        subtitle_incomplete = False
        for v in all_new_videos:
            if self._stop.is_set():
                return False
            download_dest = str(v.get('_download_dest') or dest)
            if config_loader is not None:
                try:
                    latest_cfg = config_loader()
                    site_name = v.get('_site')
                    if isinstance(latest_cfg, dict) and site_name in SITES:
                        latest_dest = str(_resolve_site_settings(
                            latest_cfg, site_name)['output_folder'])
                        if latest_dest != download_dest:
                            self._log(
                                f'  [SETTINGS] {site_name} output folder '
                                f'updated during scan: {latest_dest}')
                        download_dest = latest_dest
                except Exception as exc:
                    self._log(
                        f'  [WARN] could not refresh output folder: {exc}')
            if version_preference == 'all':
                # Code-only filenames otherwise collide across versions of
                # the same release. Keep the selected site's base directory.
                variant = '+'.join(sorted(video_versions(v)))
                download_dest = os.path.join(download_dest, 'versions', variant)
            try:
                os.makedirs(download_dest, exist_ok=True)
            except OSError as exc:
                self._halt_for_storage_error(StorageWriteError(download_dest, exc))
                return False
            result = self._download_one(
                v, download_dest)
            if result == 'storage_failed':
                return False
            elif result == 'blocked':
                download_blocked = True
            elif result == 'download_incomplete':
                download_incomplete = True
            elif result == 'subtitle_failed':
                subtitle_incomplete = True

        if download_blocked:
            self._log('[WARN] Download blocked — will retry before marking first run done.')
            return False
        if download_incomplete:
            self._log(
                '[WARN] Download incomplete — saved segments will resume on '
                'the next check.')
            return False
        if subtitle_incomplete:
            self._log(f'[WARN] {T("subtitle_retry_pending")}')
            return False
        if scan_blocked or scan_failed or not scan_had_success:
            self._log('[WARN] Scan incomplete — first run flag not updated.')
            return False
        cfg['first_run_done'] = True
        return True

    def _download_one(self, video: dict, dest: str, subtitle_mode: Optional[str] = None):
        context = current_stop_context() or self._run_stop
        if context.requested.is_set():
            return
        with execution_scope(context):
            return self._download_one_owned(video, dest, subtitle_mode)

    def _download_one_owned(self, video: dict, dest: str, subtitle_mode: Optional[str] = None):
        vurl = video['url']
        title = video.get('title', '') or vurl.rstrip('/').split('/')[-1]
        site = video.get('_site', '?')
        self._log(f'↓ [{site}] {title}')

        # Show "preparing" state on progress bar
        with self._progress_lock:
            self._progress = (0, 0, 0, title)

        site_obj = None
        try:
            site_obj = M3U8Sites.CreateSite(vurl, dest)
            if self._stop.is_set():
                return
            source_subtitle_evidence = (
                trusted_chinese_subtitle_evidence(video))
            if (
                    site_obj is not None
                    and hasattr(site_obj, 'add_source_video_metadata')):
                site_obj.add_source_video_metadata(video)
            if not site_obj or not site_obj.is_url_vaildate():
                err = getattr(site_obj, '_last_error', None)
                if isinstance(err, MirrorsBlockedError):
                    raise err
                self._log(f'  [SKIP] invalid URL: {vurl}')
                self._mark_seen(vurl, title, skipped=True, video=video)
                return

            # Wire up progress callback for the progress bar
            def _on_progress(done, total, speed):
                if self._stop.is_set():
                    return
                with self._progress_lock:
                    self._progress = (done, total, speed, title)

            site_obj._progress_callback = _on_progress
            with self._active_site_lock:
                self._active_site = site_obj
            ok = site_obj.start_download()
            if self._stop.is_set():
                self._log('  [CANCELLED]')
                if self._run_stop.cleanup_requested:
                    self._cleanup_temp(site_obj)
                return
            if ok is False and not getattr(site_obj, '_cancel_job', False):
                self._log('  [ERR] download failed')
                self._cleanup_temp(site_obj)
                return

            if getattr(site_obj, '_cancel_job', False):
                self._log('  [CANCELLED]')
                if self._run_stop.cleanup_requested:
                    self._cleanup_temp(site_obj)
                return
            subtitle_mode = normalize_subtitle_mode(
                self._subtitle_mode if subtitle_mode is None else subtitle_mode)
            if subtitle_mode != 'none':
                subtitle_started = time.monotonic()
                last_stage = [None]
                stage_keys = {
                    'queued': 'subtitle_stage_queued',
                    'runtime': 'subtitle_stage_runtime',
                    'model': 'subtitle_stage_model',
                    'translation_model': 'subtitle_stage_translation_model',
                    'audio': 'subtitle_stage_audio',
                    'transcribe_ja': 'subtitle_stage_transcribe_ja',
                    'translate_en': 'subtitle_stage_translate_en',
                    'translate_zh': 'subtitle_stage_translate_zh',
                    'done': 'subtitle_stage_done',
                }

                def _subtitle_progress(stage, percent):
                    key = stage_keys.get(stage, 'subtitle_stage_queued')
                    phase = T(key)
                    if stage != last_stage[0]:
                        self._log(f'  [SUBTITLE] {phase}')
                        last_stage[0] = stage
                    elapsed = T(
                        'subtitle_elapsed',
                        elapsed=_format_elapsed(
                            time.monotonic() - subtitle_started),
                    )
                    detail = f'{phase} · {elapsed}'
                    with self._progress_lock:
                        if percent is None:
                            self._progress = (
                                0, -1, -1, f'{title} · {detail}')
                        else:
                            self._progress = (
                                percent, 100, -1, f'{title} · {detail}')
                    self._set_status('st_subtitling', ACCENT)

                try:
                    from uav_downloader.subtitles.engine import SubtitleCancelled
                except Exception as exc:
                    self._log(
                        f'  [SUBTITLE-ERR] '
                        f'{T("subtitle_failed", error=translation_failure_message(exc))}')
                    return 'subtitle_failed'

                try:
                    evidence = set(source_subtitle_evidence)
                    evidence_getter = getattr(
                        site_obj, 'source_subtitle_evidence', None)
                    if callable(evidence_getter):
                        evidence.update(normalize_source_subtitle_evidence(
                            evidence_getter()))
                    evidence = normalize_source_subtitle_evidence(evidence)
                    subtitle_kwargs = {
                        'progress_callback': _subtitle_progress,
                        'cancel_check': lambda: (
                            self._stop.is_set()
                            or bool(getattr(
                                site_obj, '_cancel_job', False))),
                    }
                    if evidence:
                        subtitle_kwargs[
                            'source_subtitle_evidence'] = evidence
                    subtitle_result = generate_subtitles(
                        site_obj._get_video_savename(), subtitle_mode,
                        **subtitle_kwargs)
                    if subtitle_result.satisfied_by_source:
                        self._log(
                            f'  [SUBTITLE] '
                            f'{T("subtitle_source_chinese_skip")}')
                    if subtitle_result.no_speech:
                        self._log(f'  [SUBTITLE] {T("subtitle_no_speech")}')
                    elif not subtitle_result.files:
                        if not subtitle_result.satisfied_by_source:
                            self._log(
                                f'  [SUBTITLE-ERR] '
                                f'{T("subtitle_failed", error=T("subtitle_empty_result"))}')
                            return 'subtitle_failed'
                    else:
                        self._log(T(
                            'subtitle_ready', count=len(subtitle_result.files)))
                except SubtitleCancelled:
                    self._log(f'  [CANCELLED] {T("subtitle_cancelled")}')
                    return
                except StorageWriteError:
                    raise
                except Exception as exc:
                    self._log(f'  [SUBTITLE-ERR] {T("subtitle_failed", error=str(exc))}')
                    return 'subtitle_failed'
            if self._stop.is_set():
                return
            self._log(f'  [OK] {title}')
            self._mark_seen(vurl, title, video=video)
        except StorageWriteError as exc:
            self._halt_for_storage_error(exc)
            return 'storage_failed'
        except DownloadStopped:
            return
        except DownloadIncompleteError as e:
            # Each crawler stores validated segments in a URL-specific temp
            # folder.  Preserve those files so the next unattended check only
            # requests the missing tail instead of restarting a large video.
            self._log(f'  [RETRY] {e}')
            return 'download_incomplete'
        except MirrorsBlockedError as e:
            self._log(f'  [BLOCKED] {e}')
            if site_obj:
                self._cleanup_temp(site_obj)
            return 'blocked'
        except Exception as e:
            if self._stop.is_set():
                if site_obj and self._run_stop.cleanup_requested:
                    self._cleanup_temp(site_obj)
                return
            self._log(f'  [ERR] {e}')
            if site_obj:
                self._cleanup_temp(site_obj)
        finally:
            with self._active_site_lock:
                if self._active_site is site_obj:
                    self._active_site = None
            with self._progress_lock:
                self._progress = None

    def _cleanup_temp(self, site_obj):
        """Remove temp folder with partial segment clips if final video doesn't exist."""
        if self._stop.is_set() and not self._run_stop.cleanup_requested:
            return
        try:
            if site_obj.is_target_video_exist():
                return  # Video completed, nothing to clean
            cleanup = getattr(site_obj, 'cleanup_temp', None)
            if callable(cleanup) and cleanup():
                self._log(f'  [CLEANUP] removed partial clips')
        except Exception:
            pass

    def _successful_seen_candidates(self, candidate_codes: set[str]) -> list[dict]:
        """Return successful prior downloads that can collide with this scan."""
        if not candidate_codes:
            return []
        with self._seen_lock:
            seen_items = list(self._seen.items())

        candidates = []
        for url, entry in seen_items:
            if not isinstance(entry, dict) or entry.get('skipped'):
                continue
            candidate = {
                'url': url,
                'title': entry.get('title', ''),
                '_site': entry.get('site') or site_from_url(url),
                '_code': entry.get('code', ''),
                '_versions': entry.get('versions', []),
                '_already_seen': True,
            }
            code = video_code(candidate)
            if code and code in candidate_codes:
                candidate['_code'] = code
                candidates.append(candidate)
        return candidates

    def _mark_seen(self, url: str, title: str, skipped: bool = False,
                   reason: Optional[str] = None,
                   video: Optional[dict] = None):
        with self._seen_lock:
            entry = {
                'title': title,
                'at': datetime.now(timezone.utc).isoformat(),
                'skipped': skipped,
            }
            if reason:
                entry['reason'] = reason
            if video:
                site = video.get('_site') or site_from_url(url)
                code = video_code(video)
                versions = sorted(video_versions(video))
                if site:
                    entry['site'] = site
                if code:
                    entry['code'] = code
                if versions:
                    entry['versions'] = versions
                release_date = video.get('_release_date')
                if release_date:
                    entry['release_date'] = str(release_date)
            self._seen[url] = entry
            (self._storage.save_seen if self._storage is not None else save_seen)(self._seen)
