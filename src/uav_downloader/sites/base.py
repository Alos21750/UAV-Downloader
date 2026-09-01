#!/usr/bin/env python
# coding: utf-8

import platform
import html
import os
import re
import threading
import requests
from uav_downloader.core.ssl import SharedSSLAdapter, get_shared_ssl_context
try:
    from curl_cffi import requests as _cffi_requests
    _use_cffi = True
except Exception:
    _use_cffi = False
import urllib.request
import m3u8
from uav_downloader.core import config
from Crypto.Cipher import AES
from uav_downloader.core.config import headers
import concurrent.futures
import copy
import time
import subprocess
import shutil
import tempfile
import ctypes
import sys
from urllib.parse import urlsplit, urlunsplit, urljoin
from uav_downloader.core.video_identity import (
    normalize_source_subtitle_evidence,
    trusted_chinese_subtitle_evidence,
)


class MirrorsBlockedError(Exception):
    pass


class DownloadIncompleteError(Exception):
    """A resumable segment download stopped with a few chunks outstanding."""

    def __init__(self, remaining_segments):
        try:
            remaining = max(1, int(remaining_segments))
        except (TypeError, ValueError):
            remaining = 1
        self.remaining_segments = remaining
        super().__init__(
            f"download incomplete ({remaining} segment(s) still unavailable; "
            "Retry resumes existing segments)")


request_headers = {'browser': 'firefox', 'platform': platform.system().lower()}
default_max_workers = config.DEFAULT_MAX_WORKERS_PER_VIDEO

_WINDOWS_FILENAME_TRANSLATION = str.maketrans({
    '<': '＜', '>': '＞', ':': '：', '"': '＂',
    '/': '／', '\\': '＼', '|': '｜', '?': '？', '*': '＊',
})
_WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}


