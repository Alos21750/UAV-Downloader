"""Validated desktop preference transactions, independent of GUI frameworks.

Unlike legacy per-field setters, a failed write is reported to the caller and
runtime settings are changed only after the entire preference file is saved.
Run these operations on the desktop command worker, never the render thread.
"""
import math
import os
import re

from uav_downloader.core import config
from uav_downloader.sites.base import set_resolution_pref, speed_limiter
from uav_downloader.subtitles import settings as translation


CHOICES = {
    'resolution': config.VALID_RESOLUTION_PREFS,
    'subtitle_mode': config.VALID_SUBTITLE_PREFS,
    'filename_mode': config.VALID_FILENAME_MODES,
    'recognition_quality': config.VALID_RECOGNITION_QUALITIES,
    'proxy_mode': config.VALID_PROXY_MODES,
    'theme': {'system', 'light', 'dark'},
    'lang': {'en', 'zh', 'zh-Hans', 'ja', 'th'},
}
LIMITS = {
    'download_concurrency': (config.MIN_DOWNLOAD_CONCURRENCY, config.MAX_DOWNLOAD_CONCURRENCY),
    'max_workers_per_video': (config.MIN_WORKERS_PER_VIDEO, config.MAX_WORKERS_PER_VIDEO),
    'slow_requeue_kbps': (config.MIN_SLOW_REQUEUE_KBPS, config.MAX_SLOW_REQUEUE_KBPS),
    'auto_retry_count': (0, config.MAX_AUTO_RETRY_COUNT),
}


def read_preferences():
    return dict(resolution=config.get_resolution_pref(),
                download_directory=config.get_download_directory() or 'download',
                subtitle_mode=config.get_subtitle_pref(),
                filename_mode=config.get_filename_mode(),
                filename_template=config.get_filename_template(),
                download_schedule=config.get_download_schedule(),
                minimum_free_gb=config.get_minimum_free_gb(),
                auto_retry_count=config.get_auto_retry_count(),
                notify_on_complete=config.get_notify_on_complete(),
                recognition_quality=config.get_recognition_quality(),
                download_concurrency=config.get_download_concurrency(),
                max_workers_per_video=config.get_max_workers_per_video(),
                slow_requeue_kbps=config.get_slow_requeue_kbps(),
                speed_limit_mbps=speed_limiter.limit_bps / (1024 * 1024),
                proxy_mode=config.get_proxy_mode(), proxy_url=config.get_proxy_url(),
                theme=config.get_theme(), lang=config.get_ui_lang() or 'en')


