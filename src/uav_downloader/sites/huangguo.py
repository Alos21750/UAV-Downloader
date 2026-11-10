"""Public Huangguo short-drama metadata and ordinary HLS support.

Only the public page payload is interpreted, as JSON. No private API, account,
challenge solving, player-code evaluation, or dedicated face-swap discovery.
Signed playback URLs/headers stay in the job, never in persisted browser items.
"""

import ipaddress
import copy
import hashlib
import json
import re
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import CancelledError
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from uav_downloader.core import config
from uav_downloader.core.ssl import SharedSSLAdapter
from uav_downloader.core.video_identity import huangguo_video_code
from uav_downloader.i18n import sites as site_i18n
from uav_downloader.sites.base import M3U8Crawler, MirrorsBlockedError


HUANGGUO_ROOT = 'https://huangguoai.com'
_HOSTS = frozenset({'huangguoai.com', 'www.huangguoai.com'})
_VIDEO_PATH = re.compile(r'/video/([1-9][0-9]*)(?:/ep-([1-9][0-9]*))?/?\Z')
_LIST_PATH = re.compile(
    r'/(?:ai-duanju|ai-manju)(?:/[1-9][0-9]*)?/?\Z|'
    r'/search/video/[^/]+(?:/[1-9][0-9]*)?/?\Z')
_local = threading.local()
_TAIPEI = timezone(timedelta(hours=8))
_MAX_SERIES_PER_PAGE = 100
_MAX_EPISODES_PER_SERIES = 1000
_MAX_EPISODES_PER_PAGE = 5000
_MAX_PAGE_EXPANSION_SECONDS = 120
_METADATA_TTL = 120
_METADATA_MAX_SERIES = 128
_METADATA_MAX_EPISODES = 4000
_METADATA_MAX_BYTES = 2 * 1024 * 1024
_metadata_cache = OrderedDict()
_metadata_inflight = {}
_metadata_lock = threading.Lock()


class HuangguoTransientError(RuntimeError):
    """Retryable public-server failure, distinct from access challenges."""
    def __init__(self, status=None, retry_after=None):
        self.status_code = status
        self.retry_after = retry_after
        detail = f'HTTP {status}' if status else 'network timeout or connection error'
        super().__init__(f'Huangguo temporarily unavailable ({detail}); retry later')


class _MetadataDeadline(TimeoutError):
    """One caller's deadline, not a shared upstream failure."""


@contextmanager
def _operation(cancel_check=None, deadline=None):
    previous = getattr(_local, 'control', None)
    checks = list(previous['checks']) if previous else []
    if cancel_check is not None:
        checks.append(cancel_check)
    if previous and previous['deadline'] is not None:
        deadline = min(deadline, previous['deadline']) if deadline is not None else previous['deadline']
    _local.control = {
        'checks': checks, 'deadline': deadline,
        'proxy': previous['proxy'] if previous else copy.deepcopy(config.proxy_request_kwargs()),
    }
    try:
        _check_control()
        yield
    finally:
        _local.control = previous


def _check_control():
    control = getattr(_local, 'control', None)
    if not control:
        return
    if any(check() for check in control['checks']):
        raise CancelledError('Huangguo metadata request cancelled')
    if control['deadline'] is not None and time.monotonic() >= control['deadline']:
        raise _MetadataDeadline('Huangguo metadata request timed out; retry the page')


def _proxy_kwargs():
    control = getattr(_local, 'control', None)
    return copy.deepcopy(control['proxy'] if control else config.proxy_request_kwargs())


def _proxy_scope():
    return hashlib.sha256(json.dumps(_proxy_kwargs(), sort_keys=True).encode()).digest()


