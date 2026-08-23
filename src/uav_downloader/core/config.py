import json
import os
import re
import threading
from urllib.parse import urlsplit

from uav_downloader.core.paths import product_data_dir


headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
}

MIRRORS = {
    'missav': ['missav.ai', 'missav.ws', 'missav123.com', 'missav.live'],
    'jable':  ['jable.tv', 'fs1.app'],
    'supjav': ['supjav.com'],
}

_cf_lock = threading.Lock()
_prefs_lock = threading.Lock()
_proxy_lock = threading.Lock()
_PROXY_UNSET = object()
_proxy_url_cache = _PROXY_UNSET
_proxy_mode_cache = _PROXY_UNSET
_system_proxy_cache = _PROXY_UNSET
CF_OVERRIDES = {}
VALID_RESOLUTION_PREFS = {'highest', 'lowest', '1080', '720', '480', '360'}
VALID_SUBTITLE_PREFS = {'none', 'ja', 'en', 'zh', 'all'}
VALID_FILENAME_MODES = {'full-title', 'code-only'}
VALID_RECOGNITION_QUALITIES = {'auto', 'quality', 'balanced', 'fast'}
DEFAULT_DOWNLOAD_CONCURRENCY = 2
MIN_DOWNLOAD_CONCURRENCY = 1
MAX_DOWNLOAD_CONCURRENCY = 32
DEFAULT_MAX_WORKERS_PER_VIDEO = (
    min(os.cpu_count() * 2, 16) if os.cpu_count() else 8)
MIN_WORKERS_PER_VIDEO = 1
MAX_WORKERS_PER_VIDEO = 16
VALID_PROXY_SCHEMES = {
    'http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h',
}
VALID_PROXY_MODES = {'manual', 'system', 'direct'}


def _cf_store_path():
    return str(product_data_dir() / 'cf_overrides.json')


def _ui_prefs_path():
    return os.path.join(os.path.dirname(_cf_store_path()), 'ui_prefs.json')


def queue_csv_path():
    return os.path.join(os.path.dirname(_ui_prefs_path()), 'download_queue.csv')