def _sanitize_filename(value):
    """Keep the source title readable while making it portable to Windows."""
    name = html.unescape(str(value or ''))
    name = re.sub(r'[\x00-\x1f\x7f]', ' ', name)
    name = name.translate(_WINDOWS_FILENAME_TRANSLATION).strip()

    trailing_dots = len(name) - len(name.rstrip('.'))
    if trailing_dots:
        name = name[:-trailing_dots] + '．' * trailing_dots

    stem = name.split('.', 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        name = '_' + name
    return name


def _apply_filename_mode(value, mode):
    """Apply the opt-in title policy after portable-character sanitizing.

    ``code-only`` deliberately follows the reporter's cross-studio rule: keep
    everything before the first Unicode whitespace instead of guessing every
    possible JAV-code shape with a regex.  A title with no whitespace remains
    unchanged.
    """
    name = str(value or '').strip()
    if config.normalize_filename_mode(mode) != 'code-only' or not name:
        return name
    return name.split(maxsplit=1)[0]


def _utf16_units(value):
    return len(value.encode('utf-16-le')) // 2


def _portable_filename_fits(dest_folder, target_name):
    part_name = target_name + '.mp4.part'
    full_path = os.path.join(dest_folder, part_name)
    return (_utf16_units(full_path) <= 255 and
            len(part_name.encode('utf-8')) <= 255)


def _truncate_target_name(name, dest_folder, dirname):
    """Cap both Windows path units and POSIX component bytes, keeping a UID."""
    if _portable_filename_fits(dest_folder, name):
        return name

    uid = re.sub(r'[^\w]', '', dirname)[-8:] or 'video'
    suffix = '_' + uid
    if not _portable_filename_fits(dest_folder, suffix):
        raise OSError('Destination path is too long for a portable filename')

    best = suffix
    low, high = 0, len(name)
    while low <= high:
        middle = (low + high) // 2
        prefix = name[:middle].rstrip()
        candidate = prefix + suffix if prefix else suffix
        if _portable_filename_fits(dest_folder, candidate):
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best

_session_lock = threading.Lock()
_session = None
get_shared_ssl_context()

_active_host = {}                 # sticky per-process working host per site_key
_active_host_lock = threading.Lock()

def _swap_host(url, host):
    p = urlsplit(url)
    return urlunsplit((p.scheme or 'https', host, p.path, p.query, p.fragment))

def _is_cf_interstitial(resp):
    # Fast reject for Cloudflare block pages. NOTE: 'challenge-platform' also appears on
    # SUCCESSFUL missav pages -- never use it as a marker. Content success is decided by validate().
    if resp.status_code in (403, 429, 503):
        return True
    if 'challenge' in resp.headers.get('cf-mitigated', '').lower():
        return True
    head = resp.content[:3000].lower()
    return (b'just a moment' in head) or (b'cf-browser-verification' in head) or (b'cf_chl_' in head)

def fetch_with_mirrors(scraper, url, site_key, validate, timeout=15, headers_factory=None):
    """GET url, rotating host across config.MIRRORS[site_key].
    Order: original host (if allowlisted) -> sticky active host -> remaining mirrors (dedup, order-preserving).
    Per host: 1 retry on transport error; skip CF interstitials; reject redirects that land off the allowlist.
    validate(resp)->bool gates content success. headers_factory(host)->dict gives per-host headers (e.g. Referer).
    Returns (resp, host, reason): 'ok' (validated) | 'empty' (a real non-interstitial page was seen but none validated)
    | 'blocked' (every attempt was interstitial / transport failure)."""
    mirrors = config.MIRRORS.get(site_key) or [urlsplit(url).netloc]
    orig = urlsplit(url).netloc
    with _active_host_lock:
        active = _active_host.get(site_key)
    order = []
    for h in [orig, active] + list(mirrors):
        if h and h in mirrors and h not in order:
            order.append(h)
    if not order:
        order = list(mirrors)
    saw_real = False
    for host in order:
        target = _swap_host(url, host)
        base_hdrs = dict(headers_factory(host) or {}) if headers_factory else {}
        ov = config.get_cf_override(host)
        trials = []
        if ov:
            h2 = dict(base_hdrs)
            if ov.get('ua'):
                h2['User-Agent'] = ov['ua']
            ck = {'cf_clearance': ov['cookie']} if ov.get('cookie') else None
            trials.append((h2, ck))
        trials.append((base_hdrs, None))
        resp = None
        for hdrs, cookies in trials:
            r = None
            for attempt in range(2):                  # 1 retry on transport error only
                try:
                    r = scraper.get(
                        target, timeout=timeout, headers=hdrs or {}, cookies=cookies,
                        **config.proxy_request_kwargs())
                    break
                except Exception:
                    r = None
            if r is None:
                continue
            if _is_cf_interstitial(r):
                resp = r
                continue
            resp = r
            break
        if resp is None:
            continue
        if _is_cf_interstitial(resp):
            continue
        try:
            final_host = urlsplit(str(resp.url)).netloc
        except Exception:
            final_host = host
        if final_host and final_host not in mirrors:  # redirected off the allowlist -> distrust
            continue
        saw_real = True
        try:
            ok = validate(resp)
        except Exception:
            ok = False
        if ok:
            with _active_host_lock:
                _active_host[site_key] = host
            return resp, host, 'ok'
    return None, None, ('empty' if saw_real else 'blocked')

# ── Global speed limiter (token bucket) ──────────────────────────
class _SpeedLimiter:
    """Thread-safe token-bucket rate limiter shared across all downloads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._limit_bps = 0  # 0 = unlimited
        self._tokens = 0.0
        self._last = time.time()

    def set_limit(self, mbps: float) -> None:
        with self._lock:
            self._limit_bps = int(mbps * 1024 * 1024) if mbps > 0 else 0
            self._tokens = float(self._limit_bps)
            self._last = time.time()

    def acquire(self, nbytes: int) -> None:
        with self._lock:
            limit = self._limit_bps
        if limit <= 0:
            return
        while nbytes > 0:
            with self._lock:
                now = time.time()
                self._tokens += (now - self._last) * self._limit_bps
                self._last = now
                if self._tokens > self._limit_bps:
                    self._tokens = float(self._limit_bps)
                take = min(nbytes, int(self._tokens))
                if take > 0:
                    self._tokens -= take
                    nbytes -= take
            if nbytes > 0:
                time.sleep(0.05)

speed_limiter = _SpeedLimiter()


def _is_valid_ts_segment(data):
    return bool(data and len(data) >= 188 and data[:1] == b'\x47')

_FFMPEG_PATH = None
_FFMPEG_RESOLVED = False

def _no_window_kwargs():
    if os.name == 'nt':
        return {'creationflags': 0x08000000}  # CREATE_NO_WINDOW
    return {}

def _ffmpeg_works(path):
    try:
        r = subprocess.run([path, '-version'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, **_no_window_kwargs())
        return r.returncode == 0
    except Exception:
        return False

def _companion_ffmpeg():
    """Look for ffmpeg shipped next to the app (frozen exe folder / _MEIPASS / script dir)."""
    dirs = []
    if getattr(sys, 'frozen', False):
        dirs.append(os.path.dirname(sys.executable))
        mp = getattr(sys, '_MEIPASS', None)
        if mp:
            dirs.append(mp)
    else:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
        dirs.append(os.getcwd())
    names = ('ffmpeg.exe', 'ffmpeg') if os.name == 'nt' else ('ffmpeg',)
    for d in dirs:
        for nm in names:
            p = os.path.join(d, nm)
            if os.path.isfile(p):
                return p
    return None

def locate_ffmpeg():
    global _FFMPEG_PATH, _FFMPEG_RESOLVED
    if _FFMPEG_RESOLVED:
        return _FFMPEG_PATH
    def candidates():
        c = _companion_ffmpeg()
        if c:
            yield c
        w = shutil.which('ffmpeg')
        if w:
            yield w
        try:
            import imageio_ffmpeg
            yield imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return
    chosen = None
    for c in candidates():
        if c and _ffmpeg_works(c):
            chosen = c
            break
    _FFMPEG_PATH = chosen
    _FFMPEG_RESOLVED = True
    return chosen

def _ffmpeg_safe_dir(path):
    """Return an ffmpeg-friendly (ASCII if possible) form of an EXISTING dir path."""
    if os.name != 'nt' or path.isascii():
        return path
    try:
        GetShort = ctypes.windll.kernel32.GetShortPathNameW
        buf = ctypes.create_unicode_buffer(600)
        n = GetShort(path, buf, 600)
        if n and n < 600 and buf.value:
            return buf.value
    except Exception:
        pass
    return path

# ── Resolution preference ──────────────────────────────────────────
_VALID_RESOLUTION_PREFS = {'highest', 'lowest', '1080', '720', '480', '360'}
_resolution_pref = 'highest'


def set_resolution_pref(value) -> None:
    global _resolution_pref
    pref = str(value or '').strip().lower()
    if pref in _VALID_RESOLUTION_PREFS:
        _resolution_pref = pref


def get_resolution_pref() -> str:
    return _resolution_pref


def set_prefer_lowest_res(value: bool) -> None:
    set_resolution_pref('lowest' if value else 'highest')


def get_prefer_lowest_res() -> bool:
    return _resolution_pref == 'lowest'


def _variant_height_bw(playlist):
    stream_info = getattr(playlist, 'stream_info', None)
    height = None
    resolution = getattr(stream_info, 'resolution', None) if stream_info else None
    if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
        try:
            height = int(resolution[1])
        except (TypeError, ValueError):
            height = None
    try:
        bandwidth = int(getattr(stream_info, 'bandwidth', 0) or 0) if stream_info else 0
    except (TypeError, ValueError):
        bandwidth = 0
    return height, bandwidth


def select_variant(playlists, pref):
    items = [(playlist, *_variant_height_bw(playlist)) for playlist in (playlists or [])]
    if not items:
        return None

    pref = str(pref or '').strip().lower()
    if pref not in _VALID_RESOLUTION_PREFS:
        pref = 'highest'

    known = [item for item in items if item[1] is not None]
    if pref == 'lowest':
        if known:
            return min(known, key=lambda item: (item[1], item[2]))[0]
        return min(items, key=lambda item: item[2])[0]

    if pref in {'1080', '720', '480', '360'}:
        if not known:
            return max(items, key=lambda item: item[2])[0]
        target = int(pref)
        at_or_below = [item for item in known if item[1] <= target]
        if at_or_below:
            return max(at_or_below, key=lambda item: (item[1], item[2]))[0]
        return min(known, key=lambda item: (item[1], -item[2]))[0]

    if known:
        return max(known, key=lambda item: (item[1], item[2]))[0]
    return max(items, key=lambda item: item[2])[0]


def _get_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = requests.Session()
                http_adapter = requests.adapters.HTTPAdapter(
                    pool_connections=32,
                    pool_maxsize=64,
                    max_retries=2,
                )
                https_adapter = SharedSSLAdapter(
                    pool_connections=32,
                    pool_maxsize=64,
                    max_retries=2,
                )
                _session.mount('http://', http_adapter)
                _session.mount('https://', https_adapter)
    return _session


_cffi_tls = threading.local()
_CDN_BLOCKED_MSG = "影片 CDN（surrit.com）被 Cloudflare 阻擋，請改用 VPN/WARP 或不同網路後重試"


def _get_cffi_session():
    session = getattr(_cffi_tls, 'session', None)
    if session is None:
        session = _cffi_requests.Session(impersonate='chrome')
        _cffi_tls.session = session
    return session


def _is_cf_block_resp(resp):
    try:
        if getattr(resp, 'status_code', None) not in (403, 429, 503):
            return False
        resp_headers = getattr(resp, 'headers', {}) or {}
        server = resp_headers.get('Server', resp_headers.get('server', '')) if hasattr(resp_headers, 'get') else ''
        has_cf_ray = any(str(k).lower() == 'cf-ray' for k in resp_headers.keys())
        content = getattr(resp, 'content', b'') or b''
        head = content[:3000]
        if isinstance(head, str):
            head = head.encode('utf-8', errors='ignore')
        head = head.lower()
        return (
            'cloudflare' in str(server).lower()
            or has_cf_ray
            or any(marker in head for marker in (
                b'attention required',
                b'just a moment',
                b'cf_chl_',
                b'cf-error',
            ))
        )
    except Exception:
        return False


def _http_get(url, headers=None, timeout=20):
    if _use_cffi:
        try:
            cffi_headers = {
                k: v for k, v in dict(headers or {}).items()
                if str(k).lower() != 'user-agent'
            }
            return _get_cffi_session().get(
                url, headers=cffi_headers, timeout=timeout,
                **config.proxy_request_kwargs())
        except Exception:
            pass
    return _get_session().get(
        url, headers=dict(headers or {}), timeout=timeout,
        **config.proxy_request_kwargs())


class M3U8Crawler:
    """A base class for all m3u8 crawl website tools."""
    skip_pattern = False
    segment_worker_cap = None
    segment_retry_rounds = 6
    segment_retry_base_delay = 1.5
    segment_retry_max_delay = 6.0

    @classmethod
    def validate_url(cls, url):
        if not url or url == '': return None
        result = re.match(cls.website_dirname_pattern, url, flags=re.I)
        if result: return result.group(1)
        return None

    def __init__(self, url, savepath="", silence=False, max_workers=None):
        self.silence = silence
        self._tsList = []
        self._key_content = None   # raw bytes of AES key
        self._key_method = None    # e.g. 'AES-128'
        self._key_iv = None        # hex IV string (may be None)
        self._downloadList = []
        self._t_executor = None
        self._t_future = None
        self._t2_executor = None
        self._cancel_job = None
        self._ffmpeg_proc = None
        self._extra_headers = {}   # subclass may set (e.g. Referer)
        self._dirName = None
        self._dest_folder = None
        self._temp_folder = None
        self._targetName = None
        self._imageUrl = None
        self._m3u8url = None
        if max_workers is None:
            max_workers = config.get_max_workers_per_video()
        normalized_workers = config.normalize_max_workers_per_video(max_workers)
        worker_cap = getattr(self, 'segment_worker_cap', None)
        if worker_cap is not None:
            try:
                normalized_workers = min(
                    normalized_workers, max(1, int(worker_cap)))
            except (TypeError, ValueError):
                pass
        self._max_workers = normalized_workers
        self._segment_retry_rounds = max(
            1, int(getattr(self, 'segment_retry_rounds', 6)))
        self._segment_retry_base_delay = max(
            0.0, float(getattr(self, 'segment_retry_base_delay', 1.5)))
        self._segment_retry_max_delay = max(
            self._segment_retry_base_delay,
            float(getattr(self, 'segment_retry_max_delay', 6.0)))
        self._filename_mode = config.get_filename_mode()
        self._progress_callback = None   # (downloaded, total, speed_bps) -> None
        self._speed_lock = threading.Lock()
        self._bytes_downloaded = 0
        self._speed_start = 0.0
        self._last_error = None
        self._source_subtitle_evidence = ()
        try:
            self._dirName = self.validate_url(url)
            if not self._dirName: return
            self._url = url
            if (savepath is None) or (savepath == ''):
                self._dest_folder = os.path.join(os.getcwd(), self._dirName)
            else:
                self._dest_folder = os.path.abspath(savepath)
            self._temp_folder = os.path.join(self._dest_folder, self._dirName)

            self.get_url_infos()
            self.add_source_video_metadata({'url': self._url})
            if self.is_url_vaildate():
                if self._targetName:
                    self._targetName = _sanitize_filename(self._targetName)
                    self._targetName = _apply_filename_mode(
                        self._targetName, self._filename_mode)
                    if len(self._dirName) > 80:
                        self._dirName = self._dirName[:80]
                        self._temp_folder = os.path.join(self._dest_folder, self._dirName)
                    if not self._targetName.strip():
                        self._targetName = (_sanitize_filename(self._dirName)
                                            or 'video')
                    self._targetName = _truncate_target_name(
                        self._targetName, self._dest_folder, self._dirName)
                if not self.silence:
                    if self._targetName: print("檔案名稱: " + self._targetName, flush=True)
                    if self._dest_folder: print("儲存位置: " + self._dest_folder, flush=True)
                    if self._imageUrl: print("縮圖: " + self._imageUrl, flush=True)

        except Exception as exc:
            self._last_error = exc
            self._targetName = self._imageUrl = self._m3u8url = None
            print(f"下載網址 {url} 錯誤!! ({exc})", flush=True)

    def get_url_infos(self): raise Exception("Must implement get_url_infos()")
    def target_name(self): return self._targetName
    def dest_folder(self): return self._dest_folder
    def is_url_vaildate(self): return True if self._m3u8url else False

    def add_source_subtitle_evidence(self, evidence):
        evidence = trusted_chinese_subtitle_evidence({
            'url': self._url,
            '_source_subtitle_evidence': evidence,
        })
        combined = set(normalize_source_subtitle_evidence(
            self._source_subtitle_evidence))
        combined.update(evidence)
        self._source_subtitle_evidence = (
            normalize_source_subtitle_evidence(combined))

    def add_source_video_metadata(self, video):
        """Attach reviewed source-version evidence supplied by a browser scan."""
        self.add_source_subtitle_evidence(
            trusted_chinese_subtitle_evidence(video))

    def source_subtitle_evidence(self):
        return tuple(self._source_subtitle_evidence)

    def _transform_segment(self, data):
        """Hook: transform raw segment bytes before AES/write. Default identity; sites may override."""
        return data

    def _create_temp_folder(self):
        if not os.path.exists(self._temp_folder):
            os.makedirs(self._temp_folder, exist_ok=True)

    def _create_dest_folder(self):
        if not os.path.exists(self._dest_folder):
            os.makedirs(self._dest_folder, exist_ok=True)

    def _get_video_savename(self):
        return os.path.join(self._dest_folder, self._targetName + ".mp4")

    def _get_image_savename(self):
        if self._imageUrl is None: return None
        return os.path.join(self._dest_folder, self._targetName + ".jpg")

    def get_url_full(self): return self._url

    def is_target_video_exist(self):
        return os.path.exists(self._get_video_savename())

    def is_target_image_exist(self):
        if self._imageUrl is None: return True
        return os.path.exists(self._get_image_savename())

    def _m3u8_headers(self):
        """Merged headers for m3u8 and segment requests."""
        return {**headers, **self._extra_headers}

    def _load_m3u8(self, url):
        resp = _http_get(url, self._m3u8_headers(), timeout=20)
        status = getattr(resp, 'status_code', 0)
        if status != 200:
            if _is_cf_block_resp(resp):
                raise MirrorsBlockedError(_CDN_BLOCKED_MSG)
            raise Exception(f"m3u8 取得失敗 (HTTP {status}): {url}")
        text = resp.text or ''
        if '#EXTM3U' not in text:
            content = getattr(resp, 'content', b'') or b''
            head = content[:3000]
            if isinstance(head, str):
                head = head.encode('utf-8', errors='ignore')
            if _is_cf_block_resp(resp) or b'cloudflare' in head.lower():
                raise MirrorsBlockedError(_CDN_BLOCKED_MSG)
            raise Exception(f"m3u8 內容無效（可能被阻擋或改版）: {url}")
        return m3u8.loads(text, uri=str(getattr(resp, 'url', url)))

    def _getm3u8PlayList(self, uri):
        if uri.startswith(('http://', 'https://', '//')):
            playListUrl = urljoin(self._m3u8url, uri)
        else:
            m3u8urlPath = self._m3u8url.split('/')
            if uri.startswith('/'): m3u8urlPath = m3u8urlPath[:3]
            else: m3u8urlPath.pop(-1)
            baseurl = '/'.join(m3u8urlPath)
            playListUrl = baseurl + '/' + uri.lstrip('/')
        m3u8obj = self._load_m3u8(playListUrl)
        variantBase = playListUrl.rsplit('/', 1)[0] + '/'
        return m3u8obj, variantBase

    def _create_m3u8(self):
        m3u8urlList = self._m3u8url.split('/')
        m3u8urlList.pop(-1)
        downloadurl = '/'.join(m3u8urlList) + '/'

        m3u8obj = self._load_m3u8(self._m3u8url)
        if len(m3u8obj.playlists) > 0:
            # Pick variant based on resolution preference
            best = select_variant(m3u8obj.playlists, _resolution_pref)
            if best:
                m3u8obj, downloadurl = self._getm3u8PlayList(best.uri)

        # Extract key info (store bytes + IV, not a cipher - cipher is NOT thread-safe)
        self._key_content = None
        self._key_method = None
        self._key_iv = None
        for key in m3u8obj.keys:
            if key and key.uri:
                m3u8_key_uri = key.uri
                if not m3u8_key_uri.startswith('http'):
                    m3u8_key_uri = downloadurl + m3u8_key_uri
                resp = _http_get(m3u8_key_uri, self._m3u8_headers(), timeout=15)
                blocked = _is_cf_block_resp(resp)
                if resp.status_code != 200 or blocked:
                    if blocked:
                        raise MirrorsBlockedError(_CDN_BLOCKED_MSG)
                    raise Exception("AES 金鑰取得失敗")
                self._key_content = resp.content
                self._key_method = getattr(key, 'method', 'AES-128')
                self._key_iv = getattr(key, 'iv', None)
                break  # use first key

        # Per HLS: a key with no explicit IV uses an implicit IV = the segment's MEDIA
        # SEQUENCE number, which begins at #EXT-X-MEDIA-SEQUENCE (may be non-zero), not 0.
        self._media_sequence = getattr(m3u8obj, 'media_sequence', 0) or 0
        # Build segment URL list
        self._tsList = []
        for seg in m3u8obj.segments:
            uri = seg.uri
            if uri.startswith('https://') or uri.startswith('http://'):
                tsUrl = uri
            else:
                tsUrl = downloadurl + uri
            self._tsList.append(tsUrl)
        if not self._tsList:
            raise Exception("m3u8 無有效片段（可能被阻擋或改版）")

    def _make_cipher(self, seq_num=0):
        """Create a fresh AES cipher for one segment (thread-safe)."""
        if not self._key_content:
            return None
        if self._key_iv:
            iv_hex = self._key_iv.replace("0x", "").replace("0X", "")
            iv_bytes = bytes.fromhex(iv_hex.zfill(32))
        else:
            # Default IV = this segment's media-sequence number (base + playlist index),
            # as a 16-byte big-endian value.
            iv_bytes = (seq_num + getattr(self, '_media_sequence', 0)).to_bytes(16, 'big')
        return AES.new(self._key_content, AES.MODE_CBC, iv_bytes)

    def _seg_savename(self, index):
        """Per-segment temp file named by playlist INDEX, not URL basename. Two segment
        URLs can share a basename (e.g. seg.ts?n=1 vs seg.ts?n=2, or same-named files in
        different path dirs); basename naming collided them into one file — the second was
        skipped as 'already done' and the first was read twice at merge, silently
        corrupting the output. Index naming is unique and keeps resume correct."""
        return os.path.join(self._temp_folder, f"{index:06d}.mp4")

    def _deleteMp4Chunks(self):
        for i in range(len(self._tsList)):
            saveName = self._seg_savename(i)
            if os.path.exists(saveName):
                try: os.remove(saveName)
                except OSError: pass

    def _cancellable_move(self, src, dst):
        """Move src->dst honoring cancel. Fast same-volume rename, else chunked copy."""
        try:
            os.replace(src, dst)   # same volume: instant + atomic
            return True
        except OSError:
            pass
        try:
            with open(src, 'rb') as fi, open(dst, 'wb') as fo:
                while True:
                    if self._cancel_job:
                        break
                    buf = fi.read(4 * 1024 * 1024)
                    if not buf:
                        try: os.remove(src)
                        except OSError: pass
                        return True
                    fo.write(buf)
            try: os.remove(dst)      # cancelled mid-copy
            except OSError: pass
            return False
        except Exception:
            return False

    def _mergeMp4Chunks(self):
        start_time = time.time()
        saveName = self._get_video_savename()
        part = saveName + '.part'
        for p in (saveName, part):
            if os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
        n = len(self._tsList)
        print(f'開始合成影片...共有 {n} 個片段', flush=True)

        # Keep the full-size merge and remux copies on the selected download
        # volume.  Windows' default temp directory normally lives on C:, and
        # using it here could consume almost two extra copies of a video after
        # the segment progress had already reached 100%.
        workdir = tempfile.mkdtemp(
            prefix='uav-remux-', dir=self._dest_folder)
        merged = os.path.join(workdir, 'merged.ts')
        out_mp4 = os.path.join(workdir, 'out.mp4')
        published = False
        try:
            remaining = n
            with open(merged, 'wb') as out:
                for idx, ts_url in enumerate(self._tsList):
                    if self._cancel_job:
                        return 0
                    seg = self._seg_savename(idx)
                    if not os.path.exists(seg):
                        if not self._cancel_job:
                            print(f"\n片段 {idx} 遺失, 合成失敗!!!", flush=True)
                        return 0
                    with open(seg, 'rb') as f:
                        shutil.copyfileobj(f, out, 1024 * 1024)
                    remaining -= 1
                    print(f'\r合成影片中, 剩餘 {remaining} 個片段', end="")
            print()
            if self._cancel_job:
                return 0

            ok = self._remux_to_mp4(merged, out_mp4, workdir)
            if self._cancel_job:
                return 0
            if ok:
                moved = self._cancellable_move(out_mp4, part)
            else:
                print('[合成] ffmpeg 無法使用或重新封裝失敗，改用原始合併（檔案可播放，但部分播放器/NAS 拖曳進度可能異常）', flush=True)
                moved = self._cancellable_move(merged, part)
            if self._cancel_job or not moved:
                if os.path.exists(part):
                    try: os.remove(part)
                    except OSError: pass
                return 0
            os.replace(part, saveName)
            published = True
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if not published and os.path.exists(part):
                try: os.remove(part)
                except OSError: pass

        spent_time = time.time() - start_time
        print(f'\n合成完成，花費 {spent_time:.1f} 秒', flush=True)
        self._deleteMp4Chunks()
        if self._temp_folder != self._dest_folder:
            try: os.removedirs(self._temp_folder)
            except OSError: pass
        return spent_time

    def _remux_to_mp4(self, merged_ts, out_mp4, workdir):
        ffmpeg = locate_ffmpeg()
        if not ffmpeg:
            print('[合成] 找不到 ffmpeg，略過重新封裝', flush=True)
            return False
        ff_dir = _ffmpeg_safe_dir(workdir)
        in_arg = os.path.join(ff_dir, os.path.basename(merged_ts))
        out_arg = os.path.join(ff_dir, os.path.basename(out_mp4))
        log_path = os.path.join(workdir, 'ffmpeg.log')
        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
               '-fflags', '+genpts', '-i', in_arg,
               '-c', 'copy', '-movflags', '+faststart',
               '-avoid_negative_ts', 'make_zero', out_arg]
        print('正在重新封裝為可正常拖曳的 MP4 ...', flush=True)
        try:
            with open(log_path, 'wb') as errf:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=errf,
                                        **_no_window_kwargs())
                self._ffmpeg_proc = proc
                try:
                    while True:
                        try:
                            proc.wait(timeout=0.3); break
                        except subprocess.TimeoutExpired:
                            if self._cancel_job:
                                proc.kill(); proc.wait(); return False
                finally:
                    self._ffmpeg_proc = None
        except Exception as e:
            print(f'[合成] ffmpeg 執行錯誤: {e}', flush=True)
            return False
        if self._cancel_job:
            return False
        if proc.returncode == 0 and os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
            return True
        tail = ''
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                tail = f.read()[-800:]
        except Exception:
            pass
        print(f'[合成] ffmpeg 重新封裝失敗 rc={proc.returncode}: {tail}', flush=True)
        return False

    def _scrape(self, task):
        """Download and decrypt one segment. task=(seq_num, url)"""
        seq_num, url = task
        saveName = self._seg_savename(seq_num)
        if os.path.exists(saveName):
            # Segment already on disk (e.g. from a resumed job) — drop from pending
            with self._speed_lock:
                self._pending_set.discard((seq_num, url))
            return True

        try:
            response = _http_get(url, self._m3u8_headers(), timeout=25)
            if response.status_code != 200:
                return False
            content_ts = response.content
            if not content_ts:
                return False
            content_ts = self._transform_segment(content_ts)
            if self._key_content:
                cipher = self._make_cipher(seq_num)
                content_ts = cipher.decrypt(content_ts)
            if not _is_valid_ts_segment(content_ts):
                return False
            speed_limiter.acquire(len(content_ts))
            with open(saveName, 'wb') as f:
                f.write(content_ts)
            with self._speed_lock:
                self._pending_set.discard((seq_num, url))
                self._bytes_downloaded += len(content_ts)
                remain = len(self._pending_set)
                elapsed = time.time() - self._speed_start
                speed = self._bytes_downloaded / elapsed if elapsed > 0 else 0
                done = self._job_total - remain
                remain_time = (remain * elapsed / done) if done > 0 else 0
                if remain_time > 60:
                    rem_str = f"{remain_time//60:.0f}分 {remain_time%60:.0f}秒"
                else:
                    rem_str = f"{remain_time:.0f}秒"
                speed_str = f"{speed/1024:.0f} KB/s" if speed < 1024*1024 else f"{speed/1024/1024:.1f} MB/s"
                print(f'\r下載中: {done}/{self._job_total} 片段 | {speed_str} | 剩餘 {rem_str}  ', end='', flush=True)
                if self._progress_callback:
                    self._progress_callback(done, self._job_total, speed)
            return True
        except Exception:
            return False

    def _startCrawl(self):
        self._speed_start = time.time()
        self._bytes_downloaded = 0
        total = len(self._tsList)
        self._job_total = len(self._pending_set)
        print(f'共 {total} 片段，已完成 {total - self._job_total}，剩餘 {self._job_total}...', flush=True)

        max_rounds = max(1, int(getattr(
            self, '_segment_retry_rounds',
            getattr(self, 'segment_retry_rounds', 6))))
        retry_base_delay = max(0.0, float(getattr(
            self, '_segment_retry_base_delay',
            getattr(self, 'segment_retry_base_delay', 1.5))))
        retry_max_delay = max(retry_base_delay, float(getattr(
            self, '_segment_retry_max_delay',
            getattr(self, 'segment_retry_max_delay', 6.0))))
        for round_num in range(1, max_rounds + 1):
            if not self._pending_set or self._cancel_job:
                break
            tasks = list(self._pending_set)
            with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
                self._t2_executor = executor
                list(executor.map(self._scrape, tasks, timeout=None))
            self._t2_executor = None
            still_pending = len(self._pending_set)
            if still_pending == 0:
                break
            if round_num < max_rounds:
                print(f'\n重試第 {round_num} 次，剩餘 {still_pending} 片段...', flush=True)
                if not self._cancel_job:
                    delay = min(
                        retry_base_delay * round_num,
                        retry_max_delay)
                    if delay > 0:
                        time.sleep(delay)

        self._t2_executor = None
        spent = time.time() - self._speed_start
        if not self._cancel_job:
            final_pending = len(self._pending_set)
            if final_pending == 0:
                print(f'\n爬取完成！花費 {spent/60:.1f} 分鐘', flush=True)
            else:
                print(f'\n爬取結束，{final_pending} 個片段失敗', flush=True)

    def _prepareCrawl(self):
        self._pending_set = set()
        for i, url in enumerate(self._tsList):
            saveName = self._seg_savename(i)
            if not os.path.exists(saveName):
                self._pending_set.add((i, url))
        if self._pending_set:
            self._startCrawl()

    def download_image(self):
        if not self.is_target_image_exist():
            self._create_dest_folder()
            try:
                response = _http_get(self._imageUrl, self._m3u8_headers(), timeout=15)
                if response.status_code != 200:
                    return None
                with open(self._get_image_savename(), 'wb') as fs:
                    fs.write(response.content)
            except Exception:
                return None
        return self._get_image_savename()

    def cleanup_temp(self):
        try:
            temp = getattr(self, '_temp_folder', None)
            dest = getattr(self, '_dest_folder', None)
            if (temp and os.path.isdir(temp) and
                    os.path.abspath(temp) != os.path.abspath(dest or '')):
                shutil.rmtree(temp, ignore_errors=True)
        except Exception:
            pass

    def start_download(self):
        if self._cancel_job:
            return False
        self._cancel_job = False
        self._create_dest_folder()
        self.download_image()
        if not self.is_target_video_exist():
            self._create_temp_folder()
            self._create_m3u8()
            if not self._cancel_job:
                self._prepareCrawl()
            if not self._cancel_job and not self._pending_set:
                merged = self._mergeMp4Chunks()
                if not merged and not self._cancel_job:
                    raise Exception("merge/publish failed")
            elif not self._cancel_job:
                pending = len(self._pending_set)
                raise DownloadIncompleteError(pending)
        else:
            print("檔案已存在!!", flush=True)

        return not self._cancel_job

    def cancel_download(self, cleanup=True):
        print("\n取消下載....", flush=True)
        self._cancel_job = True
        if self._t2_executor:
            try: self._t2_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError: self._t2_executor.shutdown(wait=False)
            self._t2_executor = None
        if self._t_executor:
            try: self._t_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError: self._t_executor.shutdown(wait=False)
            self._t_executor = None
        proc = getattr(self, '_ffmpeg_proc', None)
        if proc is not None:
            try: proc.kill()
            except Exception: pass
        if cleanup:
            self.cleanup_temp()
        print("\n下載已取消", flush=True)

    def begin_concurrent_download(self):
        self._t_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._t_future = self._t_executor.submit(self.start_download)

    def is_concurrent_dowload_completed(self):
        if not self._t_future or not self._t_future.done():
            return False
        self._t_future = None
        return True

    def end_concurrent_download(self):
        if self._t_executor:
            self._t_executor.shutdown(wait=False)
            self._t_executor = None


class SiteUrlList_M3U8:
    def getLinks(self): return self.links
    def getLinkDescs(self): return self.linkDescriptions
    def getListType(self): return self.listType
    def getTotalLinks(self): return self.totalLinks
    def getTotalPages(self): return self.totalPages
    def getCurrentPage(self): return self.currentPage
    def getSortType(self): return self.sortType
    def isVaildLinks(self): return False if self.islist is None else True