def _wait_controlled(seconds):
    end = time.monotonic() + seconds
    while True:
        _check_control()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _retry_after(value):
    """Parse server cooldown; callers never shorten it to retry prematurely."""
    try:
        if str(value).strip().isdigit():
            return min(int(value), 86400)
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            return None
        return max(0, min(86400, (parsed - datetime.now(timezone.utc)).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return None


def _metadata(key, revision, loader):
    """Cache only parsed public episode rows, never HTML/player credentials."""
    key = (_proxy_scope(), key)
    flight_key = (key, revision)
    while True:
        _check_control()
        now = time.monotonic()
        with _metadata_lock:
            cached = _metadata_cache.get(key)
            if (cached and now < cached['expires'] and
                    (revision is None or revision == cached['revision'])):
                _metadata_cache.move_to_end(key)
                return copy.deepcopy(cached['items'])
            flight = _metadata_inflight.get(flight_key)
            owner = flight is None
            if owner:
                flight = {'event': threading.Event(), 'items': None, 'error': None,
                          'cancelled': False}
                _metadata_inflight[flight_key] = flight
        if not owner:
            while not flight['event'].wait(0.1):
                _check_control()
            _check_control()
            if flight['cancelled']:
                continue  # The other caller stopped; this caller may still proceed.
            if flight['error']:
                raise flight['error']
            return copy.deepcopy(flight['items'])
        try:
            items = loader()
            _check_control()
            # These are already explicit metadata dictionaries, not raw JSON.
            saved = copy.deepcopy(items)
            size = len(json.dumps(saved, ensure_ascii=False).encode('utf-8'))
            with _metadata_lock:
                newer = _metadata_cache.get(key)
                if (size <= _METADATA_MAX_BYTES and len(saved) <= _METADATA_MAX_EPISODES
                        and (not newer or newer['started'] <= now)):
                    _metadata_cache[key] = {'items': saved, 'bytes': size,
                                            'revision': revision, 'started': now,
                                            'expires': time.monotonic() + _METADATA_TTL}
                    _metadata_cache.move_to_end(key)
                    while (_metadata_cache and (
                            len(_metadata_cache) > _METADATA_MAX_SERIES or
                            sum(len(v['items']) for v in _metadata_cache.values()) > _METADATA_MAX_EPISODES or
                            sum(v['bytes'] for v in _metadata_cache.values()) > _METADATA_MAX_BYTES)):
                        _metadata_cache.popitem(last=False)
                flight['items'] = saved
            return items
        except BaseException as exc:
            # Cleanup also on KeyboardInterrupt/SystemExit; never mask them.
            if isinstance(exc, (CancelledError, _MetadataDeadline, KeyboardInterrupt, SystemExit)):
                flight['cancelled'] = True
            else:
                flight['error'] = exc
            raise
        finally:
            with _metadata_lock:
                _metadata_inflight.pop(flight_key, None)
                flight['event'].set()


def _parts(url):
    """Reject ambiguous URLs before urlsplit's control-character cleanup."""
    if not isinstance(url, str) or len(url) > 8192:
        return None
    if re.search(r'[\s\\\x00-\x1f\x7f]', url):
        return None
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or not p.hostname or p.username is not None or
                p.password is not None or p.port not in (None, 443)):
            return None
        return p
    except ValueError:
        return None


def _video_parts(url):
    if not huangguo_video_code(url):
        return None
    p = _parts(url)
    if not p or p.hostname not in _HOSTS or p.query or p.fragment:
        return None
    match = _VIDEO_PATH.fullmatch(p.path)
    if not match:
        return None
    return match.group(1), int(match.group(2) or 1)


def canonical_url(url):
    parts = _video_parts(url)
    if not parts:
        return ''
    series_id, episode = parts
    return (f'{HUANGGUO_ROOT}/video/{series_id}/' +
            (f'ep-{episode}/' if episode > 1 else ''))


def _public_https_url(url):
    p = _parts(url)
    if not p or p.fragment:
        return False
    host = p.hostname.lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
                    and '.' in host and '..' not in host)


