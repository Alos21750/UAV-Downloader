#!/usr/bin/env python
# coding: utf-8

import re
import html
import os
import time
import concurrent.futures
import base64
import codecs
import json
import ipaddress
import cloudscraper
try:
    from curl_cffi import requests as cffi_requests
    _use_cffi = True
except ImportError:
    _use_cffi = False
import threading as _threading
from urllib.parse import quote, urlsplit, urljoin, parse_qsl, urlencode
from uav_downloader.sites.base import *
from uav_downloader.sites.base import _get_session
from uav_downloader.sites.missav import _unpack_js_eval
from bs4 import BeautifulSoup
from uav_downloader.i18n import sites as site_i18n
from uav_downloader.core import config


SUPREMEJAV = 'https://lk1.supremejav.com/supjav.php?c={}'
_BLOCKED_MSG = "所有鏡像都被 Cloudflare 阻擋（可能是你的網路/IP 信譽問題，請改用 VPN 或不同網路）"
_DIRECT_RANGE_WORKERS = 4
_DIRECT_RANGE_RETRIES = 4
_DIRECT_RETRY_BASE_DELAY = 1.0

_browser_scraper = None
_browser_scraper_lock = _threading.Lock()

def _make_scraper():
    """Fresh scraper: curl_cffi (Cloudflare-capable) if available, else cloudscraper."""
    if _use_cffi:
        return cffi_requests.Session(impersonate='chrome')
    return cloudscraper.create_scraper(browser=request_headers, delay=10)


