"""Bounded, persistent single-worker queue with conservative storage handling."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from uav_downloader import sites
from uav_downloader.core.storage import StorageWriteError

MAX_JOBS = 1000
MAX_STATE_BYTES = 2 * 1024 * 1024
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
STATUSES = TERMINAL | {'pending', 'running', 'cancelling'}
QUALITIES = {'highest', 'lowest', '1080', '720', '480', '360'}


def validate_job(raw):
    if not isinstance(raw, dict) or set(raw) - {'url', 'resolution', 'max_workers', 'speed_limit_mib'}:
        raise ValueError('Unknown job settings')
    url = raw.get('url')
    if not isinstance(url, str) or not 1 <= len(url) <= 2048 or any(ord(c) < 33 for c in url):
        raise ValueError('Invalid URL')
    try:
        parsed = urlsplit(url)
        canonical = (not any(piece in {'.', '..'} for piece in parsed.path.split('/'))
                     and '\\' not in url and '%' not in parsed.path
                     and '//' not in parsed.path)
        if parsed.query:
            query = parse_qs(parsed.query, keep_blank_values=True)
            canonical = canonical and parsed.hostname in {'hanime1.me', 'www.hanime1.me'}
            canonical = canonical and set(query) == {'v'} and len(query['v']) == 1
            canonical = canonical and bool(re.fullmatch(r'[0-9]+', query['v'][0]))
        safe = (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment and canonical)
        cls = sites.validate_url(url) if safe else None
        # Restrict this initial remote-control surface to the maintained adapters.
        allowed = cls in sites.FEATURED_SITE_CLASSES
        host = parsed.hostname or ''
        host_allowed = any(host == h for h in (
            'jable.tv', 'fs1.app', 'jablet.com', 'jablet.tv', 'supjav.com',
            'missav.ws', 'missav.ai', 'missav.com', 'missav.live',
            'missav123.com', 'missav888.com', 'missav789.com',
            'hanime1.me', 'www.hanime1.me'))
    except (ValueError, TypeError):
        allowed = host_allowed = False
    if not allowed or not host_allowed:
        raise ValueError('Only supported HTTPS video URLs are accepted')
    resolution = raw.get('resolution', 'highest')
    workers = raw.get('max_workers', 4)
    limit = raw.get('speed_limit_mib', 0)
    if (not isinstance(resolution, str) or resolution not in QUALITIES
            or type(workers) is not int or not 1 <= workers <= 16
            or type(limit) not in (float, int) or not 0 <= limit <= 1024
            or not math.isfinite(limit)):
        raise ValueError('Invalid quality, worker count or speed limit')
    return {'url': url, 'resolution': resolution, 'max_workers': workers,
            'speed_limit_mib': float(limit)}


def run_download(job, root, cancel, progress):
    """Use the existing adapters, TLS sharing, cancellation and I/O safeguards."""
    from uav_downloader.sites import base
    base.speed_limiter.set_limit(job['speed_limit_mib'])
    finished = threading.Event()
    monitor = None
    try:
        site = sites.CreateSite(job['url'], str(root), silence=True,
                                max_workers=job['max_workers'], resolution=job['resolution'])
        if site is not None and isinstance(getattr(site, '_last_error', None), StorageWriteError):
            raise site._last_error
        if site is None or not site.is_url_vaildate():
            raise ValueError('Video resolution failed')
        # Defense in depth if a future adapter weakens its URL/identifier validator.
        for name in ('_dest_folder', '_temp_folder'):
            path = getattr(site, name, None)
            if not path or not Path(path).resolve().is_relative_to(Path(root).resolve()):
                raise ValueError('Adapter destination escaped configured download root')
        site._progress_callback = lambda done, total, speed: progress(
            done, total, speed, getattr(site, '_progress_unit', 'segments'))

        def watch_cancel():
            while not finished.wait(.1):
                if cancel.is_set():
                    site.cancel_download(cleanup=False)
                    return

        monitor = threading.Thread(target=watch_cancel, daemon=True)
        monitor.start()
        if cancel.is_set():
            return False
        return bool(site.start_download()) and not cancel.is_set()
    finally:
        finished.set()
        if monitor is not None:
            monitor.join(timeout=1)
        base.speed_limiter.set_limit(0)


class QueueManager:
    def __init__(self, root, state_dir, runner=None, autostart=True):
        self.root = Path(root).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.state_dir / 'queue.json'
        self._condition = threading.Condition(threading.RLock())
        self._runner = runner or run_download
        self._jobs = []
        self._halted = False
        self._closed = False
        self._cancel = threading.Event()
        self._active = None
        self._thread = None
        self._load()
        if autostart:
            self.start()

    def start(self):
        with self._condition:
            if self._closed:
                raise ValueError('Queue is closed')
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._work, daemon=True, name='uav-web-download')
            self._thread.start()

    def _load(self):
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError('State is too large')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('version') != 1
                    or not isinstance(data.get('jobs'), list)
                    or len(data['jobs']) > MAX_JOBS or type(data.get('halted')) is not bool):
                raise ValueError('Invalid queue state')
            seen = set()
            for saved in data['jobs']:
                settings = validate_job({k: saved[k] for k in
                                         ('url', 'resolution', 'max_workers', 'speed_limit_mib')})
                identity = str(uuid.UUID(saved['id']))
                if identity in seen or saved['status'] not in STATUSES:
                    raise ValueError('Invalid queue identity/status')
                seen.add(identity)
                job = self._new_job(settings, identity)
                job['status'] = ('interrupted' if saved['status'] in {'running', 'cancelling'}
                                 else saved['status'])
                job['logs'] = [job['status']]
                self._jobs.append(job)
            self._halted = data['halted']
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise ValueError('Queue state is invalid; preserve it and restore a valid backup') from exc

    @staticmethod
    def _new_job(settings, identity=None):
        return {**settings, 'id': identity or str(uuid.uuid4()), 'status': 'pending',
                'done': 0, 'total': 0, 'speed_bps': 0, 'unit': 'preparing',
                'logs': ['queued']}

    def _save(self):
        payload = json.dumps({'version': 1, 'halted': self._halted,
                              'jobs': self._jobs}, ensure_ascii=True).encode('utf-8')
        if len(payload) > MAX_STATE_BYTES:
            raise ValueError('Queue state limit reached; clear finished jobs')
        temp = self.path.with_suffix('.tmp')
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                written = stream.write(payload)
                if written != len(payload):
                    raise OSError('Short state write')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            self._halted = True
            self._cancel.set()
            raise StorageWriteError(self.path, exc) from exc

    def snapshot(self):
        with self._condition:
            return {'jobs': copy.deepcopy(self._jobs), 'halted': self._halted,
                    'worker_limit': 1}

    def add(self, batch):
        if not isinstance(batch, list) or not 1 <= len(batch) <= 100:
            raise ValueError('Provide 1 to 100 jobs')
        jobs = [self._new_job(validate_job(raw)) for raw in batch]
        with self._condition:
            if self._closed or len(self._jobs) + len(jobs) > MAX_JOBS:
                raise ValueError('Queue is closed or full; clear finished jobs')
            self._jobs.extend(jobs)
            try:
                self._save()
            except Exception:
                del self._jobs[-len(jobs):]
                raise
            self._condition.notify_all()
            return copy.deepcopy(jobs)

    def _find(self, identity):
        for job in self._jobs:
            if job['id'] == identity:
                return job
        raise KeyError(identity)

    def cancel(self, identity):
        with self._condition:
            job = self._find(identity)
            if job['status'] == 'pending':
                self._event(job, 'cancelled')
            elif job['status'] in {'running', 'cancelling'}:
                self._event(job, 'cancelling')
                self._cancel.set()
            self._save()

    def retry(self, identity):
        with self._condition:
            job = self._find(identity)
            if job['status'] not in TERMINAL:
                raise ValueError('Only finished or interrupted jobs can retry')
            self._event(job, 'pending')
            job.update(done=0, total=0, speed_bps=0, unit='preparing')
            self._save()
            self._condition.notify_all()

    def clear(self):
        with self._condition:
            self._jobs = [job for job in self._jobs if job['status'] not in TERMINAL]
            self._save()

    def resume(self):
        with self._condition:
            self._halted = False
            self._save()
            self._condition.notify_all()

    @staticmethod
    def _event(job, status, detail=None):
        job['status'] = status
        job['logs'] = (job['logs'] + [detail or status])[-30:]
        job['speed_bps'] = 0

    def _progress(self, job, done, total, speed, unit):
        with self._condition:
            values = (done, total, speed)
            if all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                   for value in values):
                job.update(done=min(done, 10**16), total=min(total, 10**16),
                           speed_bps=min(speed, 10**12),
                           unit=unit if unit in {'bytes', 'segments', 'preparing', 'postprocess'} else 'segments')

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or
                    (not self._halted and any(j['status'] == 'pending' for j in self._jobs)))
                if self._closed:
                    return
                job = next(j for j in self._jobs if j['status'] == 'pending')
                self._cancel = threading.Event()
                self._active = job['id']
                self._event(job, 'running')
                try:
                    self._save()
                except (OSError, ValueError):
                    self._halted = True
                    self._event(job, 'failed', 'State storage failed; queue halted')
                    continue
            storage_failed = False
            try:
                completed = self._runner(copy.deepcopy(job), self.root, self._cancel,
                    lambda *args: self._progress(job, *args))
                status = 'completed' if completed else 'failed'
                detail = status
            except StorageWriteError:
                status, detail = 'failed', 'Storage I/O failed; check the disk, then explicitly resume'
                storage_failed = True
            except Exception as exc:
                # Do not expose provider URLs, query credentials or arbitrary response bodies.
                status, detail = 'failed', 'Download failed (' + type(exc).__name__[:60] + '); retry after checking source/access'
            with self._condition:
                self._active = None
                if not storage_failed and self._cancel.is_set():
                    status = detail = 'cancelled'
                self._event(job, 'interrupted' if self._closed else status, detail)
                self._halted = self._halted or storage_failed
                try:
                    self._save()
                except (OSError, ValueError):
                    self._halted = True

    def close(self):
        with self._condition:
            self._closed = True
            self._cancel.set()
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=10)
