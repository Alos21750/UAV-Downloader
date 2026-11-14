"""Shared network thumbnail cache, independent of GUI frameworks."""
import io
import threading
from typing import Optional
from urllib.parse import urlsplit
import requests
from PIL import Image
from uav_downloader.core import config
from uav_downloader.core.config import headers
from uav_downloader.core.ssl import SharedSSLAdapter

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