def _extract_tv_link(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    for a in soup.select('a[data-link]'):
        if a.get_text(strip=True) == 'TV':
            return a.get('data-link', '')
    return None


def _extract_m3u8(body):
    body = body.replace('\\/', '/')
    m = re.search(r'urlPlay[\s=:\'"]+(?P<u>https?://[^\s\'"\\]+\.m3u8[^\s\'"\\]*)', body)
    if m:
        return m.group('u')
    m = re.search(r'https?://[^\s\'"\\]+\.m3u8[^\s\'"\\]*', body)
    return m.group(0) if m else None


def _extract_title(soup):
    h1 = soup.find('h1')
    return h1.get_text(strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else '')


def _strip_fake_header(data):
    """SupJav segments are MPEG-TS hidden behind a fake PNG header. Return the stream
    from the first valid MPEG-TS sync (0x47 on a 188-byte stride). Plain TS is returned unchanged."""
    if data[:1] == b'\x47':
        return data
    limit = min(len(data) - 188 * 4 - 1, 8000)
    i = 0
    while 0 <= i <= limit:
        j = data.find(b'\x47', i)
        if j < 0 or j > limit:
            break
        if all(data[j + 188 * n] == 0x47 for n in range(5)):
            return data[j:]
        i = j + 1
    return b''


def _server_links(html_text):
    """Return {SERVER_NAME: data_link} for every .btn-server anchor on a SupJav
    video page, including EVS, VAS and LUC added in 2026."""
    soup = BeautifulSoup(html_text, 'html.parser')
    out = {}
    for a in soup.select('a.btn-server[data-link]'):
        name = a.get_text(strip=True).upper()
        link = a.get('data-link', '')
        if name and link and name not in out:
            out[name] = link
    return out


def _streamtape_direct_url(html_text):
    """Extract the direct progressive-MP4 URL from a Streamtape embed page.
    Streamtape overwrites #robotlink via JS:  'PREFIX' + ('SUFFIX').substring(a)[.substring(b)]
    — the static div text is a decoy; only the JS-computed value carries the live token."""
    m = re.search(
        r"getElementById\(\s*['\"]robotlink['\"]\s*\)\.innerHTML\s*=\s*"
        r"['\"]([^'\"]*)['\"]\s*\+\s*(?:['\"]{2}\s*\+\s*)?"
        r"\(\s*['\"]([^'\"]*)['\"]\s*\)((?:\.substring\(\s*\d+\s*\))+)",
        html_text)
    if not m:
        return None
    prefix, suffix, subs = m.group(1), m.group(2), m.group(3)
    s = suffix
    for off in re.findall(r'substring\(\s*(\d+)\s*\)', subs):
        s = s[int(off):]
    link = (prefix + s).lstrip('/')
    if 'get_video' not in link:
        return None
    return 'https://' + link


def _extract_packed_m3u8(html_text):
    """Read FST/EVS/LUC packed player data without executing remote JavaScript."""
    for script in re.findall(r'<script[^>]*>(.*?)</script>', html_text, re.DOTALL):
        if 'eval(function' not in script or 'm3u8' not in script:
            continue
        unpacked = _unpack_js_eval(script)
        if not unpacked:
            continue
        match = re.search(
            r"https?://[^'\"\\;\s]+\.m3u8[^'\"\\;\s]*",
            unpacked,
        )
        if match:
            return match.group(0)
    return None


def _public_https_url(value):
    """Reject credentials and explicit local destinations in provider data.

    This is a URL-level guard; it does not replace a deployment's DNS/network
    policy. Provider CDN names can change without an application update.
    """
    if not isinstance(value, str):
        return None
    value = html.unescape(value.replace('\\/', '/')).strip()
    try:
        parts = urlsplit(value)
        hostname = (parts.hostname or '').lower().rstrip('.')
        hostname = hostname.encode('idna').decode('ascii')
        parts.port  # Reject malformed ports before handing the URL to HTTP.
        if (parts.scheme != 'https' or not hostname
                or parts.username is not None or parts.password is not None
                or '\\' in parts.netloc or '%' in hostname):
            return None
        if hostname == 'localhost' or hostname.endswith(('.localhost', '.local', '.internal')):
            return None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            if '.' not in hostname or all(
                    re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', label)
                    for label in hostname.split('.')):
                return None
        else:
            if not address.is_global or address.is_multicast:
                return None
        return value
    except (ValueError, UnicodeError):
        return None


def _valid_hls_url(value):
    value = _public_https_url(value)
    return value if value and urlsplit(value).path.lower().endswith('.m3u8') else None


def _fetch_embed(scraper, url, referer):
    """Validate each HTTP redirect before fetching it; cap provider hops."""
    for _ in range(6):
        if not _public_https_url(url):
            raise ValueError('Unsafe playback source URL')
        response = scraper.get(
            url, headers={'Referer': referer}, timeout=25,
            allow_redirects=False, **config.proxy_request_kwargs())
        if not _public_https_url(str(response.url)):
            raise ValueError('Unsafe playback source response URL')
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get('Location') or response.headers.get('location')
        if not location:
            return response
        referer = str(response.url)
        url = urljoin(referer, location)
        close = getattr(response, 'close', None)
        if callable(close):
            close()
    raise ValueError('Too many playback source redirects')


def _extract_voe_m3u8(html_text):
    """Decode VOE's JSON player config; ignore the deliberately unrelated source.

    The provider's loader performs ROT13, removes noise pairs, Base64-decodes,
    subtracts three, reverses, then decodes JSON. No remote code is evaluated.
    """
    soup = BeautifulSoup(html_text, 'html.parser')
    for script in soup.select('script[type="application/json"]'):
        try:
            payload = json.loads(script.get_text())
            if not isinstance(payload, list) or not payload or not isinstance(payload[0], str):
                continue
            encoded = codecs.decode(payload[0], 'rot13')
            for noise in ('@$', '^^', '~@', '%?', '*~', '!!', '#&', '_'):
                encoded = encoded.replace(noise, '')
            shifted = base64.b64decode(encoded, validate=True).decode('ascii')
            reversed_payload = ''.join(chr(ord(char) - 3) for char in shifted)[::-1]
            data = json.loads(base64.b64decode(reversed_payload, validate=True))
            if isinstance(data, dict):
                result = _valid_hls_url(data.get('source'))
                if result:
                    return result
        except (ValueError, TypeError, UnicodeError):
            continue
    return None


def _content_range(value):
    match = re.fullmatch(r'bytes\s+(\d+)-(\d+)/(\d+)', str(value or '').strip(), re.I)
    if not match:
        return None
    start, end, total = (int(part) for part in match.groups())
    if start > end or end >= total:
        return None
    return start, end, total


def _split_byte_ranges(total, workers=_DIRECT_RANGE_WORKERS):
    workers = min(max(1, int(workers)), total)
    chunk, remainder = divmod(total, workers)
    ranges = []
    start = 0
    for index in range(workers):
        size = chunk + (1 if index < remainder else 0)
        end = start + size - 1
        ranges.append((start, end))
        start = end + 1
    return ranges


def _parse_videos(soup):
    videos = []
    seen = set()
    for post in soup.select('div.post'):
        a = post.select_one('a[href*=".html"]')
        if not a:
            continue
        video_url = a['href']
        if video_url in seen:
            continue
        seen.add(video_url)
        title = html.unescape(a.get('title') or a.get_text(strip=True))
        img = post.find('img')
        thumbnail = (img.get('data-original') or img.get('data-src') or '') if img else ''
        if img and not thumbnail:
            src = img.get('src') or ''
            if not src.startswith('data:'):
                thumbnail = src
        meta = post.select_one('div.meta')
        date_match = re.search(
            r'(?<!\d)(20\d{2}/\d{1,2}/\d{1,2})(?!\d)',
            meta.get_text(' ', strip=True) if meta else '',
        )
        videos.append({
            'url': video_url,
            'title': title,
            'thumbnail': thumbnail,
            'duration': '',
            'date': date_match.group(1) if date_match else '',
        })
    return videos


class SiteSupJav(M3U8Crawler):
    website_pattern = r'https://supjav\.com/(?:(?:zh|ja)/)?\d+\.html$'
    website_dirname_pattern = r'https://supjav\.com/(?:(?:zh|ja)/)?(\d+)\.html$'
    direct_site_name = 'SupJav'
    direct_default_referer = 'https://supjav.com/'

    def _transform_segment(self, data):
        return _strip_fake_header(data)

    _direct_url = None
    _direct_referer = None

    def _resolve_server(self, scraper, name, link):
        response = _fetch_embed(
            scraper, SUPREMEJAV.format(quote(link[::-1], safe='')),
            'https://supjav.com/')
        if response.status_code != 200:
            raise RuntimeError(f'{name} returned HTTP {response.status_code}')
        if name == 'VOE':
            # VOE sends a small literal JS redirect page before its player page.
            # Follow only this known redirect shape, with a bounded hop count.
            for _ in range(2):
                if _extract_voe_m3u8(response.text):
                    break
                match = re.search(r"window\.location\.href\s*=\s*['\"](https://[^'\"]+)['\"]", response.text)
                if not match:
                    break
                redirect = match.group(1)
                parts = urlsplit(redirect)
                if not _public_https_url(redirect) or not re.fullmatch(r'/e/[A-Za-z0-9]+/?', parts.path):
                    break
                response = _fetch_embed(scraper, redirect, str(response.url))
                if response.status_code != 200:
                    raise RuntimeError(f'VOE returned HTTP {response.status_code}')
        embed_url = str(response.url)
        parts = urlsplit(embed_url)
        origin = f'{parts.scheme}://{parts.netloc}'
        source_headers = {'Referer': embed_url, 'Origin': origin}
        if name == 'ST':
            return _public_https_url(_streamtape_direct_url(response.text)), source_headers
        if name == 'VAS':
            filecode = parts.path.rstrip('/').rsplit('/', 1)[-1]
            if not re.fullmatch(r'[A-Za-z0-9]+', filecode) or '/api/stream' not in response.text:
                return None, source_headers
            query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k != 'filecode'])
            api_url = origin + '/api/stream' + ('?' + query if query else '')
            api_response = scraper.post(
                api_url, headers=source_headers,
                json={'filecode': filecode, 'device': 'web', 'codecs': []},
                timeout=25, allow_redirects=False, **config.proxy_request_kwargs(),
            )
            if api_response.status_code != 200:
                raise RuntimeError(f'VAS returned HTTP {api_response.status_code}')
            data = json.loads(api_response.text)
            return _valid_hls_url(data.get('streaming_url') if isinstance(data, dict) else None), source_headers
        if name == 'VOE':
            return _extract_voe_m3u8(response.text), source_headers
        if name == 'TV':
            return _valid_hls_url(_extract_m3u8(response.text)), {'Referer': 'https://supjav.com/'}
        return _valid_hls_url(_extract_packed_m3u8(response.text)), source_headers

    def get_url_infos(self):
        self._direct_url = None
        self._direct_referer = None
        self._hls_sources = []
        with _make_scraper() as scraper:
            def _validate(resp):
                return 'data-link' in resp.text
            htmlfile, host, reason = fetch_with_mirrors(scraper, self._url, 'supjav', _validate, timeout=30)
            if reason == 'blocked':
                raise MirrorsBlockedError(_BLOCKED_MSG)
            if reason != 'ok':
                raise Exception(f"頁面解析失敗（版面改版或影片不存在）: {self._url}")

            soup = BeautifulSoup(htmlfile.text, 'html.parser')
            servers = _server_links(htmlfile.text)
            if not servers:
                raise Exception("此影片沒有可用的伺服器來源（版面改版？）")

            # Keep independent providers as alternatives: a dead embed/CDN must
            # not suppress the new providers present on the same video page.
            for name in ('EVS', 'FST', 'VAS', 'LUC', 'VOE', 'TV', 'ST'):
                if name not in servers:
                    continue
                try:
                    media_url, source_headers = self._resolve_server(scraper, name, servers[name])
                    if not media_url:
                        continue
                    if name == 'ST':
                        self._direct_url = media_url
                        self._direct_referer = source_headers['Referer']
                    elif not any(url == media_url for _, url, _ in self._hls_sources):
                        self._hls_sources.append((name, media_url, source_headers))
                except Exception:
                    # Resolve every remaining source even if one host is blocked.
                    continue
            if not self._direct_url and not self._hls_sources:
                raise Exception("此影片目前無可用下載來源（頁面來源 " + ', '.join(servers) +
                                "；來源失效、存取受限或播放器改版）")

        title = _extract_title(soup)
        self._targetName = html.unescape(title)
        self._imageUrl = None
        self._m3u8url = self._hls_sources[0][1] if self._hls_sources else None
        self._extra_headers = self._hls_sources[0][2] if self._hls_sources else {'Referer': 'https://supjav.com/'}

    def is_url_vaildate(self):
        # The base gate is `True if self._m3u8url` — but a Streamtape source resolves to
        # a direct MP4 (_direct_url) with no m3u8. Without this override the caller would
        # treat the URL as invalid and silently skip it (and __init__ would not sanitize
        # the target filename).
        return bool(self._m3u8url or getattr(self, '_direct_url', None))

    def start_download(self):
        sources = getattr(self, '_hls_sources', None) or (
            [('HLS', self._m3u8url, getattr(self, '_extra_headers', {}))] if self._m3u8url else [])
        last_error = None
        for index, (name, media_url, source_headers) in enumerate(sources):
            self._m3u8url = media_url
            self._extra_headers = source_headers
            try:
                return super().start_download()
            except StorageWriteError:
                raise
            except Exception as exc:
                if self._cancel_job:
                    raise
                last_error = exc
                if index + 1 >= len(sources) and not getattr(self, '_direct_url', None):
                    raise
                print(f'\n[SupJav] {name} failed; trying the next playback source.', flush=True)
                self.cleanup_temp()
        if getattr(self, '_direct_url', None):
            return self._download_direct()
        if last_error:
            raise last_error
        return super().start_download()

    @staticmethod
    def _safe_remove(path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _download_direct(self):
        self._progress_unit = 'bytes'
        if self._cancel_job:
            return False
        self._cancel_job = False
        self._create_dest_folder()
        if self.is_target_video_exist():
            print("檔案已存在!!", flush=True)
            return True
        out = self._get_video_savename()
        part = out + '.part'
        self._safe_remove(part)
        ref = self._direct_referer or self.direct_default_referer
        start = time.time()
        try:
            try:
                ranged = self._download_direct_ranges(part, ref, start)
            except StorageWriteError:
                raise
            except Exception:
                if self._cancel_job:
                    raise
                print(
                    f'\n[{self.direct_site_name}] Parallel ranges failed; '
                    'retrying with one resumable connection.',
                    flush=True)
                self._safe_remove(part)
                ranged = None
            if ranged is None:
                done, total = self._download_direct_serial(part, ref, start)
            else:
                done, total = ranged
        except StorageWriteError:
            # Preserve the partial file; a failing device must not be
            # subjected to a source fallback or another write attempt.
            raise
        except Exception:
            self._safe_remove(part)
            raise
        if self._cancel_job:
            self._safe_remove(part)
            return False
        if total > 0 and done < int(total * 0.98):
            self._safe_remove(part)
            raise Exception("下載不完整（連線中斷？請重試）")
        self._progress_unit = 'postprocess'
        try:
            os.replace(part, out)
        except OSError as exc:
            raise StorageWriteError(out, exc) from exc
        except Exception:
            self._safe_remove(part)
            raise
        print(f"\n下載完成: {os.path.basename(out)}", flush=True)
        return True

    def _download_direct_ranges(self, part, ref, start_time):
        """Use up to four HTTP Range connections when the host supports ranges.
        The shared requests session mounts SharedSSLAdapter, which is required for every
        concurrent HTTPS path in this project. Return None to keep the serial fallback."""
        session = _get_session()
        probe = None
        try:
            probe = session.get(
                self._direct_url,
                headers={'Referer': ref, 'Range': 'bytes=0-0'},
                timeout=60,
                stream=True,
                allow_redirects=True,
                **config.proxy_request_kwargs(),
            )
            info = _content_range(getattr(probe, 'headers', {}).get('content-range'))
            if getattr(probe, 'status_code', 0) != 206 or not info or info[:2] != (0, 0):
                return None
            total = info[2]
        except Exception:
            return None
        finally:
            if probe is not None:
                try:
                    probe.close()
                except Exception:
                    pass

        ranges = _split_byte_ranges(
            total,
            min(
                _DIRECT_RANGE_WORKERS,
                getattr(self, '_max_workers', _DIRECT_RANGE_WORKERS)))
        if len(ranges) == 1:
            return None

        with storage_open(part, 'wb') as target:
            target.truncate(total)

        progress_lock = _threading.Lock()
        stop_event = _threading.Event()
        done = 0

        def _fetch_range(bounds):
            nonlocal done
            range_start, range_end = bounds
            expected = range_end - range_start + 1
            written = 0
            retries = 0
            while written < expected and not self._cancel_job and not stop_event.is_set():
                cursor = range_start + written
                response = None
                try:
                    response = session.get(
                        self._direct_url,
                        headers={'Referer': ref, 'Range': f'bytes={cursor}-{range_end}'},
                        timeout=60,
                        stream=True,
                        allow_redirects=True,
                        **config.proxy_request_kwargs(),
                    )
                    response_range = _content_range(
                        getattr(response, 'headers', {}).get('content-range'))
                    if (getattr(response, 'status_code', 0) != 206 or
                            response_range != (cursor, range_end, total)):
                        raise Exception("直接下載來源未正確回應分段請求")

                    received = 0
                    with storage_open(part, 'r+b', buffering=0) as target:
                        target.seek(cursor)
                        for chunk in response.iter_content(chunk_size=262144):
                            if self._cancel_job or stop_event.is_set():
                                break
                            if not chunk:
                                continue
                            if written + len(chunk) > expected:
                                raise Exception("直接下載分段長度超出預期")
                            speed_limiter.acquire(len(chunk))
                            if target.write(chunk) != len(chunk):
                                raise Exception("直接下載寫入失敗")
                            written += len(chunk)
                            received += len(chunk)
                            with progress_lock:
                                done += len(chunk)
                                current_done = done
                                elapsed = time.time() - start_time
                                speed = current_done / elapsed if elapsed > 0 else 0
                                if self._progress_callback:
                                    self._progress_callback(current_done, total, speed)

                    if self._cancel_job or stop_event.is_set():
                        break
                    if written < expected:
                        detail = "未收到資料" if received == 0 else "連線提前結束"
                        raise Exception(f"直接下載分段{detail}")
                except StorageWriteError:
                    stop_event.set()
                    raise
                except Exception:
                    if self._cancel_job or stop_event.is_set():
                        break
                    retries += 1
                    if retries > _DIRECT_RANGE_RETRIES:
                        raise
                    stop_event.wait(_DIRECT_RETRY_BASE_DELAY * retries)
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass

            if not self._cancel_job and not stop_event.is_set() and written != expected:
                raise Exception("直接下載分段不完整（連線中斷？請重試）")
            return written

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(ranges))
        self._t2_executor = executor
        futures = [executor.submit(_fetch_range, bounds) for bounds in ranges]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
        except Exception:
            stop_event.set()
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True)
            self._t2_executor = None

        if self._cancel_job:
            return done, total
        if done != total:
            raise Exception("直接下載不完整（連線中斷？請重試）")
        return done, total

    def _download_direct_serial(self, part, ref, start_time):
        done = os.path.getsize(part) if os.path.exists(part) else 0
        total = 0
        retries = 0
        session = _get_session()
        while not self._cancel_job:
            request_headers = {'Referer': ref}
            if done:
                request_headers['Range'] = f'bytes={done}-'
            resp = None
            try:
                resp = session.get(
                    self._direct_url,
                    headers=request_headers,
                    timeout=60,
                    stream=True,
                    allow_redirects=True,
                    **config.proxy_request_kwargs(),
                )
                status = getattr(resp, 'status_code', 0)
                response_range = _content_range(
                    getattr(resp, 'headers', {}).get('content-range'))
                if done:
                    if status != 206 or not response_range or response_range[0] != done:
                        raise Exception(f"直接續傳失敗 (HTTP {status})")
                    total = response_range[2]
                elif status == 206 and response_range:
                    total = response_range[2]
                elif status == 200:
                    try:
                        total = int(resp.headers.get('content-length') or 0)
                    except Exception:
                        total = 0
                else:
                    raise Exception(f"直接下載失敗 (HTTP {status})")

                received = 0
                with storage_open(part, 'ab' if done else 'wb') as target:
                    for chunk in resp.iter_content(chunk_size=262144):
                        if self._cancel_job:
                            break
                        if not chunk:
                            continue
                        speed_limiter.acquire(len(chunk))
                        target.write(chunk)
                        done += len(chunk)
                        received += len(chunk)
                        elapsed = time.time() - start_time
                        speed = done / elapsed if elapsed > 0 else 0
                        if self._progress_callback:
                            self._progress_callback(done, total, speed)

                if self._cancel_job:
                    break
                if total > 0 and done >= total:
                    return done, total
                if total == 0 and received > 0:
                    return done, done
                raise Exception("直接下載連線提前結束")
            except StorageWriteError:
                raise
            except Exception:
                if self._cancel_job:
                    break
                retries += 1
                if retries > _DIRECT_RANGE_RETRIES:
                    raise
                time.sleep(_DIRECT_RETRY_BASE_DELAY * retries)
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
        return done, total