def _load_prefs():
    try:
        with open(_ui_prefs_path(), 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return {'theme': raw}
    return {}


def _save_prefs(prefs):
    path = _ui_prefs_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def get_theme():
    mode = _load_prefs().get('theme')
    if isinstance(mode, str):
        mode = mode.strip().lower()
        if mode in {'system', 'light', 'dark'}:
            return mode
    return 'system'


def set_theme(mode):
    mode = (mode or '').strip().lower()
    if mode not in {'system', 'light', 'dark'}:
        mode = 'system'
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['theme'] = mode
            _save_prefs(prefs)
    except Exception:
        pass


def get_ui_lang():
    code = _load_prefs().get('lang')
    if isinstance(code, str):
        code = code.strip()
        if code in {'en', 'zh', 'zh-Hans', 'ja'}:
            return code
    return None


def set_ui_lang(code):
    code = (code or '').strip()
    if code not in {'en', 'zh', 'zh-Hans', 'ja'}:
        code = 'en'
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['lang'] = code
            _save_prefs(prefs)
    except Exception:
        pass


def get_resolution_pref():
    pref = _load_prefs().get('resolution')
    if isinstance(pref, str):
        pref = pref.strip().lower()
        if pref in VALID_RESOLUTION_PREFS:
            return pref
    return 'highest'


def set_resolution_pref(pref):
    pref = str(pref or '').strip().lower()
    if pref not in VALID_RESOLUTION_PREFS:
        pref = 'highest'
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['resolution'] = pref
            _save_prefs(prefs)
    except Exception:
        pass


def get_subtitle_pref():
    pref = _load_prefs().get('subtitle_mode')
    if isinstance(pref, str):
        pref = pref.strip().lower()
        if pref in VALID_SUBTITLE_PREFS:
            return pref
    return 'none'


def set_subtitle_pref(pref):
    pref = str(pref or '').strip().lower()
    if pref not in VALID_SUBTITLE_PREFS:
        pref = 'none'
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['subtitle_mode'] = pref
            _save_prefs(prefs)
    except Exception:
        pass


def normalize_filename_mode(value):
    """Return the shared output filename mode, preserving the v3 default."""
    mode = str(value or '').strip().lower().replace('_', '-')
    aliases = {
        '': 'full-title',
        'full': 'full-title',
        'title': 'full-title',
        'complete': 'full-title',
        'code': 'code-only',
        'jav-code': 'code-only',
        'number': 'code-only',
    }
    mode = aliases.get(mode, mode)
    if mode in VALID_FILENAME_MODES:
        return mode
    return 'full-title'


def get_filename_mode():
    """Return full-title (default) or code-only for every downloader UI."""
    return normalize_filename_mode(_load_prefs().get('filename_mode'))


def set_filename_mode(value):
    """Persist the shared filename mode without disturbing other preferences."""
    mode = normalize_filename_mode(value)
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['filename_mode'] = mode
            _save_prefs(prefs)
    except Exception:
        pass
    return mode


def _normalize_recognition_quality(value):
    quality = str(value or '').strip().lower().replace('_', '-')
    aliases = {
        '': 'auto',
        'default': 'auto',
        'automatic': 'auto',
        'recommended': 'auto',
        'cpu': 'auto',
        'precise': 'quality',
        'precision': 'quality',
        'accurate': 'quality',
        'accuracy': 'quality',
        'high': 'quality',
        'best': 'quality',
        'large': 'quality',
        'large-v3-turbo': 'quality',
        'medium': 'balanced',
        'normal': 'balanced',
        'small': 'balanced',
        'speed': 'fast',
        'quick': 'fast',
        'base': 'fast',
        'legacy': 'fast',
    }
    quality = aliases.get(quality, quality)
    if quality in VALID_RECOGNITION_QUALITIES:
        return quality
    return 'auto'


def get_recognition_quality():
    """Return the shared local speech-recognition quality preference."""
    return _normalize_recognition_quality(
        _load_prefs().get('recognition_quality'))


def set_recognition_quality(value):
    """Persist a normalized speech-recognition quality for both GUIs."""
    quality = _normalize_recognition_quality(value)
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['recognition_quality'] = quality
            _save_prefs(prefs)
    except Exception:
        pass
    return quality


def _normalize_download_concurrency(value):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = DEFAULT_DOWNLOAD_CONCURRENCY
    return max(MIN_DOWNLOAD_CONCURRENCY,
               min(parsed, MAX_DOWNLOAD_CONCURRENCY))


def get_download_concurrency():
    """Return the persisted number of simultaneous video downloads."""
    return _normalize_download_concurrency(
        _load_prefs().get('download_concurrency'))


def set_download_concurrency(value):
    """Persist a clamped simultaneous-video-download limit."""
    normalized = _normalize_download_concurrency(value)
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['download_concurrency'] = normalized
            _save_prefs(prefs)
    except Exception:
        pass
    return normalized


def normalize_max_workers_per_video(value):
    """Return a safe per-video segment worker count (1–16)."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_WORKERS_PER_VIDEO
    return max(MIN_WORKERS_PER_VIDEO, min(parsed, MAX_WORKERS_PER_VIDEO))


def get_max_workers_per_video():
    """Return the persisted number of segment workers used by each video."""
    return normalize_max_workers_per_video(
        _load_prefs().get('max_workers_per_video'))


def set_max_workers_per_video(value):
    """Persist a clamped per-video segment worker limit for every client."""
    normalized = normalize_max_workers_per_video(value)
    try:
        with _prefs_lock:
            prefs = _load_prefs()
            prefs['max_workers_per_video'] = normalized
            _save_prefs(prefs)
    except Exception:
        pass
    return normalized


def normalize_proxy_url(raw):
    """Validate one app-scoped proxy URL; bare host:port means HTTP."""
    value = str(raw or '').strip()
    if not value:
        return ''
    if re.search(r'[\x00-\x20\x7f]', value):
        raise ValueError('Proxy URL cannot contain whitespace or control characters')
    if '://' not in value:
        value = 'http://' + value
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        parsed.port  # force validation of malformed/out-of-range ports
    except (TypeError, ValueError):
        raise ValueError('Invalid proxy URL') from None
    if scheme not in VALID_PROXY_SCHEMES or not hostname:
        raise ValueError('Unsupported or incomplete proxy URL')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Proxy URL must not contain a path, query, or fragment')
    return value


def get_proxy_url():
    global _proxy_url_cache
    if _proxy_url_cache is _PROXY_UNSET:
        with _proxy_lock:
            if _proxy_url_cache is _PROXY_UNSET:
                try:
                    value = normalize_proxy_url(_load_prefs().get('proxy_url'))
                except ValueError:
                    value = ''
                _proxy_url_cache = value
    return _proxy_url_cache


def set_proxy_url(raw):
    global _proxy_mode_cache, _proxy_url_cache
    value = normalize_proxy_url(raw)
    with _prefs_lock:
        prefs = _load_prefs()
        if value:
            prefs['proxy_url'] = value
            prefs['proxy_mode'] = 'manual'
        else:
            prefs.pop('proxy_url', None)
            prefs['proxy_mode'] = 'direct'
        _save_prefs(prefs)
    with _proxy_lock:
        _proxy_url_cache = value
        _proxy_mode_cache = 'manual' if value else 'direct'
    return value


def get_proxy_mode():
    """Return manual/system/direct, migrating legacy preferences in memory."""
    global _proxy_mode_cache
    if _proxy_mode_cache is _PROXY_UNSET:
        with _proxy_lock:
            if _proxy_mode_cache is _PROXY_UNSET:
                prefs = _load_prefs()
                mode = str(prefs.get('proxy_mode') or '').strip().lower()
                if mode not in VALID_PROXY_MODES:
                    try:
                        legacy_url = normalize_proxy_url(
                            prefs.get('proxy_url'))
                    except ValueError:
                        legacy_url = ''
                    mode = 'manual' if legacy_url else 'direct'
                _proxy_mode_cache = mode
    return _proxy_mode_cache


def set_proxy_mode(mode):
    """Persist the routing mode without copying detected system settings."""
    global _proxy_mode_cache, _system_proxy_cache
    value = str(mode or '').strip().lower()
    if value not in VALID_PROXY_MODES:
        raise ValueError('Proxy mode must be manual, system, or direct')
    with _prefs_lock:
        prefs = _load_prefs()
        prefs['proxy_mode'] = value
        _save_prefs(prefs)
    with _proxy_lock:
        _proxy_mode_cache = value
        if value == 'system':
            _system_proxy_cache = _PROXY_UNSET
    return value


def parse_windows_proxy_server(raw):
    """Translate a Windows ProxyServer value into requests-style routes."""
    value = str(raw or '').strip()
    if not value:
        return {}

    if '=' not in value and ';' not in value:
        try:
            proxy = normalize_proxy_url(value)
        except ValueError:
            return {}
        return {'http': proxy, 'https': proxy}

    routes = {}
    socks_proxy = ''
    for item in value.split(';'):
        if '=' not in item:
            continue
        protocol, address = item.split('=', 1)
        protocol = protocol.strip().lower()
        address = address.strip()
        if protocol not in {'http', 'https', 'socks'} or not address:
            continue
        if protocol == 'socks' and '://' not in address:
            address = 'socks4://' + address
        try:
            proxy = normalize_proxy_url(address)
        except ValueError:
            continue
        if protocol == 'socks':
            socks_proxy = proxy
        else:
            routes[protocol] = proxy

    if socks_proxy:
        routes.setdefault('http', socks_proxy)
        routes.setdefault('https', socks_proxy)
    return routes


def _read_windows_proxy_settings():
    """Read the current user's manual WinINet proxy without changing it."""
    settings = {'enabled': False, 'server': '', 'pac_url': ''}
    if os.name != 'nt':
        return settings
    try:
        import winreg
        path = (
            r'Software\Microsoft\Windows\CurrentVersion'
            r'\Internet Settings'
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            try:
                enabled = winreg.QueryValueEx(key, 'ProxyEnable')[0]
                settings['enabled'] = (
                    isinstance(enabled, int) and enabled != 0)
            except OSError:
                pass
            try:
                server = winreg.QueryValueEx(key, 'ProxyServer')[0]
                if isinstance(server, str):
                    settings['server'] = server
            except OSError:
                pass
            try:
                pac_url = winreg.QueryValueEx(key, 'AutoConfigURL')[0]
                if isinstance(pac_url, str):
                    settings['pac_url'] = pac_url.strip()
            except OSError:
                pass
    except (ImportError, OSError, TypeError, ValueError):
        pass
    return settings


def _redact_proxy_url(value):
    """Return a display-safe endpoint with any user information removed."""
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ''
        if ':' in hostname and not hostname.startswith('['):
            hostname = f'[{hostname}]'
        port = f':{parsed.port}' if parsed.port is not None else ''
        return f'{parsed.scheme}://{hostname}{port}'
    except (TypeError, ValueError):
        return ''


def _detect_windows_proxy_state():
    settings = _read_windows_proxy_settings()
    routes = {}
    if settings.get('enabled'):
        routes = parse_windows_proxy_server(settings.get('server'))
    if routes:
        selected = routes.get('https') or routes.get('http') or ''
        return routes, _redact_proxy_url(selected), 'detected'
    if settings.get('pac_url'):
        # A PAC URL points to executable configuration, not a proxy endpoint.
        return {}, '', 'pac'
    if settings.get('enabled'):
        return {}, '', 'invalid'
    return {}, '', 'disabled'


def detect_windows_proxy_url():
    """Detect once and return a credential-free endpoint plus status."""
    _routes, display_url, status = _detect_windows_proxy_state()
    return display_url, status


def refresh_system_proxy():
    """Refresh the in-memory system proxy used by System mode."""
    global _system_proxy_cache
    routes, display_url, status = _detect_windows_proxy_state()
    with _proxy_lock:
        _system_proxy_cache = (dict(routes), status)
    return display_url, status


def refresh_windows_proxy():
    """Compatibility alias for callers that name the Windows source."""
    return refresh_system_proxy()


def _get_cached_system_proxy():
    global _system_proxy_cache
    if _system_proxy_cache is _PROXY_UNSET:
        routes, _display_url, status = _detect_windows_proxy_state()
        with _proxy_lock:
            if _system_proxy_cache is _PROXY_UNSET:
                _system_proxy_cache = (dict(routes), status)
    with _proxy_lock:
        routes, status = _system_proxy_cache
        return dict(routes), status


def proxy_request_kwargs():
    """Return explicit routes for requests and curl_cffi.

    Empty routes are intentional: both clients otherwise inherit proxy
    environment variables, which would make the Direct mode misleading.
    """
    direct_routes = {'http': '', 'https': '', 'all': ''}
    mode = get_proxy_mode()
    if mode == 'direct':
        return {'proxies': direct_routes}
    if mode == 'system':
        routes, _status = _get_cached_system_proxy()
        direct_routes.update(routes)
        return {'proxies': direct_routes}
    value = get_proxy_url()
    if not value:
        return {'proxies': direct_routes}
    direct_routes.update({'http': value, 'https': value})
    return {'proxies': direct_routes}


def _parse_cf_clearance(raw):
    if raw is None:
        return ''
    s = re.sub(r'[\x00-\x1f\x7f]+', '', str(raw).strip())
    if not s:
        return ''
    if 'cf_clearance=' in s:
        m = re.search(r'cf_clearance=([^;,\s]+)', s)
        return m.group(1) if m else ''
    return s.strip('\'"')


def _norm_host(host):
    h = (host or '').strip().lower().rstrip('.')
    if ':' in h:
        h = h.split(':', 1)[0].rstrip('.')
    return h


def get_cf_override(host):
    with _cf_lock:
        entry = CF_OVERRIDES.get(_norm_host(host))
        return dict(entry) if entry else None


def cf_override_hosts():
    with _cf_lock:
        return sorted(CF_OVERRIDES.keys())


def set_cf_override(host, cookie, ua):
    global CF_OVERRIDES
    h = _norm_host(host)
    if not h:
        return
    entry = {}
    ck = _parse_cf_clearance(cookie)
    if ck:
        entry['cookie'] = ck
    ua = (ua or '').strip()
    if ua:
        entry['ua'] = ua
    with _cf_lock:
        next_overrides = dict(CF_OVERRIDES)
        if entry:
            next_overrides[h] = entry
        else:
            next_overrides.pop(h, None)
        CF_OVERRIDES = next_overrides
    save_cf_overrides()


def clear_cf_override(host):
    set_cf_override(host, '', '')


def load_cf_overrides():
    global CF_OVERRIDES
    path = _cf_store_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except FileNotFoundError:
        with _cf_lock:
            CF_OVERRIDES = {}
        return
    except Exception:
        try:
            os.replace(path, path + '.bak')
        except Exception:
            pass
        with _cf_lock:
            CF_OVERRIDES = {}
        return

    parsed = {}
    if isinstance(raw, dict):
        for host, entry in raw.items():
            h = _norm_host(host)
            if not h or not isinstance(entry, dict):
                continue
            clean = {}
            cookie = entry.get('cookie')
            ua = entry.get('ua')
            if isinstance(cookie, str):
                cookie = _parse_cf_clearance(cookie)
                if cookie:
                    clean['cookie'] = cookie
            if isinstance(ua, str) and ua.strip():
                clean['ua'] = ua.strip()
            if clean:
                parsed[h] = clean
    with _cf_lock:
        CF_OVERRIDES = parsed


def save_cf_overrides():
    try:
        path = _cf_store_path()
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        with _cf_lock:
            snapshot = dict(CF_OVERRIDES)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        pass


try:
    load_cf_overrides()
except Exception:
    pass