def _verify_public_destination(url):
    if not _public_https_url(url):
        raise ValueError('Huangguo requires a public HTTPS destination')
    try:
        addresses = socket.getaddrinfo(urlsplit(url).hostname, 443,
                                      type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global
                                for a in addresses):
            raise ValueError('non-public address')
    except (OSError, ValueError):
        raise ValueError('Huangguo destination could not be verified') from None


def _session(kind='page'):
    name = 'huangguo_' + kind
    session = getattr(_local, name, None)
    if session is None:
        session = requests.Session()
        session.trust_env = False  # Never pick up a user's netrc credentials.
        session.mount('https://', SharedSSLAdapter())
        setattr(_local, name, session)
    return session


def _get(url, *, header_factory=None, timeout=20, page=False):
    """Bound redirects; rebuild headers after validating every destination."""
    session = _session('page' if page else 'media')
    transient_attempt = 0
    for _ in range(6):
        _check_control()
        if page and not (canonical_url(url) or HuangguoBrowser.is_listing_url(url)):
            raise ValueError('Unsupported Huangguo page redirect')
        _verify_public_destination(url)
        headers = dict(config.headers)
        if header_factory:
            headers.update(header_factory(url))
        while True:
            _check_control()
            control = getattr(_local, 'control', None)
            request_timeout = timeout
            if control and control['deadline'] is not None:
                request_timeout = max(0.01, min(timeout, control['deadline'] - time.monotonic()))
            try:
                response = session.get(url, headers=headers, timeout=request_timeout,
                                       allow_redirects=False, stream=page,
                                       **_proxy_kwargs())
            except (requests.Timeout, requests.ConnectionError) as exc:
                _check_control()
                if isinstance(exc, requests.exceptions.SSLError):
                    raise RuntimeError('Huangguo TLS verification failed') from None
                if not page or transient_attempt >= 2:
                    raise HuangguoTransientError() from None
                _wait_controlled(1 + transient_attempt)
                transient_attempt += 1
                continue
            except requests.RequestException:
                _check_control()
                raise RuntimeError('Huangguo public request failed') from None
            _check_control_after_response(response)
            if 'challenge' in str(response.headers.get('cf-mitigated', '')).lower():
                response.close()
                raise MirrorsBlockedError('Huangguo requires manual public-site verification')
            if response.status_code not in (408, 425, 429, 500, 502, 503, 504):
                break
            status = response.status_code
            delay = _retry_after(response.headers.get('Retry-After'))
            response.close()
            if not page or transient_attempt >= 2 or (delay is not None and delay > 10):
                raise HuangguoTransientError(status, delay)
            _wait_controlled(delay if delay is not None else 1 + transient_attempt)
            transient_attempt += 1
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('Location', '')
            response.close()
            if not location:
                raise ValueError('Huangguo returned an invalid redirect')
            url = urljoin(url, location)
            continue
        if response.status_code == 403:
            response.close()
            raise RuntimeError('Huangguo public access was refused (HTTP 403)')
        return response
    raise ValueError('Huangguo redirect limit exceeded')


def _check_control_after_response(response):
    try:
        _check_control()
    except BaseException:
        response.close()
        raise


def _page_soup(url):
    response = _get(url, page=True)
    try:
        if response.status_code != 200:
            raise ValueError(f'Huangguo page unavailable (HTTP {response.status_code})')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            _check_control()
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise ValueError('Huangguo page exceeds metadata limit')
            chunks.append(chunk)
        body = b''.join(chunks)
        soup = BeautifulSoup(body, 'html.parser')
        title = soup.title.get_text(' ', strip=True).lower() if soup.title else ''
        if 'just a moment' in title or soup.select_one('#challenge-form'):
            raise MirrorsBlockedError('Huangguo requires manual public-site verification')
        return soup
    except requests.RequestException:
        _check_control()
        raise HuangguoTransientError() from None
    finally:
        response.close()


