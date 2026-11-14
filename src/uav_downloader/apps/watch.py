from uav_downloader.core.shutdown import ShutdownAttempt, require_stopped, remaining
from uav_downloader.core.stop import StopTimeoutError
from uav_downloader.core.stop import StopContext, StopReason, DownloadStopped, current_stop_context, execution_scope
#!/usr/bin/env python
# coding: utf-8
"""UAV Watcher — unattended downloader with site/category/date selection.

Supports JableTV, MissAV, SupJav, Hanime1, and Huangguo. The user picks sites and categories
(multi-select), and a baseline date. The worker scans selected categories
daily and downloads any new video it hasn't seen before.

Author: ALOS
"""

import calendar
import ctypes
import json
import multiprocessing
import os
import shutil
import sys
import threading
import time
import re
from concurrent.futures import CancelledError
from itertools import count
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Optional

if __name__ == '__main__':
    # Dispatch frozen multiprocessing children before SSL, crash logging,
    # crawler, or Tk imports.  Translation workers must remain windowless.
    multiprocessing.freeze_support()

# --- issue #23: harden the SSL cert path before curl_cffi (pulled in by M3U8Sites)
# imports, so a non-UTF-8 OpenSSL/cert default path can't crash startup. ---
import os as _os, ssl as _ssl
try:
    import certifi as _certifi
    _ca = _certifi.where()
    if _ca and _os.path.exists(_ca):
        _os.environ.setdefault('SSL_CERT_FILE', _ca)
        _os.environ.setdefault('SSL_CERT_DIR', _os.path.dirname(_ca))
        try:
            _ssl.get_default_verify_paths()
        except (UnicodeDecodeError, SystemError):
            _dvp = _ssl.DefaultVerifyPaths(_ca, _os.path.dirname(_ca),
                                           'SSL_CERT_FILE', _ca,
                                           'SSL_CERT_DIR', _os.path.dirname(_ca))
            _ssl.get_default_verify_paths = lambda: _dvp
except Exception:
    pass

# issue #24: global crash logger -> crash_log.txt + copyable dialog
try:
    from uav_downloader.core import crashlog
    crashlog.install()
except Exception:
    pass


def _diagnostic_env(*names):
    for name in names:
        if name in os.environ:
            return os.environ.get(name)
    return None


def _run_translation_diagnostic_if_requested():
    whisper_input = _diagnostic_env(
        'UAV_WHISPER_DIAGNOSTIC_INPUT',
        'ALOS_WHISPER_DIAGNOSTIC_INPUT',
        'JABLE_WHISPER_DIAGNOSTIC_INPUT')
    whisper_output = _diagnostic_env(
        'UAV_WHISPER_DIAGNOSTIC_OUTPUT',
        'ALOS_WHISPER_DIAGNOSTIC_OUTPUT',
        'JABLE_WHISPER_DIAGNOSTIC_OUTPUT')
    if whisper_input is not None or whisper_output is not None:
        if not (whisper_input and whisper_input.strip()
                and whisper_output and whisper_output.strip()):
            raise SystemExit(2)
        try:
            input_path = os.path.abspath(whisper_input)
            output_path = os.path.abspath(whisper_output)
            if os.path.normcase(input_path) == os.path.normcase(output_path):
                raise ValueError('diagnostic input and output must differ')
            if not os.path.isfile(input_path):
                raise FileNotFoundError('diagnostic input is not a file')
            if (os.path.isdir(output_path)
                    or not os.path.isdir(os.path.dirname(output_path))):
                raise FileNotFoundError(
                    'diagnostic output directory is unavailable')
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass
            from uav_downloader.subtitles.engine import run_whisper_diagnostic
            run_whisper_diagnostic(input_path, output_path)
            if not os.path.isfile(output_path):
                raise RuntimeError('diagnostic did not produce its report')
        except (Exception, SystemExit):
            # Frozen verification is machine-consumed.  Keep failures
            # deterministic and never echo media paths, transcripts, or keys.
            raise SystemExit(2) from None
        raise SystemExit(0)

    local_output = _diagnostic_env(
        'UAV_LOCAL_TRANSLATION_DIAGNOSTIC_OUTPUT',
        'ALOS_LOCAL_TRANSLATION_DIAGNOSTIC_OUTPUT',
        'JABLE_LOCAL_TRANSLATION_DIAGNOSTIC_OUTPUT') or ''
    if local_output:
        try:
            output_path = os.path.abspath(local_output.strip())
            if (os.path.isdir(output_path)
                    or not os.path.isdir(os.path.dirname(output_path))):
                raise FileNotFoundError(
                    'diagnostic output directory is unavailable')
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass
            from uav_downloader.subtitles.engine import run_local_translation_diagnostic
            run_local_translation_diagnostic(output_path)
            if not os.path.isfile(output_path):
                raise RuntimeError('diagnostic did not produce its report')
        except (Exception, SystemExit):
            raise SystemExit(2) from None
        raise SystemExit(0)

    local_soak_output = _diagnostic_env(
        'UAV_LOCAL_TRANSLATION_SOAK_DIAGNOSTIC_OUTPUT',
        'ALOS_LOCAL_TRANSLATION_SOAK_DIAGNOSTIC_OUTPUT',
        'JABLE_LOCAL_TRANSLATION_SOAK_DIAGNOSTIC_OUTPUT') or ''
    if local_soak_output:
        try:
            output_path = os.path.abspath(local_soak_output.strip())
            if (os.path.isdir(output_path)
                    or not os.path.isdir(os.path.dirname(output_path))):
                raise FileNotFoundError(
                    'diagnostic output directory is unavailable')
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass
            from uav_downloader.subtitles.engine import (
                run_local_translation_worker_soak_diagnostic,
            )
            run_local_translation_worker_soak_diagnostic(output_path)
            if not os.path.isfile(output_path):
                raise RuntimeError('diagnostic did not produce its report')
        except (Exception, SystemExit):
            raise SystemExit(2) from None
        raise SystemExit(0)

    llm_output = _diagnostic_env(
        'UAV_LLM_TRANSLATION_DIAGNOSTIC_OUTPUT',
        'ALOS_LLM_TRANSLATION_DIAGNOSTIC_OUTPUT',
        'JABLE_LLM_TRANSLATION_DIAGNOSTIC_OUTPUT') or ''
    if llm_output:
        try:
            output_path = os.path.abspath(llm_output.strip())
            if (os.path.isdir(output_path)
                    or not os.path.isdir(os.path.dirname(output_path))):
                raise FileNotFoundError(
                    'diagnostic output directory is unavailable')
            try:
                os.remove(output_path)
            except FileNotFoundError:
                pass
            from uav_downloader.subtitles.engine import run_llm_translation_diagnostic
            run_llm_translation_diagnostic(output_path)
            if not os.path.isfile(output_path):
                raise RuntimeError('diagnostic did not produce its report')
        except (Exception, SystemExit):
            raise SystemExit(2) from None
        raise SystemExit(0)


if __name__ == '__main__':
    _run_translation_diagnostic_if_requested()

# Enable DPI awareness (Windows)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from uav_downloader import sites as M3U8Sites
from uav_downloader.sites.jabletv import JableTVBrowser
from uav_downloader.sites.missav import MissAVBrowser
from uav_downloader.sites.supjav import SupJavBrowser
from uav_downloader.sites.base import (
    DownloadIncompleteError,
    MirrorsBlockedError,
    fetch_with_mirrors,
)
from uav_downloader.core import config
from uav_downloader.i18n.locales import T, set_lang, get_lang, ui_font, LANGUAGES
from uav_downloader.apps.watch_categories import (
    SITES,
    find_target,
    group_label,
    iter_targets,
    selection_key,
    target_label,
)
from uav_downloader.ui.translation_settings import (
    open_translation_settings_dialog,
    translation_failure_message,
    translation_provider_summary,
)

# Keep the optional AI subtitle stack off UAV Watcher's startup path.  Frozen
# one-file builds should be able to paint the window before importing requests,
# FFmpeg discovery, and the subtitle runtime; those are only needed after a
# video has finished downloading.
from uav_downloader.core.watcher import (
    APP_DIR,
    _yesterday,
    DEFAULT_BASELINE_DATE,
    DEFAULT_BASELINE_DT,
    PER_VIDEO_FETCH_DELAY_SEC,
    PER_LISTING_FETCH_DELAY_SEC,
    SCAN_RETRY_BACKOFF_SEC,
    MAX_SCAN_PAGES,
    DAILY_SCAN_PAGES,
    MAX_CONCURRENT,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_DAILY_SCAN_TIME,
    MIN_SCAN_INTERVAL_HOURS,
    MAX_SCAN_INTERVAL_HOURS,
    _format_elapsed,
    ScanSchedulePlan,
    _normalize_scan_schedule,
    _parse_utc_timestamp,
    _plan_next_scan,
    _default_output_folder,
    _months_before,
    _missav_video_code,
    _missav_video_version,
    _dedupe_missav_candidates,
    _fallback_state_dir,
    _state_dir_is_readable,
    _state_dir_is_writable,
    _select_state_dir,
    STATE_DIR,
    CONFIG_PATH,
    SEEN_PATH,
    _VALID_RESOLUTION_PREFS,
    _normalize_version_pref,
    _targets_for_scan,
    _CONFIG_LOCK,
    _ensure_state_dir,
    _atomic_write,
    _default_config,
    _valid_baseline_date,
    _normalize_site_settings,
    _resolve_site_settings,
    _normalize_loaded_config,
    _load_config_unlocked,
    load_config,
    save_config,
    update_config,
    _normalize_resolution_pref,
    load_seen,
    save_seen,
    SmallToolWorker,
)

_VALID_SUBTITLE_MODES = {'none', 'ja', 'en', 'zh', 'all'}


def normalize_subtitle_mode(value) -> str:
    mode = str(value or 'none').strip().lower()
    return mode if mode in _VALID_SUBTITLE_MODES else 'none'


def generate_subtitles(*args, **kwargs):
    from uav_downloader.subtitles.engine import generate_subtitles as _generate_subtitles
    return _generate_subtitles(*args, **kwargs)
from uav_downloader.core.video_identity import (
    DEFAULT_VERSION_PREFERENCE,
    VALID_VERSION_PREFERENCES,
    dedupe_video_candidates,
    normalize_source_subtitle_evidence,
    normalize_version_preference,
    site_from_url,
    trusted_chinese_subtitle_evidence,
    url_slug,
    video_code,
    video_versions,
)
from uav_downloader.ui.theme import (
    ACCENT, ACCENT_HOVER, ACCENT_DIM, SUCCESS, WARNING, ERROR_C, ERROR_DIM,
    BG_DARK, BG_CARD, BG_CARD_HOVER, BG_INPUT, BG_HEADER, BG_SECTION,
    BG_BADGE, TEXT_PRI, TEXT_SEC, TEXT_DIM, BORDER, BORDER_HOVER,
    BORDER_CARD, WHITE, CARD_RADIUS, CONTROL_RADIUS, color_for_mode,
    category_columns_for_width,
)
from uav_downloader.core import paths as product_paths
from uav_downloader.core.storage import StorageWriteError
from uav_downloader.metadata import APP_DISPLAY_NAMES, VERSION

# Optional direct-fetch fallback for diagnostics / when cloudscraper struggles
try:
    import cloudscraper
    from bs4 import BeautifulSoup
except Exception:
    cloudscraper = None
    BeautifulSoup = None

# ── Constants ────────────────────────────────────────────────────────
APP_NAME = APP_DISPLAY_NAMES['watch']
APP_VERSION = VERSION
DEFAULT_WINDOW_WIDTH = 1180
DEFAULT_WINDOW_HEIGHT = 780
MIN_WINDOW_WIDTH = 760
MIN_WINDOW_HEIGHT = 440
# A configured baseline can span thousands of listing pages. End-of-list,
# known entries, baseline dates and repeated pages bound each scan instead.
# Keep the optional limits available for diagnostics/tests, disabled by default.












def _initial_window_size(
        work_width: int, work_height: int) -> tuple[int, int]:
    horizontal_margin = 24
    vertical_margin = 40 if work_height >= 600 else 24
    width = min(DEFAULT_WINDOW_WIDTH, max(
        320, int(work_width) - horizontal_margin))
    height = min(DEFAULT_WINDOW_HEIGHT, max(
        320, int(work_height) - vertical_margin))
    return width, height


def _settings_viewport_height(window_height: int | float) -> int:
    """Keep advanced settings usable without pushing the main controls away."""
    return max(104, min(260, int(window_height) - 290))


def _logical_work_area(window) -> tuple[int, int]:
    try:
        scaling = max(float(window._get_window_scaling()), 1.0)
    except Exception:
        scaling = 1.0
    if os.name == 'nt':
        class _RECT(ctypes.Structure):
            _fields_ = [
                ('left', ctypes.c_long),
                ('top', ctypes.c_long),
                ('right', ctypes.c_long),
                ('bottom', ctypes.c_long),
            ]

        rect = _RECT()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rect), 0):
                return (
                    max(1, round((rect.right - rect.left) / scaling)),
                    max(1, round((rect.bottom - rect.top) / scaling)),
                )
        except Exception:
            pass
    return (
        max(1, round(window.winfo_screenwidth() / scaling)),
        max(1, round(window.winfo_screenheight() / scaling)),
    )