def save_preferences(values, manager):
    values = dict(values)
    unknown = values.keys() - (CHOICES.keys() | LIMITS.keys() | {
        'proxy_url', 'speed_limit_mbps', 'download_directory', 'filename_template',
        'download_schedule', 'minimum_free_gb', 'notify_on_complete'})
    if unknown:
        raise ValueError('Unknown preference')
    if 'filename_template' in values:
        raw = str(values['filename_template']).strip() or config.DEFAULT_FILENAME_TEMPLATE
        if config.normalize_filename_template(raw) != raw:
            raise ValueError('Invalid filename template')
        values['filename_template'] = raw
    if 'download_schedule' in values:
        raw = str(values['download_schedule']).strip()
        normalized = config.normalize_download_schedule(raw)
        if raw and not normalized:
            raise ValueError('Schedule must use HH:MM')
        values['download_schedule'] = normalized
    if 'minimum_free_gb' in values:
        value = float(values['minimum_free_gb'])
        if not math.isfinite(value) or not 0 <= value <= config.MAX_MINIMUM_FREE_GB:
            raise ValueError('Invalid minimum free space')
        values['minimum_free_gb'] = value
    if 'notify_on_complete' in values and not isinstance(values['notify_on_complete'], bool):
        raise ValueError('Notification setting must be boolean')
    if 'download_directory' in values:
        value = str(values['download_directory']).strip()
        if not value or '\x00' in value:
            raise ValueError('Invalid download directory')
        values['download_directory'] = os.path.abspath(os.path.expanduser(value))
    for field, choices in CHOICES.items():
        if field in values and values[field] not in choices:
            raise ValueError(f'Invalid {field}')
    for field, (minimum, maximum) in LIMITS.items():
        if field in values:
            raw = values[field]
            value = int(raw)
            if (isinstance(raw, bool) or
                    (isinstance(raw, float) and value != raw) or
                    not minimum <= value <= maximum):
                raise ValueError(f'{field} must be between {minimum} and {maximum}')
            values[field] = value
    speed = values.pop('speed_limit_mbps', None)
    if speed is not None:
        speed = float(speed)
        if not math.isfinite(speed) or not 0 <= speed <= 10000:
            raise ValueError('Speed limit must be between 0 and 10000 MiB/s')
    if 'proxy_url' in values:
        values['proxy_url'] = config.normalize_proxy_url(values['proxy_url'])
    with config._prefs_lock:
        prefs = config._load_prefs()
        prefs.update(values)
        if speed is not None:
            prefs['speed_limit_mbps'] = speed
        if prefs.get('proxy_mode') == 'manual' and not prefs.get('proxy_url'):
            raise ValueError('Manual proxy mode requires a proxy URL')
        config._save_prefs(prefs)
        # Keep caches in the same order as the persisted transaction.
        with config._proxy_lock:
            config._proxy_mode_cache = config._PROXY_UNSET
            config._proxy_url_cache = config._PROXY_UNSET
            config._system_proxy_cache = config._PROXY_UNSET
    if 'resolution' in values:
        set_resolution_pref(values['resolution'])
    if 'download_concurrency' in values:
        manager.max_concurrent = values['download_concurrency']
    if speed is not None:
        speed_limiter.set_limit(speed)
    return read_preferences()


def read_translation(provider=None):
    """Only non-secret profile fields cross into the GUI model."""
    profile = translation.get_translation_settings(provider)
    return dict(provider=profile.provider, base_url=profile.base_url,
                model=profile.model, key_source=profile.api_key_source)


def save_translation(values):
    values = dict(values)
    provider = values.pop('provider')
    if provider not in translation.PROVIDERS:
        raise ValueError('Unknown translation provider')
    translation.save_translation_profile(provider, **values)
    return read_translation(provider)


def read_cloudflare(host='jable.tv'):
    override = config.get_cf_override(host) or {}
    available = sorted({host, 'hanime1.me', *config.cf_override_hosts(),
                        *(domain for domains in config.MIRRORS.values() for domain in domains)})
    # Cookies are never filled back into the editable/display model.
    return dict(host=host, has_cookie=bool(override.get('cookie')), ua=override.get('ua', ''),
                hosts=config.cf_override_hosts(), available=available)


def save_cloudflare(values):
    values = dict(values)
    if values.keys() - {'host', 'cookie', 'ua', 'clear'}:
        raise ValueError('Unknown Cloudflare setting')
    host = str(values.get('host', '')).strip().lower().rstrip('.')
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}', host):
        raise ValueError('Enter a domain name without a URL or port')
    with config._cf_lock:
        snapshot = dict(config.CF_OVERRIDES)
        entry = dict(snapshot.get(host, {}))
        if values.get('clear'):
            entry = {}
        else:
            if values.get('cookie') is not None:
                cookie = config._parse_cf_clearance(values['cookie'])
                if cookie:
                    entry['cookie'] = cookie
                else:
                    entry.pop('cookie', None)
            if 'ua' in values:
                ua = str(values['ua']).strip()
                if any(ord(char) < 32 or ord(char) == 127 for char in ua):
                    raise ValueError('User-Agent must be a single line')
                if ua:
                    entry['ua'] = ua
                else:
                    entry.pop('ua', None)
        if entry:
            snapshot[host] = entry
        else:
            snapshot.pop(host, None)
        config._write_cf_snapshot(snapshot)
        config.CF_OVERRIDES = snapshot
    return read_cloudflare(host)