def _initial_data(soup, url):
    script = soup.select_one('script#videoInitialData[type="application/json"]')
    try:
        text = script.string or script.get_text() if script else ''
        if len(text) > 1024 * 1024:
            raise ValueError('payload too large')
        data = json.loads(text)
        series_id, episode = _video_parts(url)
        if (not isinstance(data, dict) or str(data.get('id')) != series_id or
                str(data.get('ep')) != str(episode)):
            raise ValueError('episode mismatch')
        return data
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Huangguo public episode metadata is invalid') from None


def _clean(value):
    # One hostile title repeated across 1,000 episodes must not amplify a
    # bounded HTML response into gigabytes of metadata/cache serialization.
    return ' '.join(str(value or '').split())[:1000]


def _revision(value):
    text = str(value or '')
    return text if re.fullmatch(r'[1-9][0-9]{0,19}', text) else None


def _date(value):
    try:
        if isinstance(value, bool):
            return ''
        if isinstance(value, (float, int)) or str(value).isdigit():
            if int(value) <= 0:
                return ''
            return datetime.fromtimestamp(int(value), _TAIPEI).date().isoformat()
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (parsed.astimezone(_TAIPEI) if parsed.tzinfo else parsed).date().isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return ''


def _detail_date(soup, data):
    # The initial payload's time is the SERIES creation time, not episode time.
    # Only a verified series update can be used for Watcher date comparisons.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            raw = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        nodes = raw if isinstance(raw, list) else [raw]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get('@graph', [])
            for item in [node] + (graph if isinstance(graph, list) else []):
                if isinstance(item, dict):
                    kind = item.get('@type', [])
                    kinds = [kind] if isinstance(kind, str) else kind
                    if not isinstance(kinds, list) or not any(
                            isinstance(k, str) and k in {'WebPage', 'VideoObject'} for k in kinds):
                        continue
                    value = _date(item.get('dateModified'))
                    if value:
                        return value
    return ''


def _parse_videos(soup, listing_url):
    videos, seen = [], set()
    if not soup.select_one('main .hg-list-page'):
        raise ValueError('Huangguo listing layout is unrecognized')
    for card in soup.select('main .hg-list-page .hg-card-grid .hg-drama-card'):
        grid = card.find_parent(class_='hg-card-grid')
        if grid and grid.get('data-channel-panel') not in (None, 'latest'):
            continue
        a = card.select_one('a.hg-drama-card__cover-link[href]')
        url = canonical_url(urljoin(HUANGGUO_ROOT, a.get('href', ''))) if a else ''
        if not url or url in seen:
            continue
        seen.add(url)
        if len(seen) > _MAX_SERIES_PER_PAGE:
            raise ValueError('Huangguo listing exceeds the series safety limit')
        title = card.select_one('.hg-drama-card__title a')
        updated = card.select_one('[data-ep-at]')
        date = _date(updated.get('data-ep-at')) if updated else ''
        videos.append({
            'url': url, 'title': _clean(title.get_text(' ', strip=True) if title else ''),
            'thumbnail': '', 'duration': '', 'date': date, 'author': '',
            '_date_kind': 'series_updated' if date else 'unknown',
            '_series_revision': _revision(updated.get('data-ep-at')) if updated else None,
            '_source_listing_url': listing_url,
        })
    return videos