class SupJavBrowser:
    _url_root = 'https://supjav.com'
    _scraper = None

    CATEGORIES = [
        ('最近更新', 'https://supjav.com/'),
        ('熱門總榜', 'https://supjav.com/popular'),
        ('本週熱門', 'https://supjav.com/popular?sort=week'),
        ('本月熱門', 'https://supjav.com/popular?sort=month'),
        ('無碼', 'https://supjav.com/category/uncensored-jav'),
        ('有碼', 'https://supjav.com/category/censored-jav'),
        ('素人', 'https://supjav.com/category/amateur'),
        ('中文字幕', 'https://supjav.com/category/chinese-subtitles'),
        ('英文字幕', 'https://supjav.com/category/english-subtitles'),
        ('破壞版', 'https://supjav.com/category/reducing-mosaic'),
    ]

    @classmethod
    def _get_scraper(cls):
        global _browser_scraper
        if _browser_scraper is None:
            with _browser_scraper_lock:
                if _browser_scraper is None:
                    _browser_scraper = _make_scraper()
        cls._scraper = _browser_scraper
        return _browser_scraper

    @classmethod
    def _with_lang(cls, url, lang=''):
        lang = (lang or '').strip().strip('/')
        if not lang:
            return url
        root = cls._url_root
        prefix = root + '/'
        if url == root or url == prefix:
            return f'{prefix}{lang}/'
        if url.startswith(prefix):
            return f'{prefix}{lang}/{url[len(prefix):]}'
        return url

    @classmethod
    def fetch_categories(cls, lang=''):
        return [{'name': site_i18n.loc(site_i18n.CATEGORY_I18N, u, n),
                 'url': cls._with_lang(u, lang), 'count': 0}
                for n, u in cls.CATEGORIES]

    @classmethod
    def fetch_page(cls, url):
        def _validate(resp):
            s = BeautifulSoup(resp.content, 'html.parser')
            return bool(s.select('div.post a[href*=".html"]'))
        resp, host, reason = fetch_with_mirrors(cls._get_scraper(), url, 'supjav', _validate)
        if reason == 'blocked':
            raise MirrorsBlockedError(url)
        if reason != 'ok':
            return []
        try:
            soup = BeautifulSoup(resp.content, 'html.parser')
            return _parse_videos(soup)
        except Exception:
            return []

    @classmethod
    def page_url(cls, base, page):
        if page <= 1:
            return base
        if '?s=' in base or '&s=' in base:
            root, _, qs = base.partition('?')
            return f"{root.rstrip('/')}/page/{page}/?{qs}"
        if '?' in base:
            return f"{base}&page={page}"
        return f"{base.rstrip('/')}/page/{page}"

    @classmethod
    def search_url(cls, query, lang=''):
        return f"{cls._with_lang(cls._url_root + '/', lang)}?s={quote(query, safe='')}"

    @classmethod
    def search(cls, query, lang=''):
        return cls.fetch_page(cls.search_url(query, lang=lang))