# ── Site / category registry ────────────────────────────────────────
# The grouped stable-ID registry lives in smalltool_categories.py.

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

























# ── Persistence ──────────────────────────────────────────────────────






























# ── Downloader core ──────────────────────────────────────────────────


# ── GUI ──────────────────────────────────────────────────────────────
class SmallToolApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self._lang_code_by_name = {name: code for code, name in LANGUAGES}
        self._lang_name_by_code = {code: name for code, name in LANGUAGES}
        self._theme_mode = config.get_theme()
        ctk.set_appearance_mode(self._theme_mode)
        ctk.set_default_color_theme('blue')

        stored = config.get_ui_lang()
        set_lang(stored or 'en')
        self._needs_lang_prompt = (stored is None)

        self._update_window_title()
        work_width, work_height = _logical_work_area(self)
        window_width, window_height = _initial_window_size(
            work_width, work_height)
        self.geometry(f'{window_width}x{window_height}')
        self.minsize(
            min(MIN_WINDOW_WIDTH, window_width),
            min(MIN_WINDOW_HEIGHT, window_height),
        )
        self.configure(fg_color=BG_DARK)

        self._cfg = load_config()
        self._cfg['resolution'] = _normalize_resolution_pref(self._cfg)
        self._cfg['version_preference'] = _normalize_version_pref(self._cfg)
        self._cfg.pop('missav_version_preference', None)
        from uav_downloader.sites.base import set_resolution_pref
        set_resolution_pref(self._cfg['resolution'])
        self._log_queue: list[str] = []
        self._log_lock = threading.Lock()
        self._is_closing = False
        self._rebuilding = False
        self._build_gen = 0
        self._status_key = 'st_idle'
        self._status_fg = TEXT_DIM
        self._worker = SmallToolWorker(log_fn=self._enqueue_log,
                                       status_fn=self._set_status_threadsafe)
        self._check_vars: dict[str, tk.BooleanVar] = {}  # stable "site|target-id" keys
        self._target_widgets: list[tuple[object, str]] = []
        self._filter_groups: list[dict] = []
        self._category_groups: list[list[object]] = []
        self._category_columns = category_columns_for_width(
            window_width)
        self._categories_collapsed = False
        self._settings_expanded = False
        self._activity_visible = False
        self._progress_visible = False
        self._schedule_popup = None
        self._progress_display_mode = 'idle'
        self._resize_after_id = None

        self._build_ui()
        self.bind('<Configure>', self._on_root_resize, add='+')
        self._load_selections_from_config()
        self._sync_select_all_vars()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.after_idle(self._fit_window_to_work_area)

        # Auto-start if configured
        if self._cfg.get('output_folder') and self._cfg.get('selected_targets'):
            self.after_idle(self._auto_start_worker)

        self._schedule_log_flush()
        self._schedule_progress_refresh()
        if self._needs_lang_prompt:
            self.after(300, self._first_run_language_prompt)

    def _update_window_title(self):
        self.title(T('st_window_title', app=APP_NAME, version=APP_VERSION))

    def _fit_window_to_work_area(self):
        """Center the realized outer window without crossing the work area."""
        if self._is_closing:
            return
        try:
            self.update_idletasks()
            if os.name == 'nt':
                class _RECT(ctypes.Structure):
                    _fields_ = [
                        ('left', ctypes.c_long),
                        ('top', ctypes.c_long),
                        ('right', ctypes.c_long),
                        ('bottom', ctypes.c_long),
                    ]

                work = _RECT()
                outer = _RECT()
                client_hwnd = self.winfo_id()
                get_ancestor = ctypes.windll.user32.GetAncestor
                get_ancestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                get_ancestor.restype = ctypes.c_void_p
                hwnd = get_ancestor(client_hwnd, 2) or client_hwnd
                if (ctypes.windll.user32.SystemParametersInfoW(
                        0x0030, 0, ctypes.byref(work), 0) and
                        ctypes.windll.user32.GetWindowRect(
                            hwnd, ctypes.byref(outer))):
                    width = outer.right - outer.left
                    height = outer.bottom - outer.top
                    x = work.left + max(
                        0, ((work.right - work.left) - width) // 2)
                    y = work.top + max(
                        0, ((work.bottom - work.top) - height) // 2)
                    ctypes.windll.user32.SetWindowPos(
                        hwnd, 0, x, y, 0, 0,
                        0x0001 | 0x0004 | 0x0010)
                    return
            x = max(0, (self.winfo_screenwidth() - self.winfo_width()) // 2)
            y = max(0, (self.winfo_screenheight() - self.winfo_height()) // 2)
            self.geometry(f'+{x}+{y}')
        except (tk.TclError, RuntimeError):
            pass

    def _ask_language_first_run(self):
        popup = None
        try:
            mode = ctk.get_appearance_mode().lower()
            bg = color_for_mode(BG_DARK, mode)
            card = color_for_mode(BG_CARD, mode)
            fg = color_for_mode(TEXT_PRI, mode)
            accent = color_for_mode(ACCENT, mode)
            popup = tk.Toplevel(self)
            popup.title(T('st_lang_picker_title'))
            popup.configure(bg=bg)
            popup.resizable(False, False)
            popup.transient(self)

            picker_font = ui_font()
            tk.Label(
                popup, text=T('st_lang_picker_title'),
                bg=bg, fg=fg,
                font=(picker_font, 14, 'bold')).pack(padx=28, pady=(24, 12))

            def _choose(code='en'):
                config.set_ui_lang(code)
                if code != get_lang():
                    self._apply_language(code)
                try:
                    popup.destroy()
                except tk.TclError:
                    pass

            for code, name in LANGUAGES:
                tk.Button(
                    popup, text=name, width=24,
                    bg=card, fg=fg,
                    activebackground=accent, activeforeground='#ffffff',
                    relief='flat', bd=0, padx=12, pady=8,
                    font=(picker_font, 11),
                    command=lambda c=code: _choose(c)).pack(padx=28, pady=4)

            popup.protocol('WM_DELETE_WINDOW', lambda: _choose(get_lang() or 'en'))
            popup.update_idletasks()
            x = max(0, (popup.winfo_screenwidth() - popup.winfo_width()) // 2)
            y = max(0, (popup.winfo_screenheight() - popup.winfo_height()) // 3)
            popup.geometry(f'+{x}+{y}')
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
        if getattr(self, '_is_closing', False):
            return
        self._ask_language_first_run()

    def _on_lang_change(self, name: str):
        code = self._lang_code_by_name.get(name, 'en')
        if code != get_lang():
            self._apply_language(code)

    def _snapshot_ui_state(self) -> dict:
        check_state = 'normal'
        if hasattr(self, '_check_now_btn'):
            try:
                check_state = self._check_now_btn.cget('state')
            except tk.TclError:
                pass
        categories_collapsed = self._categories_collapsed
        activity_visible = self._activity_visible
        if self._settings_expanded:
            categories_collapsed = getattr(
                self, '_categories_before_settings', categories_collapsed)
            activity_visible = getattr(
                self, '_activity_before_settings', activity_visible)
        return {
            'folder': self._folder_var.get() if hasattr(self, '_folder_var') else self._cfg.get('output_folder', ''),
            'baseline_date': self._date_var.get() if hasattr(self, '_date_var') else self._cfg.get('baseline_date', DEFAULT_BASELINE_DATE),
            'site_settings': {
                site: dict(values)
                for site, values in _normalize_site_settings(
                    self._cfg.get('site_settings')).items()
            },
            'selected_targets': self._get_selected_targets() if self._check_vars else self._cfg.get('selected_targets', []),
            'resolution': self._cfg.get('resolution', 'highest'),
            'version_preference': self._cfg.get(
                'version_preference', DEFAULT_VERSION_PREFERENCE),
            'subtitle_mode': normalize_subtitle_mode(
                self._cfg.get('subtitle_mode')),
            'recognition_quality': config.get_recognition_quality(),
            'max_workers_per_video': self._commit_workers_preference(),
            'running': self._worker.is_running(),
            'check_now_state': check_state,
            'status_key': self._status_key,
            'status_fg': self._status_fg,
            'categories_collapsed': categories_collapsed,
            'settings_expanded': self._settings_expanded,
            'activity_visible': activity_visible,
            'category_site': self._active_category_site(),
            'category_filter': (self._category_filter_var.get()
                                if hasattr(self, '_category_filter_var') else ''),
        }

    def _restore_ui_state(self, snapshot: dict):
        self._cfg['output_folder'] = snapshot['folder']
        self._cfg['baseline_date'] = snapshot['baseline_date']
        self._cfg['site_settings'] = _normalize_site_settings(
            snapshot.get('site_settings'))
        self._cfg['resolution'] = snapshot['resolution']
        self._cfg['version_preference'] = snapshot['version_preference']
        self._cfg['subtitle_mode'] = snapshot['subtitle_mode']
        from uav_downloader.sites.base import set_resolution_pref
        set_resolution_pref(snapshot['resolution'])
        self._folder_var.set(snapshot['folder'])
        self._date_var.set(snapshot['baseline_date'])
        self._res_var.set(self._resolution_label())
        self._version_var.set(self._version_label())
        self._subtitle_var.set(self._subtitle_label())
        self._recognition_quality_var.set(
            self._recognition_quality_label(snapshot['recognition_quality']))
        self._workers_var.set(str(snapshot['max_workers_per_video']))

        for var in self._check_vars.values():
            var.set(False)
        self._restore_target_checks(snapshot['selected_targets'])
        self._sync_select_all_vars()
        if self.__dict__.get('_category_tab_names'):
            site = snapshot.get('category_site')
            if site in self._category_tab_names:
                self._category_tabview.set(self._category_tab_names[site])
            self._category_filter_var.set(snapshot.get('category_filter', ''))
            self._on_category_tab_changed()

        running = snapshot['running']
        self._start_btn.configure(state='disabled' if running else 'normal')
        self._stop_btn.configure(state='normal' if running else 'disabled')
        if running and snapshot['status_key'] in ('st_idle', 'st_stopped'):
            self._set_status_key('st_running', SUCCESS)
        else:
            self._set_status_key(snapshot['status_key'], snapshot['status_fg'])
        self._check_now_btn.configure(state=snapshot['check_now_state'])
        self._set_categories_collapsed(snapshot['categories_collapsed'])
        self._set_activity_visible(snapshot['activity_visible'])
        self._set_settings_expanded(snapshot['settings_expanded'])
        self._refresh_site_settings_summary()
        self._refresh_schedule_summary()

    def _apply_language(self, code: str):
        self._rebuilding = True
        try:
            snapshot = self._snapshot_ui_state()
            set_lang(code)
            config.set_ui_lang(code)
            self._build_gen += 1
            self._cancel_category_work()

            for child in self.winfo_children():
                try:
                    child.destroy()
                except tk.TclError:
                    pass

            self._check_vars = {}
            self._target_widgets = []
            self._filter_groups = []
            self._category_groups = []
            self._build_ui()
            self._restore_ui_state(snapshot)
            self._update_window_title()
        finally:
            self._rebuilding = False

    def _resolution_label(self) -> str:
        pref = self._cfg.get('resolution', 'highest')
        if pref == 'lowest':
            return T('st_resolution_lowest')
        if pref in {'1080', '720', '480', '360'}:
            return f'{pref}p'
        return T('st_resolution_highest')

    def _resolution_values(self) -> list[str]:
        return [T('st_resolution_highest'), '1080p', '720p', '480p', '360p',
                T('st_resolution_lowest')]

    def _resolution_pref_from_label(self, label) -> str:
        label = str(label or '').strip()
        if label == T('st_resolution_lowest'):
            return 'lowest'
        if label in {'1080p', '720p', '480p', '360p'}:
            return label[:-1]
        return 'highest'

    def _filename_mode_values(self) -> list[str]:
        return [T('filename_mode_full'), T('filename_mode_code')]

    def _filename_mode_from_label(self, label: str) -> str:
        if str(label or '') == T('filename_mode_code'):
            return 'code-only'
        return 'full-title'

    def _filename_mode_label(self, mode=None) -> str:
        if mode is None:
            mode = config.get_filename_mode()
        mode = config.normalize_filename_mode(mode)
        if mode == 'code-only':
            return T('filename_mode_code')
        return T('filename_mode_full')

    def _version_label(self) -> str:
        pref = self._cfg.get('version_preference', DEFAULT_VERSION_PREFERENCE)
        return {
            'chinese-subtitle': T('st_pref_chinese'),
            'uncensored': T('st_pref_uncensored'),
            'standard': T('st_pref_standard'),
            'english-subtitle': T('st_pref_english'),
            'reducing-mosaic': T('st_pref_reducing_mosaic'),
            'all': T('st_pref_all'),
        }.get(pref, T('st_pref_chinese'))

    def _version_values(self) -> list[str]:
        return [
            T('st_pref_chinese'),
            T('st_pref_uncensored'),
            T('st_pref_standard'),
            T('st_pref_english'),
            T('st_pref_reducing_mosaic'),
            T('st_pref_all'),
        ]

    def _version_pref_from_label(self, label: str) -> str:
        return {
            T('st_pref_chinese'): 'chinese-subtitle',
            T('st_pref_uncensored'): 'uncensored',
            T('st_pref_standard'): 'standard',
            T('st_pref_english'): 'english-subtitle',
            T('st_pref_reducing_mosaic'): 'reducing-mosaic',
            T('st_pref_all'): 'all',
        }.get(str(label or ''), DEFAULT_VERSION_PREFERENCE)

    def _subtitle_label(self) -> str:
        return {
            'none': T('subtitle_none'),
            'ja': T('subtitle_ja'),
            'en': T('subtitle_en'),
            'zh': T('subtitle_zh'),
            'all': T('subtitle_all'),
        }.get(normalize_subtitle_mode(self._cfg.get('subtitle_mode')),
              T('subtitle_none'))

    def _subtitle_values(self) -> list[str]:
        return [
            T('subtitle_none'), T('subtitle_ja'), T('subtitle_en'),
            T('subtitle_zh'), T('subtitle_all'),
        ]

    def _subtitle_pref_from_label(self, label: str) -> str:
        return {
            T('subtitle_none'): 'none',
            T('subtitle_ja'): 'ja',
            T('subtitle_en'): 'en',
            T('subtitle_zh'): 'zh',
            T('subtitle_all'): 'all',
        }.get(str(label or ''), 'none')

    def _recognition_quality_label(self, quality=None) -> str:
        quality = (
            config.get_recognition_quality()
            if quality is None else str(quality or '').strip().lower())
        return {
            'auto': T('recognition_quality_auto'),
            'quality': T('recognition_quality_quality'),
            'balanced': T('recognition_quality_balanced'),
            'fast': T('recognition_quality_fast'),
        }.get(quality, T('recognition_quality_auto'))

    def _recognition_quality_values(self) -> list[str]:
        return [
            T('recognition_quality_auto'),
            T('recognition_quality_quality'),
            T('recognition_quality_balanced'),
            T('recognition_quality_fast'),
        ]

    def _recognition_quality_from_label(self, label: str) -> str:
        return {
            T('recognition_quality_auto'): 'auto',
            T('recognition_quality_quality'): 'quality',
            T('recognition_quality_balanced'): 'balanced',
            T('recognition_quality_fast'): 'fast',
        }.get(str(label or ''), 'auto')

    def _open_translation_settings(self):
        open_translation_settings_dialog(
            self, on_saved=self._refresh_translation_provider_status)

    def _refresh_translation_provider_status(self):
        label = getattr(self, '_translation_provider_status_lbl', None)
        if label is None:
            return
        try:
            label.configure(text=translation_provider_summary(short=True))
        except tk.TclError:
            pass

    def _set_status_key(self, key: str, fg: str = TEXT_DIM):
        self._status_key = key
        self._status_fg = fg
        text = T(key) if key.startswith('st_') else key
        if hasattr(self, '_status_lbl'):
            try:
                self._status_lbl.configure(text=text, text_color=fg)
            except tk.TclError:
                pass

    def _theme_glyph(self):
        return {'system': '◐', 'light': '☀', 'dark': '☾'}.get(self._theme_mode, '◐')

    def _cycle_theme(self):
        modes = ('system', 'light', 'dark')
        try:
            index = modes.index(self._theme_mode)
        except ValueError:
            index = 0
        self._theme_mode = modes[(index + 1) % len(modes)]
        ctk.set_appearance_mode(self._theme_mode)
        config.set_theme(self._theme_mode)
        self._theme_btn.configure(text=self._theme_glyph())

    def _on_root_resize(self, event):
        if event.widget is not self or self._is_closing:
            return
        try:
            scaling = max(self._get_window_scaling(), 1.0)
            logical_width = event.width / scaling
            logical_height = event.height / scaling
        except Exception:
            logical_width = event.width
            logical_height = event.height
        if hasattr(self, '_settings_scroll'):
            try:
                self._settings_scroll.configure(
                    height=_settings_viewport_height(logical_height))
            except (tk.TclError, AttributeError):
                pass
        columns = category_columns_for_width(logical_width)
        if columns == self._category_columns:
            return
        self._category_columns = columns
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        try:
            self._resize_after_id = self.after(160, self._reflow_category_grid)
        except tk.TclError:
            self._resize_after_id = None

    def _reflow_category_grid(self):
        self._resize_after_id = None
        # grid_configure can remap a previously grid-removed checkbox.
        for group in self.__dict__.get('_filter_groups', []):
            group['item_visibility'] = {}
        for widgets in self._category_groups:
            for index, widget in enumerate(widgets):
                try:
                    widget.grid_configure(
                        row=index // self._category_columns,
                        column=index % self._category_columns)
                except tk.TclError:
                    pass
        if hasattr(self, '_category_filter_var'):
            self._filter_targets()

    def _build_ui(self):
        font_family = ui_font()
        self._update_window_title()

        # ── Header ──────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG_HEADER, corner_radius=0, height=72)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)

        brand = ctk.CTkFrame(hdr, fg_color='transparent')
        brand.pack(side='left', fill='y', padx=20)
        ctk.CTkLabel(
            brand, text=T('st_header'), text_color=TEXT_PRI,
            font=(font_family, 17, 'bold')).pack(anchor='w', pady=(10, 0))
        ctk.CTkLabel(
            brand, text=T('st_subtitle').upper(), text_color=ACCENT,
            font=('Consolas', 9, 'bold')).pack(anchor='w', pady=(0, 10))

        right_info = ctk.CTkFrame(hdr, fg_color='transparent')
        right_info.pack(side='right', fill='y', padx=20)

        version_box = ctk.CTkFrame(
            right_info, fg_color=BG_BADGE, corner_radius=6,
            border_width=1, border_color=BORDER)
        version_box.pack(side='right', padx=(10, 0), pady=18)
        ctk.CTkLabel(
            version_box, text=f'v{APP_VERSION}', text_color=TEXT_SEC,
            font=('Consolas', 10, 'bold')).pack(padx=10, pady=4)

        self._theme_btn = ctk.CTkButton(
            right_info, text=self._theme_glyph(), width=36, height=36,
            corner_radius=CONTROL_RADIUS, fg_color=BG_CARD,
            border_width=1, border_color=BORDER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(font_family, 14), command=self._cycle_theme)
        self._theme_btn.pack(side='right', padx=(8, 0), pady=18)

        self._lang_var = ctk.StringVar(
            value=self._lang_name_by_code.get(get_lang(), 'English'))
        self._lang_menu = ctk.CTkOptionMenu(
            right_info, values=[name for _, name in LANGUAGES],
            variable=self._lang_var, command=self._on_lang_change,
            width=126, height=36, corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 11), dropdown_font=(font_family, 11))
        self._lang_menu.pack(side='right', pady=18)

        ctk.CTkFrame(self, height=1, fg_color=BORDER, corner_radius=0).pack(fill='x')

        main = ctk.CTkFrame(self, fg_color='transparent')
        main.pack(fill='both', expand=True, padx=18, pady=(14, 12))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        main.grid_rowconfigure(4, weight=0)
        self._main_frame = main

        # ── Config row: folder + date ───────────────────────────────
        cfg_card = ctk.CTkFrame(
            main, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        cfg_card.grid(row=0, column=0, sticky='ew')
        cfg_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            cfg_card, text=T('st_save_location'), text_color=TEXT_SEC,
            font=(font_family, 11, 'bold'), width=98, anchor='w').grid(
                row=0, column=0, padx=(16, 8), pady=(14, 8), sticky='w')
        self._folder_var = tk.StringVar(
            value=self._cfg.get('output_folder') or _default_output_folder())
        ctk.CTkEntry(
            cfg_card, textvariable=self._folder_var, height=38,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, font=(font_family, 11)).grid(
                row=0, column=1, padx=8, pady=(14, 8), sticky='ew')
        folder_actions = ctk.CTkFrame(cfg_card, fg_color='transparent')
        folder_actions.grid(
            row=0, column=2, padx=(8, 16), pady=(14, 8))
        ctk.CTkButton(
            folder_actions, text=T('st_browse'), width=82, height=38,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
            font=(font_family, 11), command=self._pick_folder).pack(
                side='left')
        self._settings_toggle_btn = ctk.CTkButton(
            folder_actions, text=T('st_settings_expand'),
            width=104, height=38,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(font_family, 10, 'bold'),
            command=self._toggle_settings)
        self._settings_toggle_btn.pack(side='left', padx=(6, 0))

        settings_scroll = ctk.CTkScrollableFrame(
            cfg_card,
            height=_settings_viewport_height(
                max(self.winfo_height(), MIN_WINDOW_HEIGHT)),
            fg_color='transparent', corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        settings_scroll.grid(
            row=1, column=0, columnspan=3, padx=(8, 4),
            pady=(0, 10), sticky='ew')
        settings_scroll.grid_columnconfigure(1, weight=1)
        self._settings_scroll = settings_scroll

        proxy_label = ctk.CTkLabel(
            settings_scroll, text=T('proxy_url_label'), text_color=TEXT_SEC,
            font=(font_family, 11, 'bold'), width=98, anchor='w')
        proxy_label.grid(
            row=0, column=0, padx=(8, 8), pady=(2, 2), sticky='w')
        self._proxy_var = tk.StringVar(value=config.get_proxy_url())
        proxy_entry = ctk.CTkEntry(
            settings_scroll, textvariable=self._proxy_var,
            placeholder_text=T('proxy_url_placeholder'), height=34,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, font=(font_family, 10))
        proxy_entry.grid(
            row=0, column=1, padx=8, pady=(2, 2), sticky='ew')
        proxy_actions = ctk.CTkFrame(
            settings_scroll, fg_color='transparent')
        proxy_actions.grid(row=0, column=2, padx=(8, 8), pady=(2, 2))
        ctk.CTkButton(
            proxy_actions, text=T('proxy_save'), width=58, height=34,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=WHITE,
            font=(font_family, 9, 'bold'), command=self._on_proxy_save).pack(
                side='left')
        ctk.CTkButton(
            proxy_actions, text=T('proxy_windows'), width=70, height=34,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
            font=(font_family, 9), command=self._on_proxy_windows).pack(
                side='left', padx=(6, 0))
        ctk.CTkButton(
            proxy_actions, text=T('proxy_clear'), width=58, height=34,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(font_family, 9), command=self._on_proxy_clear).pack(
                side='left', padx=(6, 0))

        self._proxy_status_lbl = ctk.CTkLabel(
            settings_scroll, text='', text_color=TEXT_DIM,
            font=(font_family, 9), anchor='w')
        self._proxy_status_lbl.grid(
            row=1, column=1, columnspan=2, padx=8, pady=(0, 4), sticky='w')
        self._refresh_proxy_status()

        options = ctk.CTkFrame(settings_scroll, fg_color='transparent')
        options.grid(
            row=2, column=0, columnspan=3, padx=8, pady=(2, 8),
            sticky='ew')

        date_group = ctk.CTkFrame(options, fg_color='transparent')
        date_group.pack(fill='x', expand=True)
        date_controls = ctk.CTkFrame(date_group, fg_color='transparent')
        date_controls.pack(fill='x')
        ctk.CTkLabel(
            date_controls, text=T('st_baseline_date'), text_color=TEXT_SEC,
            font=(font_family, 10, 'bold')).pack(side='left')
        self._date_var = tk.StringVar(value=self._cfg.get('baseline_date', DEFAULT_BASELINE_DATE))
        ctk.CTkEntry(
            date_controls, textvariable=self._date_var, width=116, height=34,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, font=('Consolas', 10)).pack(
                side='left', padx=(8, 6))
        ctk.CTkButton(
            date_controls, text=T('st_calendar'), width=82, height=34,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
            font=(font_family, 9, 'bold'), command=self._open_calendar).pack(
                side='left', padx=(0, 6))
        self._date_preset_var = tk.StringVar(value=T('st_date_quick'))
        ctk.CTkOptionMenu(
            date_controls, variable=self._date_preset_var,
            values=[label for label, _kind, _amount in self._date_presets()],
            command=self._on_date_preset, width=132, height=34,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            button_color=BORDER_HOVER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 9), dropdown_font=(font_family, 9)).pack(
                side='left')

        ctk.CTkLabel(
            date_group, text=T('st_date_hint'), text_color=TEXT_DIM,
            font=(font_family, 9), wraplength=440, justify='left').pack(
                anchor='w', pady=(4, 0))

        site_settings_row = ctk.CTkFrame(date_group, fg_color='transparent')
        site_settings_row.pack(fill='x', pady=(8, 0))
        ctk.CTkButton(
            site_settings_row, text=T('st_site_settings_button'),
            width=178, height=34, corner_radius=CONTROL_RADIUS,
            fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(font_family, 9, 'bold'),
            command=self._open_site_settings).pack(side='left')
        self._site_settings_summary_lbl = ctk.CTkLabel(
            site_settings_row, text='', text_color=TEXT_DIM,
            font=(font_family, 9), anchor='w')
        self._site_settings_summary_lbl.pack(
            side='left', padx=(10, 0), fill='x', expand=True)
        self._refresh_site_settings_summary()

        res_group = ctk.CTkFrame(options, fg_color='transparent')
        res_group.pack(fill='x', pady=(12, 0))
        quality_row = ctk.CTkFrame(res_group, fg_color='transparent')
        quality_row.pack(fill='x')
        ctk.CTkLabel(
            quality_row, text=T('st_resolution'), text_color=TEXT_SEC,
            font=(font_family, 10, 'bold'), width=108, anchor='e').pack(
                side='left', padx=(0, 8))
        self._res_var = tk.StringVar(value=self._resolution_label())
        ctk.CTkOptionMenu(
            quality_row, variable=self._res_var, values=self._resolution_values(),
            command=self._on_res_change, width=178, height=34,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            button_color=BORDER_HOVER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 10), dropdown_font=(font_family, 10)).pack(side='left')

        filename_row = ctk.CTkFrame(res_group, fg_color='transparent')
        filename_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(
            filename_row, text=T('filename_mode_setting'),
            text_color=TEXT_SEC, font=(font_family, 10, 'bold'),
            width=108, anchor='e').pack(side='left', padx=(0, 8))
        self._filename_mode_var = tk.StringVar(
            value=self._filename_mode_label())
        ctk.CTkOptionMenu(
            filename_row, variable=self._filename_mode_var,
            values=self._filename_mode_values(),
            command=self._on_filename_mode_change, width=178, height=34,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            button_color=BORDER_HOVER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 10), dropdown_font=(font_family, 10)).pack(
                side='left')
        ctk.CTkLabel(
            res_group, text=T('filename_mode_desc'), text_color=TEXT_DIM,
            font=(font_family, 8), wraplength=286,
            justify='right').pack(anchor='e', pady=(3, 0))

        workers_row = ctk.CTkFrame(res_group, fg_color='transparent')
        workers_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(
            workers_row, text=T('max_workers_per_video_setting'),
            text_color=TEXT_SEC, font=(font_family, 10, 'bold'),
            width=108, anchor='e').pack(side='left', padx=(0, 8))
        self._workers_var = tk.StringVar(
            value=str(config.get_max_workers_per_video()))
        self._workers_entry = ctk.CTkEntry(
            workers_row, textvariable=self._workers_var,
            width=72, height=34, corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, justify='center')
        self._workers_entry.pack(side='left')
        self._workers_entry.bind('<Return>', self._on_workers_change)
        self._workers_entry.bind('<FocusOut>', self._on_workers_change)
        ctk.CTkLabel(
            workers_row, text=T('max_n', n=config.MAX_WORKERS_PER_VIDEO),
            text_color=TEXT_DIM, font=(font_family, 9)).pack(
                side='left', padx=(8, 0))
        ctk.CTkLabel(
            res_group,
            text=T(
                'max_workers_per_video_desc',
                n=config.MAX_WORKERS_PER_VIDEO),
            text_color=TEXT_DIM, font=(font_family, 8),
            wraplength=286, justify='right').pack(
                anchor='e', pady=(3, 0))

        version_row = ctk.CTkFrame(res_group, fg_color='transparent')
        version_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(
            version_row, text=T('st_version_preference'), text_color=TEXT_SEC,
            font=(font_family, 10, 'bold'), width=108, anchor='e').pack(
                side='left', padx=(0, 8))
        self._version_var = tk.StringVar(value=self._version_label())
        ctk.CTkOptionMenu(
            version_row, variable=self._version_var,
            values=self._version_values(),
            command=self._on_version_change, width=178, height=34,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            button_color=BORDER_HOVER, button_hover_color=ACCENT,
            text_color=TEXT_PRI, dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 10), dropdown_font=(font_family, 10)).pack(
                side='left')

        subtitle_row = ctk.CTkFrame(res_group, fg_color='transparent')
        subtitle_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(
            subtitle_row, text=T('subtitle_setting'), text_color=TEXT_SEC,
            font=(font_family, 10, 'bold'), width=108, anchor='e').pack(
                side='left', padx=(0, 8))
        self._subtitle_var = tk.StringVar(value=self._subtitle_label())
        ctk.CTkOptionMenu(
            subtitle_row, variable=self._subtitle_var,
            values=self._subtitle_values(), command=self._on_subtitle_change,
            width=178, height=34, corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD, dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 9), dropdown_font=(font_family, 9)).pack(
                side='left')

        recognition_row = ctk.CTkFrame(res_group, fg_color='transparent')
        recognition_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(
            recognition_row, text=T('recognition_quality_setting'),
            text_color=TEXT_SEC, font=(font_family, 10, 'bold'),
            width=108, anchor='e').pack(side='left', padx=(0, 8))
        self._recognition_quality_var = tk.StringVar(
            value=self._recognition_quality_label())
        ctk.CTkOptionMenu(
            recognition_row, variable=self._recognition_quality_var,
            values=self._recognition_quality_values(),
            command=self._on_recognition_quality_change,
            width=178, height=34, corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, button_color=BORDER_HOVER,
            button_hover_color=ACCENT, text_color=TEXT_PRI,
            dropdown_fg_color=BG_CARD,
            dropdown_hover_color=BG_CARD_HOVER,
            dropdown_text_color=TEXT_PRI,
            font=(font_family, 9), dropdown_font=(font_family, 9)).pack(
                side='left')
        ctk.CTkLabel(
            res_group, text=T('recognition_quality_desc'),
            text_color=TEXT_DIM, font=(font_family, 8),
            wraplength=286, justify='right').pack(
                anchor='e', pady=(3, 0))

        translation_row = ctk.CTkFrame(res_group, fg_color='transparent')
        translation_row.pack(fill='x', pady=(6, 0))
        ctk.CTkLabel(
            translation_row, text=T('translation_provider_setting'),
            text_color=TEXT_SEC, font=(font_family, 10, 'bold'),
            width=108, anchor='e').pack(side='left', padx=(0, 8))
        translation_controls = ctk.CTkFrame(
            translation_row, fg_color='transparent', width=178, height=34)
        translation_controls.pack(side='left')
        translation_controls.pack_propagate(False)
        self._translation_provider_status_lbl = ctk.CTkLabel(
            translation_controls,
            text=translation_provider_summary(short=True),
            text_color=TEXT_SEC, font=(font_family, 9),
            anchor='w')
        self._translation_provider_status_lbl.pack(
            side='left', fill='x', expand=True)
        ctk.CTkButton(
            translation_controls, text=T('translation_provider_configure'),
            width=64, height=32, corner_radius=CONTROL_RADIUS,
            fg_color='transparent', border_width=1,
            border_color=BORDER_HOVER, hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(font_family, 9, 'bold'),
            command=self._open_translation_settings).pack(side='right')
        ctk.CTkLabel(
            res_group, text=T('translation_provider_hint'),
            text_color=TEXT_DIM, font=(font_family, 8),
            wraplength=286, justify='right').pack(
                anchor='e', pady=(3, 0))
        # Apply saved preference immediately (before auto-start)
        from uav_downloader.sites.base import set_resolution_pref
        set_resolution_pref(self._cfg.get('resolution', 'highest'))
        self._settings_widgets = [settings_scroll]
        for widget in self._settings_widgets:
            widget.grid_remove()
        self._settings_expanded = False
        self._categories_before_settings = self._categories_collapsed
        self._activity_before_settings = self._activity_visible

        # ── Site / Category selection ───────────────────────────────
        selection = ctk.CTkFrame(
            main, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        selection.grid(row=1, column=0, sticky='nsew',
                       pady=(12, 10))
        self._selection_panel = selection

        selection_header = ctk.CTkFrame(selection, fg_color='transparent')
        selection_header.pack(fill='x', padx=16, pady=(14, 8))
        ctk.CTkLabel(
            selection_header, text=T('st_select_hint'), text_color=TEXT_PRI,
            font=(font_family, 13, 'bold')).pack(side='left')
        self._selected_count_lbl = ctk.CTkLabel(
            selection_header, text=f'0 {T("selected")}', text_color=TEXT_DIM,
            font=(font_family, 10, 'bold'))
        self._selected_count_lbl.pack(side='left', padx=(10, 0))

        self._categories_toggle_btn = ctk.CTkButton(
            selection_header, text=T('st_categories_collapse'),
            width=104, height=32, corner_radius=CONTROL_RADIUS,
            fg_color='transparent', hover_color=BG_CARD_HOVER,
            border_width=1, border_color=BORDER,
            text_color=TEXT_SEC, font=(font_family, 9, 'bold'),
            command=self._toggle_categories)
        self._categories_toggle_btn.pack(side='right')

        self._category_filter_box = ctk.CTkFrame(
            selection_header, fg_color='transparent')
        self._category_filter_box.pack(side='right', padx=(0, 8))
        self._category_filter_var = tk.StringVar()
        filter_entry = ctk.CTkEntry(
            self._category_filter_box, textvariable=self._category_filter_var,
            placeholder_text=T('st_filter_categories'),
            width=200, height=32,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, font=(font_family, 10))
        filter_entry.pack(side='left')
        self._category_filter_var.trace_add('write', self._schedule_category_filter)
        self._build_category_tabs(selection, font_family)

        # ── Control row ─────────────────────────────────────────────
        ctrl = ctk.CTkFrame(main, fg_color='transparent')
        ctrl.grid(row=2, column=0, sticky='ew', pady=(0, 10))

        self._start_btn = ctk.CTkButton(
            ctrl, text=T('st_start'), width=142, height=40,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=WHITE,
            font=(font_family, 11, 'bold'),
            command=self._start_worker)
        self._start_btn.pack(side='left')

        self._stop_btn = ctk.CTkButton(
            ctrl, text=T('st_stop'), width=92, height=40,
            corner_radius=CONTROL_RADIUS, fg_color=ERROR_DIM,
            hover_color=BG_CARD_HOVER, text_color=ERROR_C,
            border_width=1, border_color=BORDER,
            font=(font_family, 11),
            command=self._stop_worker,
            state='disabled')
        self._stop_btn.pack(side='left', padx=(8, 0))

        self._check_now_btn = ctk.CTkButton(
            ctrl, text=T('st_check_now'), width=112, height=40,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
            font=(font_family, 11),
            command=self._check_now)
        self._check_now_btn.pack(side='left', padx=(8, 0))

        self._schedule_btn = ctk.CTkButton(
            ctrl, text=T('st_schedule'), width=104, height=40,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
            font=(font_family, 10, 'bold'),
            command=self._open_schedule)
        self._schedule_btn.pack(side='left', padx=(8, 0))

        self._activity_toggle_btn = ctk.CTkButton(
            ctrl, text=T('st_activity_show'), width=108, height=40,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER_HOVER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(font_family, 10),
            command=self._toggle_activity)
        self._activity_toggle_btn.pack(side='left', padx=(8, 0))

        status_box = ctk.CTkFrame(
            ctrl, fg_color=BG_BADGE, corner_radius=CONTROL_RADIUS,
            border_width=1, border_color=BORDER)
        status_box.pack(side='right')
        self._status_lbl = ctk.CTkLabel(
            status_box, text=T(self._status_key), text_color=self._status_fg,
            font=(font_family, 10, 'bold'))
        self._status_lbl.pack(padx=12, pady=6)

        # ── Download progress bar ───────────────────────────────────
        prog_outer = ctk.CTkFrame(
            main, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        prog_outer.grid(row=3, column=0, sticky='ew', pady=(0, 10))
        self._progress_panel = prog_outer

        self._prog_title = ctk.CTkLabel(
            prog_outer, text=T('st_progress_idle'), text_color=TEXT_SEC,
            font=(font_family, 10, 'bold'), anchor='w')
        self._prog_title.pack(fill='x', padx=14, pady=(10, 4))

        bar_row = ctk.CTkFrame(prog_outer, fg_color='transparent')
        bar_row.pack(fill='x', padx=14, pady=(0, 10))

        self._prog_bar = ctk.CTkProgressBar(
            bar_row, height=8, corner_radius=4,
            fg_color=BG_INPUT, progress_color=ACCENT)
        self._prog_bar.set(0)
        self._prog_bar.pack(side='left', fill='x', expand=True, pady=7)
        self._progress_display_mode = 'idle'

        self._prog_pct = ctk.CTkLabel(
            bar_row, text='', text_color=ACCENT,
            font=('Consolas', 10, 'bold'), width=48, anchor='e')
        self._prog_pct.pack(side='left', padx=(8, 0))

        self._prog_info = ctk.CTkLabel(
            bar_row, text='', text_color=TEXT_SEC,
            font=('Consolas', 9), anchor='e')
        self._prog_info.pack(side='right', padx=(8, 0))

        # ── Log box ─────────────────────────────────────────────────
        activity = ctk.CTkFrame(
            main, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        activity.grid(row=4, column=0, sticky='nsew')
        self._activity_panel = activity
        activity_header = ctk.CTkFrame(activity, fg_color='transparent')
        activity_header.pack(fill='x', padx=14, pady=(9, 5))
        ctk.CTkLabel(
            activity_header, text=T('st_activity'), text_color=TEXT_PRI,
            font=(font_family, 11, 'bold')).pack(side='left')
        self._schedule_summary_lbl = ctk.CTkLabel(
            activity_header, text=self._schedule_summary_text(),
            text_color=TEXT_DIM, font=(font_family, 9))
        self._schedule_summary_lbl.pack(side='right')

        self._log_box = ctk.CTkTextbox(
            activity, fg_color=BG_INPUT, text_color=TEXT_SEC,
            border_width=0, corner_radius=6,
            font=('Consolas', 10), wrap='word', state='disabled')
        self._log_box.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        prog_outer.grid_remove()
        activity.grid_remove()
        self._progress_visible = False
        self._activity_visible = False

    def _schedule_summary_text(self) -> str:
        schedule = _normalize_scan_schedule(
            self._cfg.get('scan_schedule'))
        if schedule['mode'] == 'daily':
            return T(
                'st_schedule_summary_daily',
                time=schedule['daily_time'],
            )
        return T(
            'st_schedule_summary_interval',
            hours=schedule['interval_hours'],
        )

    def _refresh_schedule_summary(self):
        if hasattr(self, '_schedule_summary_lbl'):
            try:
                self._schedule_summary_lbl.configure(
                    text=self._schedule_summary_text())
            except tk.TclError:
                pass

    def _toggle_settings(self):
        self._set_settings_expanded(not self._settings_expanded)

    def _set_settings_expanded(self, expanded: bool):
        if not hasattr(self, '_settings_widgets'):
            return
        expanded = bool(expanded)
        was_expanded = self._settings_expanded
        if expanded and not was_expanded:
            self._categories_before_settings = self._categories_collapsed
            self._activity_before_settings = self._activity_visible
            self._settings_expanded = True
            if hasattr(self, '_selection_panel'):
                try:
                    self._selection_panel.grid_remove()
                    self._main_frame.grid_rowconfigure(
                        1, weight=0, minsize=0)
                except (tk.TclError, AttributeError):
                    pass
            self._set_activity_visible(False)
        else:
            self._settings_expanded = expanded
        for widget in self._settings_widgets:
            try:
                if self._settings_expanded:
                    widget.grid()
                else:
                    widget.grid_remove()
            except (tk.TclError, AttributeError):
                pass
        if hasattr(self, '_settings_toggle_btn'):
            self._settings_toggle_btn.configure(
                text=T(
                    'st_settings_collapse' if self._settings_expanded
                    else 'st_settings_expand'))
        if not expanded and was_expanded:
            if hasattr(self, '_selection_panel'):
                try:
                    self._selection_panel.grid()
                except (tk.TclError, AttributeError):
                    pass
            self._set_categories_collapsed(
                getattr(self, '_categories_before_settings', False))
            self._set_activity_visible(
                getattr(self, '_activity_before_settings', False))

    def _toggle_activity(self):
        if self._settings_expanded:
            self._set_settings_expanded(False)
            if not self._activity_visible:
                self._set_activity_visible(True)
            return
        self._set_activity_visible(not self._activity_visible)

    def _set_activity_visible(self, visible: bool):
        if not hasattr(self, '_activity_panel'):
            return
        self._activity_visible = bool(visible)
        try:
            if self._activity_visible:
                self._activity_panel.grid()
                self._main_frame.grid_rowconfigure(4, weight=1)
            else:
                self._activity_panel.grid_remove()
                self._main_frame.grid_rowconfigure(4, weight=0)
        except tk.TclError:
            return
        if hasattr(self, '_activity_toggle_btn'):
            self._activity_toggle_btn.configure(
                text=T(
                    'st_activity_hide' if self._activity_visible
                    else 'st_activity_show'))

    def _set_progress_visible(self, visible: bool):
        if not hasattr(self, '_progress_panel'):
            return
        visible = bool(visible)
        if visible == self._progress_visible:
            return
        self._progress_visible = visible
        try:
            if visible:
                self._progress_panel.grid()
            else:
                self._progress_panel.grid_remove()
        except tk.TclError:
            pass

    @staticmethod
    def _timezone_display() -> tuple[str, str]:
        local = datetime.now().astimezone()
        zone = local.tzname() or 'Local'
        offset = local.utcoffset() or timedelta(0)
        total_minutes = int(offset.total_seconds() // 60)
        sign = '+' if total_minutes >= 0 else '-'
        total_minutes = abs(total_minutes)
        return zone, f'{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}'

    def _open_schedule(self):
        existing = self._schedule_popup
        try:
            if existing is not None and existing.winfo_exists():
                existing.focus_force()
                return
        except tk.TclError:
            pass

        schedule = _normalize_scan_schedule(
            self._cfg.get('scan_schedule'))
        popup = ctk.CTkToplevel(self)
        self._schedule_popup = popup
        popup.title(T('st_schedule_title'))
        popup.geometry('460x340')
        popup.resizable(False, False)
        popup.transient(self)
        popup.configure(fg_color=BG_DARK)
        popup.grid_columnconfigure(0, weight=1)

        body = ctk.CTkFrame(
            popup, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        body.grid(row=0, column=0, padx=18, pady=(18, 10), sticky='nsew')
        body.grid_columnconfigure(1, weight=1)

        mode_var = tk.StringVar(value=schedule['mode'])
        hours_var = tk.StringVar(value=str(schedule['interval_hours']))
        time_var = tk.StringVar(value=schedule['daily_time'])

        ctk.CTkRadioButton(
            body, text=T('st_schedule_interval'), variable=mode_var,
            value='interval', fg_color=ACCENT, hover_color=ACCENT_HOVER,
            border_color=BORDER_HOVER, text_color=TEXT_PRI,
            font=(ui_font(), 11, 'bold')).grid(
                row=0, column=0, padx=(18, 10), pady=(20, 10), sticky='w')
        interval_controls = ctk.CTkFrame(body, fg_color='transparent')
        interval_controls.grid(
            row=0, column=1, padx=(0, 18), pady=(20, 10), sticky='w')
        ctk.CTkEntry(
            interval_controls, textvariable=hours_var, width=76, height=36,
            corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
            border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, font=('Consolas', 11)).pack(side='left')
        ctk.CTkLabel(
            interval_controls, text=T('st_schedule_hours'),
            text_color=TEXT_SEC, font=(ui_font(), 10)).pack(
                side='left', padx=(8, 0))

        ctk.CTkRadioButton(
            body, text=T('st_schedule_daily'), variable=mode_var,
            value='daily', fg_color=ACCENT, hover_color=ACCENT_HOVER,
            border_color=BORDER_HOVER, text_color=TEXT_PRI,
            font=(ui_font(), 11, 'bold')).grid(
                row=1, column=0, padx=(18, 10), pady=10, sticky='w')
        ctk.CTkEntry(
            body, textvariable=time_var, width=96, height=36,
            placeholder_text='HH:MM', corner_radius=CONTROL_RADIUS,
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, font=('Consolas', 11)).grid(
                row=1, column=1, padx=(0, 18), pady=10, sticky='w')

        zone, offset = self._timezone_display()
        ctk.CTkLabel(
            body, text=T(
                'st_schedule_local_time', zone=zone, offset=offset),
            text_color=TEXT_SEC, font=(ui_font(), 10),
            anchor='w').grid(
                row=2, column=0, columnspan=2,
                padx=18, pady=(12, 4), sticky='ew')
        ctk.CTkLabel(
            body, text=T('st_schedule_hint'), text_color=TEXT_DIM,
            font=(ui_font(), 9), justify='left', wraplength=390,
            anchor='w').grid(
                row=3, column=0, columnspan=2,
                padx=18, pady=(4, 18), sticky='ew')

        actions = ctk.CTkFrame(popup, fg_color='transparent')
        actions.grid(row=1, column=0, padx=18, pady=(0, 18), sticky='ew')

        def close_popup():
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            try:
                popup.destroy()
            except tk.TclError:
                pass
            self._schedule_popup = None

        def save_schedule():
            try:
                hours = int(hours_var.get().strip())
            except ValueError:
                hours = 0
            if not MIN_SCAN_INTERVAL_HOURS <= hours <= MAX_SCAN_INTERVAL_HOURS:
                messagebox.showwarning(
                    T('st_schedule_title'),
                    T('st_schedule_invalid_hours'),
                    parent=popup)
                return
            daily_time = time_var.get().strip()
            match = re.fullmatch(r'(\d{2}):(\d{2})', daily_time)
            if (not match or int(match.group(1)) > 23 or
                    int(match.group(2)) > 59):
                messagebox.showwarning(
                    T('st_schedule_title'),
                    T('st_schedule_invalid_time'),
                    parent=popup)
                return
            normalized = _normalize_scan_schedule({
                'mode': mode_var.get(),
                'interval_hours': hours,
                'daily_time': daily_time,
            })
            self._cfg['scan_schedule'] = normalized
            update_config({'scan_schedule': normalized})
            self._worker.notify_schedule_changed()
            self._refresh_schedule_summary()
            self._log(T('st_schedule_saved'))
            close_popup()

        ctk.CTkButton(
            actions, text=T('st_schedule_save'), height=40,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=WHITE,
            font=(ui_font(), 10, 'bold'),
            command=save_schedule).pack(side='left', expand=True, fill='x')
        ctk.CTkButton(
            actions, text=T('st_calendar_cancel'), height=40,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(ui_font(), 10), command=close_popup).pack(
                side='left', expand=True, fill='x', padx=(8, 0))

        popup.protocol('WM_DELETE_WINDOW', close_popup)
        popup.update_idletasks()
        x = self.winfo_rootx() + max(
            0, (self.winfo_width() - popup.winfo_width()) // 2)
        y = self.winfo_rooty() + max(
            0, (self.winfo_height() - popup.winfo_height()) // 3)
        popup.geometry(f'+{x}+{y}')
        popup.after(40, popup.grab_set)

    # ── Selection helpers ────────────────────────────────────────────
    def _build_category_tabs(self, parent, font_family):
        """Keep all selection variables, but construct only visited tab widgets."""
        self._category_font = font_family
        self._category_tab_names = {}
        self._category_site_views = {}
        self._category_build_after_id = None
        self._category_filter_after_id = None
        self._category_render_epoch = 0
        tabview = ctk.CTkTabview(
            parent, fg_color='transparent', corner_radius=0,
            segmented_button_fg_color=BG_INPUT,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=BG_INPUT,
            segmented_button_unselected_hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, border_width=0,
            command=self._on_category_tab_changed)
        tabview.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self._category_tabview = tabview
        for site_name, site_info in SITES.items():
            tab_name = f'{site_name}  {len(list(iter_targets(site_name)))}'
            self._category_tab_names[site_name] = tab_name
            tabview.add(tab_name)
            tabview.tab(tab_name).configure(fg_color='transparent')
            for group in site_info['groups']:
                self._check_vars[f'{site_name}|__group__|{group["id"]}'] = (
                    tk.BooleanVar(master=self, value=False))
                for target in group['targets']:
                    self._check_vars[selection_key(site_name, target['id'])] = (
                        tk.BooleanVar(master=self, value=False))
        self._on_category_tab_changed()

    def _active_category_site(self):
        names = self.__dict__.get('_category_tab_names', {})
        if not names:
            return None
        selected = self._category_tabview.get()
        return next((site for site, label in names.items() if label == selected), None)

    def _cancel_category_work(self):
        self._category_render_epoch = self.__dict__.get('_category_render_epoch', 0) + 1
        for name in ('_category_build_after_id', '_category_filter_after_id'):
            handle = self.__dict__.get(name)
            if handle is not None:
                try:
                    self.after_cancel(handle)
                except (tk.TclError, RuntimeError):
                    pass
            setattr(self, name, None)

    def _on_category_tab_changed(self):
        self._cancel_category_work()
        if self._is_closing:
            return
        site = self._active_category_site()
        if site is None:
            return
        if site not in self._category_site_views:
            self._category_site_views[site] = {
                'iterator': self._iter_category_widgets(site), 'complete': False}
        self._filter_targets()
        if not self._category_site_views[site]['complete']:
            gen, epoch = self._build_gen, self._category_render_epoch
            self._category_build_after_id = self.after(
                16, lambda: self._run_category_build_batch(site, gen, epoch))

    def _run_category_build_batch(self, site, gen, epoch):
        if (self._is_closing or gen != self._build_gen or
                epoch != self._category_render_epoch or site != self._active_category_site()):
            return
        self._category_build_after_id = None
        view = self._category_site_views[site]
        deadline = time.monotonic() + 0.008
        for _ in range(8):
            try:
                next(view['iterator'])
            except StopIteration:
                view['complete'] = True
                break
            if time.monotonic() >= deadline:
                break
        if not view['complete']:
            # Give Tk's geometry/paint idle tasks a turn between batches.
            self._category_build_after_id = self.after(
                16, lambda: self._run_category_build_batch(site, gen, epoch))

    def _iter_category_widgets(self, site_name):
        inner = ctk.CTkScrollableFrame(
            self._category_tabview.tab(self._category_tab_names[site_name]),
            fg_color='transparent', corner_radius=0,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=BORDER_HOVER)
        inner.pack(fill='both', expand=True)
        for group in SITES[site_name]['groups']:
            group_frame = ctk.CTkFrame(
                inner, fg_color=BG_SECTION, corner_radius=8,
                border_width=1, border_color=BORDER_CARD)
            group_header = ctk.CTkFrame(group_frame, fg_color='transparent')
            group_header.pack(fill='x', padx=12, pady=(9, 5))
            ctk.CTkLabel(
                group_header, text=f'{group_label(group)}  {len(group["targets"])}',
                text_color=TEXT_PRI, font=(self._category_font, 11, 'bold')).pack(side='left')
            ctk.CTkCheckBox(
                group_header, text=T('st_select_all'),
                variable=self._check_vars[f'{site_name}|__group__|{group["id"]}'],
                width=110, height=24, checkbox_width=18, checkbox_height=18,
                corner_radius=4, border_width=1, fg_color=ACCENT,
                hover_color=ACCENT_HOVER, border_color=BORDER_HOVER,
                checkmark_color=WHITE, text_color=ACCENT,
                font=(self._category_font, 10, 'bold'),
                command=lambda sn=site_name, gid=group['id']:
                    self._toggle_select_group(sn, gid)).pack(side='right')
            cat_grid = ctk.CTkFrame(group_frame, fg_color='transparent')
            cat_grid.pack(fill='x', padx=10, pady=(0, 10))
            for col in range(3):
                cat_grid.grid_columnconfigure(col, weight=1)
            group_widgets, filter_items = [], []
            self._category_groups.append(group_widgets)
            filter_group = {
                'site': site_name, 'frame': group_frame, 'items': filter_items,
                'visible': False, 'item_visibility': {}}
            self._filter_groups.append(filter_group)
            yield
            for index, target in enumerate(group['targets']):
                label = target_label(target)
                cb = ctk.CTkCheckBox(
                    cat_grid, text=label,
                    variable=self._check_vars[selection_key(site_name, target['id'])],
                    height=26, checkbox_width=17, checkbox_height=17,
                    corner_radius=4, border_width=1, fg_color=ACCENT,
                    hover_color=ACCENT_HOVER, border_color=BORDER_HOVER,
                    checkmark_color=WHITE, text_color=TEXT_SEC,
                    font=(self._category_font, 10),
                    command=lambda sn=site_name, gid=group['id']:
                        self._sync_group_select(sn, gid))
                row, col = divmod(index, self._category_columns)
                cb.grid(row=row, column=col, sticky='w', padx=6, pady=3)
                self._target_widgets.append((cb, label.casefold()))
                group_widgets.append(cb)
                filter_items.append((cb, label.casefold()))
                query = self._category_filter_var.get().strip().casefold()
                matches = not query or query in label.casefold()
                filter_group['item_visibility'][cb] = matches
                if matches:
                    self._set_category_group_visible(filter_group, True)
                else:
                    cb.grid_remove()
                yield

    def _schedule_category_filter(self, *_args):
        handle = self.__dict__.get('_category_filter_after_id')
        if handle is not None:
            try:
                self.after_cancel(handle)
            except tk.TclError:
                pass
        gen = self._build_gen
        def apply_filter():
            if self._is_closing or gen != self._build_gen:
                return
            self._category_filter_after_id = None
            self._filter_targets()
        self._category_filter_after_id = self.after(120, apply_filter)

    def _toggle_categories(self):
        if self._settings_expanded:
            self._set_settings_expanded(False)
            if self._categories_collapsed:
                self._set_categories_collapsed(False)
            return
        self._set_categories_collapsed(not self._categories_collapsed)

    def _set_categories_collapsed(self, collapsed: bool):
        if not all(hasattr(self, name) for name in (
                '_selection_panel', '_category_tabview',
                '_category_filter_box', '_categories_toggle_btn')):
            return
        self._categories_collapsed = bool(collapsed)
        if self._categories_collapsed:
            self._category_filter_box.pack_forget()
            self._category_tabview.pack_forget()
            if '_main_frame' in self.__dict__:
                self._main_frame.grid_rowconfigure(
                    1, weight=0, minsize=0)
                self._selection_panel.grid_configure(sticky='ew')
            else:
                self._selection_panel.pack_configure(
                    fill='x', expand=False)
            button_text = T('st_categories_expand')
        else:
            self._category_filter_box.pack(side='right', padx=(0, 8))
            self._category_tabview.pack(
                fill='both', expand=True, padx=10, pady=(0, 10))
            if '_main_frame' in self.__dict__:
                self._main_frame.grid_rowconfigure(
                    1, weight=1, minsize=0)
                self._selection_panel.grid_configure(sticky='nsew')
            else:
                self._selection_panel.pack_configure(
                    fill='both', expand=True)
            button_text = T('st_categories_collapse')
        self._categories_toggle_btn.configure(text=button_text)

    def _update_selected_count(self):
        if not hasattr(self, '_selected_count_lbl'):
            return
        count = sum(
            1 for key, var in self._check_vars.items()
            if '|__group__|' not in key and var.get())
        self._selected_count_lbl.configure(
            text=f'{count} {T("selected")}',
            text_color=ACCENT if count else TEXT_DIM)

    def _filter_targets(self, *_args):
        query = self._category_filter_var.get().strip().casefold()
        active_site = self._active_category_site()
        for group in self._filter_groups:
            if active_site and group.get('site', active_site) != active_site:
                continue
            group_matches = False
            item_visibility = group.setdefault('item_visibility', {})
            for widget, label in group['items']:
                try:
                    matches = not query or query in label
                    group_matches = group_matches or matches
                    if matches != item_visibility.get(widget, True):
                        if matches:
                            widget.grid()
                        else:
                            widget.grid_remove()
                    item_visibility[widget] = matches
                except tk.TclError:
                    pass
            self._set_category_group_visible(group, group_matches)

    def _set_category_group_visible(self, group, visible):
        """Change only this frame; never blank/repack the whole category tab."""
        if group.get('visible', True) == visible:
            return
        try:
            if not visible:
                group['frame'].pack_forget()
            else:
                options = {}
                found = False
                for following in self._filter_groups:
                    if following is group:
                        found = True
                    elif (found and following.get('site') == group.get('site')
                          and following.get('visible', True)):
                        options['before'] = following['frame']
                        break
                group['frame'].pack(fill='x', padx=4, pady=5, **options)
            group['visible'] = visible
        except tk.TclError:
            pass

    def _toggle_select_group(self, site_name: str, group_id: str):
        all_key = f'{site_name}|__group__|{group_id}'
        val = self._check_vars[all_key].get()
        group = next(g for g in SITES[site_name]['groups'] if g['id'] == group_id)
        for target in group['targets']:
            self._check_vars[selection_key(site_name, target['id'])].set(val)
        self._update_selected_count()

    def _sync_group_select(self, site_name: str, group_id: str):
        group = next(g for g in SITES[site_name]['groups'] if g['id'] == group_id)
        all_key = f'{site_name}|__group__|{group_id}'
        target_keys = [selection_key(site_name, target['id'])
                       for target in group['targets']]
        self._check_vars[all_key].set(
            bool(target_keys) and
            all(self._check_vars[key].get() for key in target_keys))
        self._update_selected_count()

    def _sync_select_all_vars(self):
        for site_name, site_info in SITES.items():
            for group in site_info['groups']:
                all_key = f'{site_name}|__group__|{group["id"]}'
                if all_key in self._check_vars:
                    self._sync_group_select(site_name, group['id'])
        self._update_selected_count()

    def _get_selected_targets(self) -> list[dict]:
        targets = []
        for site_name in SITES:
            for target in iter_targets(site_name):
                key = selection_key(site_name, target['id'])
                var = self._check_vars.get(key)
                if var is not None and var.get():
                    targets.append({
                        'site': site_name,
                        'id': target['id'],
                        'category': target['name'],
                    })
        return targets

    def _date_presets(self):
        return [
            (T('st_date_yesterday'), 'days', 1),
            (T('st_date_month_1'), 'months', 1),
            (T('st_date_month_2'), 'months', 2),
            (T('st_date_month_3'), 'months', 3),
            (T('st_date_month_6'), 'months', 6),
        ]

    def _on_date_preset(self, selected_label: str):
        today = datetime.now().astimezone().date()
        for label, kind, amount in self._date_presets():
            if label != selected_label:
                continue
            selected = (today - timedelta(days=amount)
                        if kind == 'days' else _months_before(today, amount))
            self._date_var.set(selected.isoformat())
            return

    def _open_calendar(self, date_var=None, owner=None):
        existing = getattr(self, '_calendar_popup', None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.focus_force()
                return
        except tk.TclError:
            pass

        today = datetime.now().astimezone().date()
        target_var = date_var or self._date_var
        popup_owner = owner or self
        try:
            selected = datetime.strptime(
                target_var.get().strip(), '%Y-%m-%d').date()
        except ValueError:
            selected = today

        popup = ctk.CTkToplevel(self)
        self._calendar_popup = popup
        popup.title(T('st_calendar_title'))
        popup.geometry('364x448')
        popup.resizable(False, False)
        popup.transient(popup_owner)
        popup.configure(fg_color=BG_DARK)
        popup.grid_columnconfigure(0, weight=1)
        popup.grid_rowconfigure(1, weight=1)

        view_month = [selected.replace(day=1)]

        def close_popup():
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            try:
                popup.destroy()
            except tk.TclError:
                pass
            self._calendar_popup = None

        def choose_day(day_value: date):
            target_var.set(day_value.isoformat())
            close_popup()

        header = ctk.CTkFrame(popup, fg_color=BG_CARD, corner_radius=0)
        header.grid(row=0, column=0, sticky='ew')
        header.grid_columnconfigure(1, weight=1)
        month_label = ctk.CTkLabel(
            header, text='', text_color=TEXT_PRI,
            font=(ui_font(), 14, 'bold'))
        ctk.CTkButton(
            header, text='‹', width=44, height=40,
            fg_color='transparent', hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(ui_font(), 20, 'bold'),
            command=lambda: shift_month(-1)).grid(
                row=0, column=0, padx=(12, 4), pady=10)
        month_label.grid(row=0, column=1, padx=4, pady=10)
        ctk.CTkButton(
            header, text='›', width=44, height=40,
            fg_color='transparent', hover_color=BG_CARD_HOVER,
            text_color=TEXT_PRI, font=(ui_font(), 20, 'bold'),
            command=lambda: shift_month(1)).grid(
                row=0, column=2, padx=(4, 12), pady=10)

        grid = ctk.CTkFrame(
            popup, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
            border_width=1, border_color=BORDER_CARD)
        grid.grid(row=1, column=0, padx=14, pady=14, sticky='nsew')
        for column in range(7):
            grid.grid_columnconfigure(column, weight=1)

        def render_month():
            for child in grid.winfo_children():
                child.destroy()
            current = view_month[0]
            month_label.configure(text=f'{current.year} / {current.month:02d}')
            weekdays = T('st_weekdays').split('|')
            if len(weekdays) != 7:
                weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            for column, weekday in enumerate(weekdays):
                ctk.CTkLabel(
                    grid, text=weekday, text_color=TEXT_DIM,
                    font=(ui_font(), 9, 'bold')).grid(
                        row=0, column=column, padx=2, pady=(10, 6))

            weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
                current.year, current.month)
            for row, week in enumerate(weeks, start=1):
                for column, day_number in enumerate(week):
                    if day_number == 0:
                        ctk.CTkLabel(grid, text='', width=38, height=34).grid(
                            row=row, column=column, padx=2, pady=2)
                        continue
                    day_value = date(current.year, current.month, day_number)
                    is_selected = day_value == selected
                    is_today = day_value == today
                    ctk.CTkButton(
                        grid, text=str(day_number), width=38, height=34,
                        corner_radius=9,
                        fg_color=ACCENT if is_selected else 'transparent',
                        hover_color=ACCENT_HOVER,
                        text_color=WHITE if is_selected else TEXT_PRI,
                        border_width=1 if is_today and not is_selected else 0,
                        border_color=ACCENT,
                        font=(ui_font(), 10, 'bold' if is_selected else 'normal'),
                        command=lambda value=day_value: choose_day(value)).grid(
                            row=row, column=column, padx=2, pady=2)

        def shift_month(delta: int):
            current = view_month[0]
            month_index = current.year * 12 + current.month - 1 + delta
            year, month_zero = divmod(month_index, 12)
            view_month[0] = date(year, month_zero + 1, 1)
            render_month()

        footer = ctk.CTkFrame(popup, fg_color='transparent')
        footer.grid(row=2, column=0, padx=14, pady=(0, 14), sticky='ew')
        ctk.CTkButton(
            footer, text=T('st_date_today'), height=36,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=WHITE,
            font=(ui_font(), 10, 'bold'),
            command=lambda: choose_day(today)).pack(side='left', expand=True, fill='x')
        ctk.CTkButton(
            footer, text=T('st_calendar_cancel'), height=36,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(ui_font(), 10), command=close_popup).pack(
                side='left', expand=True, fill='x', padx=(8, 0))

        popup.protocol('WM_DELETE_WINDOW', close_popup)
        render_month()
        popup.update_idletasks()
        x = popup_owner.winfo_rootx() + max(
            0, (popup_owner.winfo_width() - popup.winfo_width()) // 2)
        y = popup_owner.winfo_rooty() + max(
            0, (popup_owner.winfo_height() - popup.winfo_height()) // 3)
        popup.geometry(f'+{x}+{y}')
        popup.after(40, popup.grab_set)

    def _refresh_site_settings_summary(self):
        label = getattr(self, '_site_settings_summary_lbl', None)
        if label is None:
            return
        count = len(_normalize_site_settings(self._cfg.get('site_settings')))
        try:
            label.configure(text=(
                T('st_site_settings_default') if count == 0 else
                T('st_site_settings_custom_count', count=count)
            ))
        except tk.TclError:
            pass

    def _open_site_settings(self):
        existing = getattr(self, '_site_settings_popup', None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.focus_force()
                return
        except tk.TclError:
            pass

        popup = ctk.CTkToplevel(self)
        self._site_settings_popup = popup
        popup.title(T('st_site_settings_title'))
        popup.geometry('820x640')
        popup.minsize(700, 500)
        popup.transient(self)
        popup.configure(fg_color=BG_DARK)
        popup.grid_columnconfigure(0, weight=1)
        popup.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(
            popup, fg_color=BG_CARD, corner_radius=0,
            border_width=0)
        header.grid(row=0, column=0, sticky='ew')
        ctk.CTkLabel(
            header, text=T('st_site_settings_title'), text_color=TEXT_PRI,
            font=(ui_font(), 17, 'bold')).pack(
                anchor='w', padx=22, pady=(18, 4))
        ctk.CTkLabel(
            header, text=T('st_site_settings_desc'), text_color=TEXT_DIM,
            font=(ui_font(), 10), justify='left', wraplength=760).pack(
                anchor='w', padx=22, pady=(0, 16))

        content = ctk.CTkScrollableFrame(
            popup, fg_color='transparent', corner_radius=0,
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=BORDER_HOVER)
        content.grid(row=1, column=0, sticky='nsew', padx=14, pady=14)

        default_folder = (
            self._folder_var.get().strip() or _default_output_folder())
        default_date = self._date_var.get().strip() or DEFAULT_BASELINE_DATE
        current = _normalize_site_settings(self._cfg.get('site_settings'))
        rows = {}

        for site_name in SITES:
            override = current.get(site_name, {})
            card = ctk.CTkFrame(
                content, fg_color=BG_CARD, corner_radius=CARD_RADIUS,
                border_width=1, border_color=BORDER_CARD)
            card.pack(fill='x', padx=4, pady=6)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                card, text=site_name, text_color=ACCENT,
                font=(ui_font(), 13, 'bold')).grid(
                    row=0, column=0, columnspan=4, padx=14,
                    pady=(12, 6), sticky='w')

            folder_default = tk.BooleanVar(
                value='output_folder' not in override)
            folder_var = tk.StringVar(
                value=override.get('output_folder', default_folder))
            folder_check = ctk.CTkCheckBox(
                card, text=T('st_site_use_default_folder'),
                variable=folder_default, width=176, height=30,
                checkbox_width=18, checkbox_height=18,
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                border_color=BORDER_HOVER, checkmark_color=WHITE,
                text_color=TEXT_SEC, font=(ui_font(), 9))
            folder_check.grid(
                row=1, column=0, padx=(14, 8), pady=5, sticky='w')
            folder_entry = ctk.CTkEntry(
                card, textvariable=folder_var, height=34,
                corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
                border_color=BORDER, border_width=1,
                text_color=TEXT_PRI, font=(ui_font(), 9))
            folder_entry.grid(
                row=1, column=1, columnspan=2, padx=8, pady=5,
                sticky='ew')
            folder_button = ctk.CTkButton(
                card, text=T('st_browse'), width=76, height=34,
                corner_radius=CONTROL_RADIUS, fg_color='transparent',
                border_width=1, border_color=BORDER_HOVER,
                hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                font=(ui_font(), 9))
            folder_button.grid(
                row=1, column=3, padx=(8, 14), pady=5)

            date_default = tk.BooleanVar(
                value='baseline_date' not in override)
            date_var = tk.StringVar(
                value=override.get('baseline_date', default_date))
            date_check = ctk.CTkCheckBox(
                card, text=T('st_site_use_default_date'),
                variable=date_default, width=176, height=30,
                checkbox_width=18, checkbox_height=18,
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                border_color=BORDER_HOVER, checkmark_color=WHITE,
                text_color=TEXT_SEC, font=(ui_font(), 9))
            date_check.grid(
                row=2, column=0, padx=(14, 8), pady=(5, 12), sticky='w')
            date_entry = ctk.CTkEntry(
                card, textvariable=date_var, width=150, height=34,
                corner_radius=CONTROL_RADIUS, fg_color=BG_INPUT,
                border_color=BORDER, border_width=1,
                text_color=TEXT_PRI, font=('Consolas', 10))
            date_entry.grid(
                row=2, column=1, padx=8, pady=(5, 12), sticky='w')
            date_button = ctk.CTkButton(
                card, text=T('st_calendar'), width=84, height=34,
                corner_radius=CONTROL_RADIUS, fg_color='transparent',
                border_width=1, border_color=BORDER_HOVER,
                hover_color=BG_CARD_HOVER, text_color=TEXT_PRI,
                font=(ui_font(), 9))
            date_button.grid(
                row=2, column=2, padx=8, pady=(5, 12), sticky='w')

            row = {
                'folder_default': folder_default,
                'folder': folder_var,
                'folder_entry': folder_entry,
                'folder_button': folder_button,
                'date_default': date_default,
                'date': date_var,
                'date_entry': date_entry,
                'date_button': date_button,
            }
            rows[site_name] = row

            def sync_row(name=site_name):
                values = rows[name]
                folder_inherits = values['folder_default'].get()
                date_inherits = values['date_default'].get()
                if folder_inherits:
                    values['folder'].set(default_folder)
                if date_inherits:
                    values['date'].set(default_date)
                values['folder_entry'].configure(
                    state='disabled' if folder_inherits else 'normal')
                values['folder_button'].configure(
                    state='disabled' if folder_inherits else 'normal')
                values['date_entry'].configure(
                    state='disabled' if date_inherits else 'normal')
                values['date_button'].configure(
                    state='disabled' if date_inherits else 'normal')

            def choose_folder(name=site_name):
                selected = filedialog.askdirectory(
                    parent=popup, title=T('st_choose_folder'),
                    initialdir=rows[name]['folder'].get() or default_folder)
                if selected:
                    rows[name]['folder_default'].set(False)
                    rows[name]['folder'].set(selected)
                    sync_row(name)

            def choose_date(name=site_name):
                rows[name]['date_default'].set(False)
                sync_row(name)
                self._open_calendar(rows[name]['date'], popup)

            folder_check.configure(command=sync_row)
            date_check.configure(command=sync_row)
            folder_button.configure(command=choose_folder)
            date_button.configure(command=choose_date)
            sync_row()

        footer = ctk.CTkFrame(popup, fg_color=BG_CARD, corner_radius=0)
        footer.grid(row=2, column=0, sticky='ew')
        status_label = ctk.CTkLabel(
            footer, text='', text_color=ERROR_C,
            font=(ui_font(), 9), anchor='w')
        status_label.pack(side='left', padx=18, fill='x', expand=True)

        def close_popup():
            try:
                popup.destroy()
            except tk.TclError:
                pass
            self._site_settings_popup = None

        def save_settings():
            saved = {}
            for site_name, values in rows.items():
                item = {}
                if not values['folder_default'].get():
                    folder = values['folder'].get().strip()
                    if not folder:
                        status_label.configure(
                            text=T('st_site_folder_required', site=site_name))
                        return
                    item['output_folder'] = folder
                if not values['date_default'].get():
                    baseline = values['date'].get().strip()
                    if not _valid_baseline_date(baseline):
                        status_label.configure(
                            text=T('st_site_date_invalid', site=site_name))
                        return
                    item['baseline_date'] = baseline
                if item:
                    saved[site_name] = item
            self._cfg['site_settings'] = saved
            update_config({
                'output_folder': default_folder,
                'baseline_date': default_date,
                'site_settings': saved,
            })
            self._refresh_site_settings_summary()
            close_popup()

        ctk.CTkButton(
            footer, text=T('st_site_settings_save'), width=116, height=38,
            corner_radius=CONTROL_RADIUS, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=WHITE,
            font=(ui_font(), 10, 'bold'), command=save_settings).pack(
                side='right', padx=(8, 18), pady=12)
        ctk.CTkButton(
            footer, text=T('st_calendar_cancel'), width=92, height=38,
            corner_radius=CONTROL_RADIUS, fg_color='transparent',
            border_width=1, border_color=BORDER,
            hover_color=BG_CARD_HOVER, text_color=TEXT_SEC,
            font=(ui_font(), 10), command=close_popup).pack(
                side='right', pady=12)

        popup.protocol('WM_DELETE_WINDOW', close_popup)
        popup.update_idletasks()
        x = self.winfo_rootx() + max(
            0, (self.winfo_width() - popup.winfo_width()) // 2)
        y = self.winfo_rooty() + max(
            0, (self.winfo_height() - popup.winfo_height()) // 3)
        popup.geometry(f'+{x}+{y}')

    def _restore_target_checks(self, targets):
        for saved in targets:
            site_name = saved.get('site')
            if site_name not in SITES:
                continue
            target = find_target(site_name, saved.get('id'), saved.get('category'))
            if not target:
                continue
            key = selection_key(site_name, target['id'])
            if key in self._check_vars:
                self._check_vars[key].set(True)

    def _validate_baseline_date(self) -> Optional[str]:
        date_str = self._date_var.get().strip()
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            messagebox.showwarning(T('st_bad_date'), T('st_bad_date_msg'))
            return None
        return date_str

    def _save_selections_to_config(self, date_str: Optional[str] = None) -> bool:
        if date_str is None:
            date_str = self._validate_baseline_date()
            if not date_str:
                return False
        self._cfg['selected_targets'] = self._get_selected_targets()
        self._cfg['baseline_date'] = date_str
        self._cfg['output_folder'] = (
            self._folder_var.get().strip() or _default_output_folder())
        self._cfg['site_settings'] = _normalize_site_settings(
            self._cfg.get('site_settings'))
        update_config({
            'selected_targets': self._cfg['selected_targets'],
            'baseline_date': date_str,
            'output_folder': self._cfg['output_folder'],
            'site_settings': self._cfg['site_settings'],
        })
        return True

    def _load_selections_from_config(self):
        self._restore_target_checks(self._cfg.get('selected_targets', []))

    # ── Handlers ─────────────────────────────────────────────────────
    def _pick_folder(self):
        d = filedialog.askdirectory(title=T('st_choose_folder'))
        if d:
            self._folder_var.set(d)
            self._cfg['output_folder'] = d
            update_config({'output_folder': d})
            self._log(T('st_folder_set', path=d))

    def _prepare_output_folder(self) -> Optional[str]:
        folder = self._folder_var.get().strip() or _default_output_folder()
        self._folder_var.set(folder)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                T('st_folder_error'),
                T('st_folder_error_msg', path=folder, error=str(exc)))
            return None
        self._cfg['output_folder'] = folder
        return folder

    def _prepare_site_output_folders(self, targets: list[dict]) -> bool:
        selected_sites = {
            target.get('site') for target in targets
            if target.get('site') in SITES
        }
        for site_name in selected_sites:
            folder = _resolve_site_settings(
                self._cfg, site_name)['output_folder']
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(
                    T('st_folder_error'),
                    T('st_folder_error_msg', path=folder, error=str(exc)))
                return False
        return True

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

    def _on_res_change(self, val):
        from uav_downloader.sites.base import set_resolution_pref
        pref = self._resolution_pref_from_label(val)
        set_resolution_pref(pref)
        self._cfg['resolution'] = pref
        update_config({'resolution': pref})

    def _on_filename_mode_change(self, val):
        mode = config.set_filename_mode(
            self._filename_mode_from_label(val))
        self._filename_mode_var.set(self._filename_mode_label(mode))

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

    def _on_version_change(self, val):
        pref = self._version_pref_from_label(val)
        changed = pref != _normalize_version_pref(self._cfg)
        self._cfg['version_preference'] = pref
        self._cfg.pop('missav_version_preference', None)
        patch = {'version_preference': pref}
        if changed:
            # Revisit older pages that contain versions skipped by the previous
            # preference. Successful download history still prevents duplicates.
            self._cfg['first_run_done'] = False
            patch['first_run_done'] = False
        update_config(
            patch,
            remove=('missav_version_preference',))

    def _on_subtitle_change(self, val):
        self._cfg['subtitle_mode'] = self._subtitle_pref_from_label(val)
        update_config({'subtitle_mode': self._cfg['subtitle_mode']})

    def _on_recognition_quality_change(self, val):
        quality = config.set_recognition_quality(
            self._recognition_quality_from_label(val))
        self._recognition_quality_var.set(
            self._recognition_quality_label(quality))

    def _auto_start_worker(self):
        if not self._is_closing:
            self._start_worker(auto_start=True)

    def _start_worker(self, auto_start=False):
        folder = self._prepare_output_folder()
        if not folder:
            return
        targets = self._get_selected_targets()
        if not targets:
            messagebox.showwarning(T('st_no_cat'), T('st_no_cat_msg'))
            return
        if not self._prepare_site_output_folders(targets):
            return

        date_str = self._validate_baseline_date()
        if not date_str:
            return

        self._cfg['output_folder'] = folder
        self._cfg['baseline_date'] = date_str
        if not self._save_selections_to_config(date_str):
            return

        sites_summary = ', '.join(set(t['site'] for t in targets))
        cat_names = []
        for saved in targets:
            target = find_target(saved['site'], saved.get('id'), saved.get('category'))
            cat_names.append(target_label(target) if target else saved.get('category', '?'))
        cats_summary = ', '.join(cat_names[:12])
        if len(cat_names) > 12:
            cats_summary += f' … (+{len(cat_names) - 12})'
        self._log(T('st_target_log', sites=sites_summary, categories=cats_summary))
        self._log(T('st_baseline_log', date=date_str))

        if not self._worker.start_monitoring():
            self._log(T('st_scan_running'))
            return
        self._start_btn.configure(state='disabled')
        self._stop_btn.configure(state='normal')
        self._check_now_btn.configure(state='normal')
        plan = _plan_next_scan(load_config())
        self._set_status_key(
            'st_scanning' if plan.due else 'st_waiting_schedule',
            ACCENT if plan.due else SUCCESS)
        self._log(T('st_started_msg'))

    def _stop_worker(self):
        if not self._worker.is_running():
            self._start_btn.configure(state='normal')
            self._stop_btn.configure(state='disabled')
            self._check_now_btn.configure(state='normal')
            self._set_status_key('st_stopped', TEXT_DIM)
            return
        self._worker.stop()
        self._worker.cancel_active_download()
        self._start_btn.configure(state='disabled')
        self._stop_btn.configure(state='disabled')
        self._check_now_btn.configure(state='disabled')
        self._set_status_key('st_stopping', TEXT_DIM)

    def _check_now(self):
        if self._worker.is_monitoring():
            result = self._worker.request_scan_now()
            if result == 'queued':
                self._log(T('st_scan_queued'))
            else:
                self._log(T('st_scan_running'))
            return
        if self._worker.is_running():
            self._log(T('st_scan_running'))
            return
        folder = self._prepare_output_folder()
        if not folder:
            return
        targets = self._get_selected_targets()
        if not targets:
            messagebox.showwarning(T('st_no_cat'), T('st_no_cat_msg'))
            return
        if not self._prepare_site_output_folders(targets):
            return
        date_str = self._validate_baseline_date()
        if not date_str:
            return
        self._cfg['output_folder'] = folder
        if not self._save_selections_to_config(date_str):
            return
        if not self._worker.start_once():
            self._log(T('st_scan_running'))
            return
        self._start_btn.configure(state='disabled')
        self._stop_btn.configure(state='normal')
        self._check_now_btn.configure(state='disabled')
        self._set_status_key('st_scanning', ACCENT)
        self._log(T('st_checking_now'))

    # ── Logging (thread-safe) ────────────────────────────────────────
    def _sync_worker_controls(self):
        if self._is_closing or not hasattr(self, '_check_now_btn'):
            return
        try:
            running = self._worker.is_running()
            monitoring = self._worker.is_monitoring()
            self._start_btn.configure(
                state='disabled' if running else 'normal')
            self._stop_btn.configure(
                state='normal' if running else 'disabled')
            self._check_now_btn.configure(
                state='normal' if (not running or monitoring)
                else 'disabled')
            if not running and self._status_key == 'st_stopping':
                self._set_status_key('st_stopped', TEXT_DIM)
        except (tk.TclError, RuntimeError):
            pass

    def _set_status_threadsafe(
            self, key: str, fg: str = TEXT_DIM,
            worker_generation: Optional[int] = None):
        def _apply():
            if (not self._is_closing and
                    (worker_generation is None or
                     worker_generation == self._worker.run_generation)):
                self._set_status_key(key, fg)
        try:
            self.after(0, _apply)
        except (tk.TclError, RuntimeError):
            pass

    def _enqueue_log(self, msg: str):
        ts = datetime.now().strftime('%H:%M:%S')
        line = f'[{ts}] {msg}'
        with self._log_lock:
            self._log_queue.append(line)

    def _log(self, msg: str):
        self._enqueue_log(msg)

    def _schedule_log_flush(self):
        gen = self._build_gen
        self.after(300, lambda gen=gen: self._flush_log_queue(gen))

    def _flush_log_queue(self, gen: int):
        if self._is_closing:
            return
        if (self._rebuilding or gen != self._build_gen
                or getattr(self, '_log_box', None) is None):
            try:
                self.after(300, lambda gen=self._build_gen: self._flush_log_queue(gen))
            except tk.TclError:
                pass
            return
        with self._log_lock:
            pending = self._log_queue[:]
            self._log_queue.clear()
        if pending:
            try:
                self._log_box.configure(state='normal')
                for line in pending:
                    self._log_box.insert('end', line + '\n')
                self._log_box.see('end')
                self._log_box.configure(state='disabled')
            except (tk.TclError, AttributeError):
                if not self._is_closing:
                    try:
                        self.after(300, lambda gen=self._build_gen: self._flush_log_queue(gen))
                    except tk.TclError:
                        pass
                return
        try:
            self.after(300, lambda gen=self._build_gen: self._flush_log_queue(gen))
        except tk.TclError:
            pass

    def _schedule_progress_refresh(self):
        gen = self._build_gen
        self.after(500, lambda gen=gen: self._refresh_progress(gen))

    def _set_progress_display_mode(self, mode: str):
        if mode == self._progress_display_mode:
            return
        try:
            self._prog_bar.stop()
            if mode == 'scan':
                self._prog_bar.configure(mode='indeterminate')
                self._prog_bar.start()
            else:
                self._prog_bar.configure(mode='determinate')
        except (tk.TclError, AttributeError):
            return
        self._progress_display_mode = mode

    def _refresh_progress(self, gen: int):
        if self._is_closing:
            return
        if (self._rebuilding or gen != self._build_gen
                or getattr(self, '_prog_bar', None) is None
                or getattr(self, '_prog_pct', None) is None
                or getattr(self, '_prog_info', None) is None
                or getattr(self, '_prog_title', None) is None):
            try:
                self.after(500, lambda gen=self._build_gen: self._refresh_progress(gen))
            except tk.TclError:
                pass
            return
        prog = self._worker.get_progress()
        scan = self._worker.get_scan_state()
        try:
            self._sync_worker_controls()
            self._refresh_schedule_summary()
            if prog:
                self._set_progress_visible(True)
                done, total, speed, title = prog
                if total > 0:
                    self._set_progress_display_mode('download')
                    pct = int(done * 100 / total)
                    self._prog_bar.set(max(0.0, min(1.0, pct / 100)))
                    self._prog_pct.configure(text=f'{pct}%')
                    if speed < 0:
                        self._prog_info.configure(
                            text=T('subtitle_processing'))
                    else:
                        speed_str = (
                            f'{speed / 1024:.0f} KB/s'
                            if speed < 1024 * 1024
                            else f'{speed / 1024 / 1024:.1f} MB/s')
                        self._prog_info.configure(
                            text=f'{done}/{total} | {speed_str}')
                elif total < 0:
                    self._set_progress_display_mode('scan')
                    self._prog_pct.configure(text='')
                    self._prog_info.configure(text=T('subtitle_processing'))
                else:
                    # Preparing phase (0, 0, ...)
                    self._set_progress_display_mode('download')
                    self._prog_bar.set(0)
                    self._prog_pct.configure(text='')
                    self._prog_info.configure(text=T('st_preparing'))
                short = title[:50] + '...' if len(title) > 50 else title
                self._prog_title.configure(text=f'↓ {short}', text_color=TEXT_PRI)
            elif scan:
                self._set_progress_visible(True)
                self._set_progress_display_mode('scan')
                site, category, page, eligible_count = scan
                self._prog_pct.configure(text='')
                self._prog_info.configure(
                    text=T('st_candidates_found', count=eligible_count))
                self._prog_title.configure(
                    text=T('st_scan_progress', site=site,
                           category=category, page=page),
                    text_color=ACCENT)
            else:
                self._set_progress_visible(False)
                self._set_progress_display_mode('idle')
                self._prog_bar.set(0)
                self._prog_pct.configure(text='')
                self._prog_info.configure(text='')
                self._prog_title.configure(text=T('st_progress_idle'), text_color=TEXT_SEC)
        except (tk.TclError, AttributeError, RuntimeError):
            if not self._is_closing:
                try:
                    self.after(500, lambda gen=self._build_gen: self._refresh_progress(gen))
                except (tk.TclError, RuntimeError):
                    pass
            return
        try:
            self.after(500, lambda gen=self._build_gen: self._refresh_progress(gen))
        except (tk.TclError, RuntimeError):
            pass

    def _on_close(self):
        if self._is_closing:
            return
        self._is_closing = True
        self._build_gen += 1
        self._cancel_category_work()
        for popup_name in (
                '_schedule_popup', '_site_settings_popup',
                '_calendar_popup'):
            popup = getattr(self, popup_name, None)
            try:
                if popup is not None and popup.winfo_exists():
                    popup.destroy()
            except tk.TclError:
                pass
        self._worker.stop(StopReason.SHUTDOWN, cleanup=False)
        try:
            patch = {
                'selected_targets': self._get_selected_targets(),
                'output_folder': (
                    self._folder_var.get().strip()
                    if hasattr(self, '_folder_var')
                    else self._cfg.get('output_folder')),
                'resolution': self._cfg.get('resolution', 'highest'),
                'version_preference': self._cfg.get(
                    'version_preference', DEFAULT_VERSION_PREFERENCE),
                'subtitle_mode': normalize_subtitle_mode(
                    self._cfg.get('subtitle_mode')),
                'scan_schedule': _normalize_scan_schedule(
                    self._cfg.get('scan_schedule')),
                'site_settings': _normalize_site_settings(
                    self._cfg.get('site_settings')),
            }
            if hasattr(self, '_date_var'):
                candidate = self._date_var.get().strip()
                try:
                    datetime.strptime(candidate, '%Y-%m-%d')
                except ValueError:
                    pass
                else:
                    patch['baseline_date'] = candidate
            self._close_patch = patch
        except Exception as error:
            # Widget reads happen only on Tk's thread; do not silently discard
            # a malformed final draft or run Tk access in the background.
            self._close_capture_error = error
            self._close_patch = None
        attempt = SmallToolApp._start_close_work(self)
        SmallToolApp._poll_close_work(self, attempt.future, time.monotonic()+10)

    def _start_close_work(self):
        def stop_and_save(deadline):
            self._worker.cancel_active_download()
            require_stopped(self._worker.wait_until_stopped, deadline)
            if getattr(self, '_close_capture_error', None) is not None:
                raise self._close_capture_error
            update_config(self._close_patch, remove=('missav_version_preference',))
            return True
        self._close_attempt = ShutdownAttempt(stop_and_save)
        return self._close_attempt

    def _poll_close_work(self, future, deadline):
        error = None
        if future.done():
            try:
                future.result()
            except Exception as failure:
                error = 'Background work is still stopping' if isinstance(failure, StopTimeoutError) else str(failure)
            else:
                self.destroy()
                return
        elif time.monotonic() >= deadline:
            error = 'Waiting for background work or saving'
        if error is not None:
            if messagebox.askretrycancel('Close pending / 正在关闭', error+'\n\n'+'Retry waits again; Cancel exits without waiting. / 重试继续等待，取消直接退出。'):
                if future.done():
                    future = SmallToolApp._start_close_work(self).future
                deadline = time.monotonic()+10
            else:
                self.destroy()  # Explicit exit without waiting; no fake stop acknowledgement.
                return
        self.after(100, lambda: SmallToolApp._poll_close_work(self, future, deadline))

def main():
    app = SmallToolApp()
    app.mainloop()


if __name__ == '__main__':
    main()