class SiteHuangguo(M3U8Crawler):
    website_pattern = r'https://(?:www\.)?huangguoai\.com/video/[1-9][0-9]*(?:/ep-[1-9][0-9]*)?/?\Z'
    website_dirname_pattern = website_pattern

    @classmethod
    def validate_url(cls, url):
        return huangguo_video_code(url) or None

    def get_url_infos(self):
        self._url = canonical_url(self._url)
        self._m3u8url = None
        self._cdn = {}
        soup = _page_soup(self._url)
        data = _initial_data(soup, self._url)
        source = data.get('videoSrc')
        if (not _public_https_url(source) or
                not urlsplit(source).path.lower().endswith('.m3u8')):
            raise ValueError('Huangguo has no public HTTPS HLS source')
        if data.get('videoApi') or data.get('videoKey') or data.get('videoIv'):
            raise ValueError('Huangguo private player sources are not supported')
        title = _clean(data.get('title'))
        if not title:
            raise ValueError('Huangguo episode title is missing')
        self._targetName = f'{self.validate_url(self._url)} {title}'
        # Huangguo thumbnails use a separate encrypted-image protocol. Do not
        # save those bytes under a misleading .jpg extension or fetch previews.
        self._imageUrl = None
        key, value, app = (str(data.get(k) or '') for k in
                           ('cdnAuthKey', 'cdnAuthValue', 'cdnApp'))
        if (key.lower() == 'x-cdn-auth' and value and app and
                len(value) <= 2048 and len(app) <= 64 and
                not re.search(r'[\x00-\x20\x7f]', value + app)):
            self._cdn = {'auth': value, 'app': app,
                         'manifest': data.get('cdnM3u8Enabled') is True,
                         'other': data.get('cdnOtherEnabled') is True}
        elif data.get('cdnM3u8Enabled') or data.get('cdnOtherEnabled'):
            raise ValueError('Huangguo CDN header metadata is invalid')
        source_parts = urlsplit(source)
        self._media_origin = (source_parts.hostname.lower(), source_parts.port or 443)
        self._m3u8url = source

    def _media_headers(self, url):
        p = _parts(url)
        if not p:
            raise ValueError('Huangguo requires HTTPS media')
        headers = {'Referer': self._url, 'Origin': HUANGGUO_ROOT}
        cdn = self._cdn
        is_manifest = p.path.lower().endswith('.m3u8')
        is_other = p.path.lower().endswith(('.ts', '.m4s', '.key'))
        if ((p.hostname.lower(), p.port or 443) == self._media_origin and
                ((is_manifest and cdn.get('manifest')) or
                 (is_other and cdn.get('other')))):
            headers.update({'x-cdn-auth': cdn['auth'], 'x-cdn-app': cdn['app']})
        return headers

    def _media_get(self, url, timeout=20):
        return _get(url, header_factory=self._media_headers, timeout=timeout)

    def _load_m3u8(self, url):
        try:
            playlist = super()._load_m3u8(url)
        except MirrorsBlockedError:
            raise
        except Exception:
            # The base parser's diagnostics include URLs. Signed queries are
            # transient access material and must not appear in logs/UI errors.
            raise ValueError('Huangguo manifest could not be loaded') from None
        if any(getattr(segment, 'init_section', None) or
               urlsplit(segment.uri).path.lower().endswith(('.m4s', '.mp4'))
               for segment in playlist.segments):
            raise ValueError('Huangguo fragmented MP4 HLS is not supported; MPEG-TS is required')
        return playlist


class HuangguoBrowser:
    _url_root = HUANGGUO_ROOT
    CATEGORIES = (
        ('AI 短劇', HUANGGUO_ROOT + '/ai-duanju/'),
        ('AI 漫劇', HUANGGUO_ROOT + '/ai-manju/'),
    )
    HOMEPAGE_SECTIONS = CATEGORIES

    @classmethod
    def is_listing_url(cls, url):
        p = _parts(url)
        return bool(p and p.hostname in _HOSTS and not p.query and not p.fragment
                    and _LIST_PATH.fullmatch(p.path))

    _listing_url_allowed = is_listing_url

    @classmethod
    def fetch_categories(cls):
        return [{'name': site_i18n.loc(site_i18n.CATEGORY_I18N, url, name),
                 'url': url, 'count': 0} for name, url in cls.CATEGORIES]

    @classmethod
    def clear_metadata_cache(cls):
        with _metadata_lock:
            _metadata_cache.clear()

    @classmethod
    def fetch_episodes(cls, url, *, cancel_check=None, deadline=None, revision=None):
        url = canonical_url(url)
        if not url:
            raise ValueError('Unsupported Huangguo episode URL')
        series_id, _ = _video_parts(url)
        root = f'{HUANGGUO_ROOT}/video/{series_id}/'
        with _operation(cancel_check, deadline):
            return _metadata(root, _revision(revision), lambda: cls._fetch_episodes_uncached(root))

    @classmethod
    def _fetch_episodes_uncached(cls, url):
        soup = _page_soup(url)
        data = _initial_data(soup, url)
        series_id, _ = _video_parts(url)
        title = _clean(data.get('title'))
        date = _detail_date(soup, data)
        items, seen = [], set()
        for anchor in soup.select('[data-web-play] a.hg-web-play__ep[data-ep-id][href]'):
            _check_control()
            episode_url = canonical_url(urljoin(url, anchor.get('href', '')))
            parts = _video_parts(episode_url)
            if (not parts or parts[0] != series_id or
                    str(parts[1]) != anchor.get('data-ep-id') or episode_url in seen):
                continue
            seen.add(episode_url)
            if len(seen) > _MAX_EPISODES_PER_SERIES:
                raise ValueError('Huangguo episode list exceeds the safety limit')
            items.append({'url': episode_url, 'title': f'{title} - EP {parts[1]}',
                          'thumbnail': '', 'duration': '', 'date': date, 'author': '',
                          '_date_kind': 'series_updated' if date else 'unknown',
                          'episode': parts[1], 'series_id': series_id,
                          '_source_listing_url': url})
        if not items:
            raise ValueError('Huangguo has no public playable episode links')
        return sorted(items, key=lambda item: item['episode'])

    @classmethod
    def fetch_page(cls, url, *, cancel_check=None, deadline=None):
        if not cls.is_listing_url(url):
            return []
        limit = time.monotonic() + _MAX_PAGE_EXPANSION_SECONDS
        with _operation(cancel_check, min(limit, deadline) if deadline is not None else limit):
            return cls._fetch_page(url)

    @classmethod
    def _fetch_page(cls, url):
        cards = _parse_videos(_page_soup(url), url)
        items, seen = [], set()
        for card in cards:
            _check_control()
            for item in cls.fetch_episodes(card['url'], revision=card.get('_series_revision') or None):
                _check_control()
                if item['url'] not in seen:
                    seen.add(item['url'])
                    if len(seen) > _MAX_EPISODES_PER_PAGE:
                        raise ValueError('Huangguo expanded page exceeds the episode safety limit')
                    items.append({**item, 'date': card['date'] or item['date'],
                                  '_date_kind': (card['_date_kind'] if card['date']
                                                 else item['_date_kind']),
                                  '_source_listing_url': url})
        return items

    @classmethod
    def fetch_video_date(cls, url, *, cancel_check=None, deadline=None):
        url = canonical_url(url)
        if not url:
            return ''
        episodes = cls.fetch_episodes(url, cancel_check=cancel_check, deadline=deadline)
        return episodes[0]['date'] if episodes else ''

    @classmethod
    def page_url(cls, base, page):
        if not cls.is_listing_url(base):
            raise ValueError('Unsupported Huangguo listing URL')
        path = urlsplit(base).path.rstrip('/')
        parts = path.split('/')
        # A numeric search term is not a page number.
        if ((parts[1] == 'search' and len(parts) == 5) or
                (parts[1] != 'search' and len(parts) == 3)):
            path = path.rsplit('/', 1)[0]
        return HUANGGUO_ROOT + path + ('/' if int(page) <= 1 else f'/{int(page)}/')

    @classmethod
    def search_url(cls, query):
        query = str(query or '').strip()
        if not query:
            return cls.CATEGORIES[0][1]
        return HUANGGUO_ROOT + '/search/video/' + quote(query, safe='') + '/'

    @classmethod
    def search(cls, query):
        return cls.fetch_page(cls.search_url(query))
