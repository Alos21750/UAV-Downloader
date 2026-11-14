"""Versioned media identity and OS-backed ownership for download files.

Lock files are permanent rendezvous points: unlinking them would let another
process lock a different inode. The OS releases ownership when a process dies.
No signed URL, cookie, encryption key or authorization header is serialized.
"""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from uav_downloader.core.storage import StorageWriteError
from uav_downloader.core.stop import current_stop_context
from uav_downloader.core.video_identity import site_from_url


SCHEMA = 1
_SIGNATURE_PARAMETERS = frozenset({
    'token', 'access_token', 'signature', 'sig', 'expires', 'expire',
    'policy', 'key-pair-id', 'hdnts', 'hdnea',
})


def identity_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def url_fingerprint(url, *, signed=False):
    """Retain resource selectors; optionally remove known auth-only arguments.

    A match after removing signatures is only a candidate for reuse: cached
    segments still require the exact URL or revalidation against the new URL.
    """
    p = urlsplit(str(url or ''))
    query = parse_qsl(p.query, keep_blank_values=True)
    if signed:
        query = [(k, v) for k, v in query if k.lower() not in _SIGNATURE_PARAMETERS
                 and not k.lower().startswith('x-amz-')]
    canonical = urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path,
                            urlencode(query), ''))
    return hashlib.sha256(canonical.encode()).hexdigest()


def task_identity(url, video_id, resolution):
    site = site_from_url(url) or (urlsplit(url).hostname or '').lower()
    return dict(site=site, video_id=str(video_id).casefold(), resolution=str(resolution))


def file_digest(path, *, stop_context=None):
    with open(path, 'rb') as stream:
        if stop_context is None:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
        digest = hashlib.sha256()
        while True:
            stop_context.check()
            chunk = stream.read(1024 * 1024)
            stop_context.check()
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _read_json(path):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, value, *, stop_context=None):
    tmp = None
    try:
        if stop_context is not None:
            stop_context.check()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.uav-', suffix='.tmp', delete=False) as stream:
            tmp = Path(stream.name)
            json.dump(value, stream, sort_keys=True, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        if stop_context is not None:
            stop_context.check()
        os.replace(tmp, path)
    except OSError as exc:
        raise StorageWriteError(path, exc, operation='write ownership metadata') from exc
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # Preserve the original storage failure if a drive vanished.


class ResourceBusyError(RuntimeError):
    """Another download owns this cache or output. Retry after it finishes."""


class ResourceChangedError(RuntimeError):
    """The selected media changed while a transfer was in progress."""


class FileOwnership:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        if self.handle is not None:
            raise RuntimeError('Ownership is not reentrant')
        handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, 'a+b')
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
        except OSError as exc:
            if handle is not None:
                handle.close()
            raise StorageWriteError(self.path, exc, operation='open ownership file') from exc
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle = handle
        except OSError as exc:
            if handle is not None:
                handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ResourceBusyError('Another download owns these files; retry after it finishes') from exc
            raise StorageWriteError(self.path, exc, operation='lock download files') from exc
        return self

    def release(self):
        if self.handle is not None:
            # Closing releases the OS lock, including on exception paths.
            self.handle.close()
            self.handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *unused):
        self.release()


class DownloadStorage:
    def __init__(self, destination, identity):
        self.destination = Path(destination).resolve()
        self.root = self.destination / '.uav'
        self.identity = identity
        self.task_key = identity_hash(identity)
        self.task_root = self.root / 'c' / self.task_key[:16]
        self.cache = self.task_root / 'staging'
        self.writer = uuid.uuid4().hex
        self.media_key = None
        self.output = None
        self.output_record = None
        self._output_lock = None
        # Lock the actual shortened directory key too: even a truncated-hash
        # collision must serialize before checking the full manifest identity.
        self._task_lock = FileOwnership(self.root / 'l' / (self.task_key[:16] + '.lock'))
        self._metadata_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._running = False
        self._cleanup_requested = False
        self.manifest = {}

    def _managed(self, path):
        path = Path(path)
        # Refuse redirected managed directories and arbitrary cleanup targets.
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root.resolve()) or not self.root.resolve().is_relative_to(self.destination):
            raise RuntimeError('Download metadata or cache escaped its destination')
        return path

    @contextmanager
    def session(self):
        self._managed(self._task_lock.path)
        with self._state_lock:
            if self._running:
                raise ResourceBusyError('This download already owns an active session')
            self._task_lock.acquire()
            self.writer = uuid.uuid4().hex
            self._running = True
        try:
            yield self
        finally:
            with self._state_lock:
                try:
                    if self._cleanup_requested:
                        self._cleanup_locked()
                finally:
                    if self._output_lock is not None:
                        self._output_lock.release()
                        self._output_lock = None
                    self._running = False
                    self._cleanup_requested = False
                    self._task_lock.release()

    def bind_media(self, descriptor):
        if not self._running:
            raise RuntimeError('Cache binding requires task ownership')
        if self._output_lock is not None:
            self._output_lock.release()
            self._output_lock = None
        self.output = self.output_record = None
        self.media_key = identity_hash(descriptor)
        cache = self._managed(self.task_root / self.media_key[:16])
        existing = _read_json(cache / 'manifest.json')
        valid = (existing.get('schema') == SCHEMA and existing.get('task_key') == self.task_key
                 and existing.get('media_key') == self.media_key and isinstance(existing.get('segments'), dict))
        if cache.exists() and not valid:
            # Never adopt or delete files with missing, corrupt or foreign metadata.
            try:
                cache = Path(tempfile.mkdtemp(prefix=self.media_key[:16] + '-', dir=self.task_root))
            except OSError as exc:
                raise StorageWriteError(self.task_root, exc, operation='isolate unproven cache') from exc
        self.cache = self._managed(cache)
        self.manifest = existing if valid else dict(schema=SCHEMA, task_key=self.task_key,
            media_key=self.media_key, identity=self.identity, segments={})
        self.manifest['writer'] = self.writer
        with self._metadata_lock:
            _write_json(self.cache / 'manifest.json', self.manifest)

    def segment_record(self, index):
        with self._metadata_lock:
            record = self.manifest.get('segments', {}).get(str(index), {})
            return dict(record) if isinstance(record, dict) else {}

    def local_segment_valid(self, index):
        record = self.segment_record(index)
        path = self._managed(self.cache / f'{index:06d}.mp4')
        try:
            return (record.get('bytes', 0) > 0 and path.stat().st_size == record['bytes']
                    and file_digest(path) == record.get('sha256'))
        except (OSError, TypeError, ValueError):
            return False

    def reusable_segment(self, index, url):
        record = self.segment_record(index)
        return (record.get('url_sha256') == url_fingerprint(url)
                and record.get('validated_writer') == self.writer
                and self.local_segment_valid(index))

    def record_segment(self, index, url, data, etag=''):
        if not self._running:
            raise RuntimeError('Segment publication requires task ownership')
        with self._metadata_lock:
            digest = hashlib.sha256(data).hexdigest()
            expected = self.manifest.get('expected_segments', {}).get(str(index), {})
            if expected and (expected.get('sha256') != digest or expected.get('bytes') != len(data)):
                raise RuntimeError('Media changed during download; resolve the source again before retrying')
            self.manifest['segments'][str(index)] = dict(bytes=len(data),
                sha256=digest, url_sha256=url_fingerprint(url), validated_writer=self.writer,
                etag=etag if isinstance(etag, str) and len(etag) < 1024 and etag.startswith('"') and etag.endswith('"') else '')
            _write_json(self.cache / 'manifest.json', self.manifest)

    def revalidate_segment(self, index, url):
        if not self._running or not self.local_segment_valid(index):
            return False
        with self._metadata_lock:
            self.manifest['segments'][str(index)]['url_sha256'] = url_fingerprint(url)
            self.manifest['segments'][str(index)]['validated_writer'] = self.writer
            _write_json(self.cache / 'manifest.json', self.manifest)
        return True

    def confirm_output_sources(self, sources):
        """Keep completed-output provenance only after every source matches."""
        if not self._running:
            raise RuntimeError('Source confirmation requires ownership')
        with self._metadata_lock:
            for index, source in enumerate(sources):
                self.manifest['segments'][str(index)] = dict(source, validated_writer=self.writer)
            _write_json(self.cache / 'manifest.json', self.manifest)

    def expect_segments(self, sources):
        if not self._running:
            raise RuntimeError('Source expectations require ownership')
        with self._metadata_lock:
            self.manifest['expected_segments'] = {
                str(index): dict(bytes=s['bytes'], sha256=s['sha256']) for index, s in enumerate(sources)}
            _write_json(self.cache / 'manifest.json', self.manifest)

    def _output_meta(self, path):
        key = hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()
        return self._managed(self.root / 'o' / (key + '.json')), key

    def reserve_output(self, candidate):
        """Reserve a readable candidate, then a stable identity suffix on collision."""
        if not self._running or self.media_key is None:
            raise RuntimeError('Output binding requires known media and ownership')
        owner = identity_hash([self.task_key, self.media_key])
        for attempt in range(100):
            path = Path(candidate(attempt, owner))
            if path.parent.resolve() != self.destination:
                raise RuntimeError('Output must stay in its selected destination')
            meta, key = self._output_meta(path)
            lock = FileOwnership(self.root / 'l' / ('out-' + key + '.lock'))
            try:
                lock.acquire()
            except ResourceBusyError:
                current = _read_json(meta)
                if current.get('schema') == SCHEMA and current.get('owner') != owner:
                    continue
                # Do not concurrently make a second copy of the same resource.
                raise
            record = _read_json(meta)
            own = record.get('schema') == SCHEMA and record.get('owner') == owner
            related = (path, Path(str(path) + '.part'), path.with_suffix('.jpg'), path.with_suffix('.srt'))
            occupied = any(p.exists() for p in related)
            if (record and not own) or (occupied and not own):
                lock.release()
                continue
            if path.exists() and (not own or record.get('state') != 'published'
                                  or not self._valid_output(path, record)):
                # Preserve tampered, incomplete and legacy files at their old name.
                lock.release()
                continue
            self.output = path
            self.output_record = meta
            self._output_lock = lock
            if not path.exists():
                _write_json(meta, dict(schema=SCHEMA, owner=owner, state='reserved'))
            return path
        raise ResourceBusyError('All alternate output names are occupied; choose another destination or title')

    @staticmethod
    def _valid_output(path, record):
        try:
            return record.get('bytes', 0) > 0 and path.stat().st_size == record['bytes'] and file_digest(path) == record.get('sha256')
        except (OSError, TypeError, ValueError):
            return False

    def output_exists(self):
        if self.output is None or self.output_record is None:
            return False
        record = _read_json(self.output_record)
        return (record.get('schema') == SCHEMA and record.get('state') == 'published'
                and record.get('owner') == identity_hash([self.task_key, self.media_key])
                and self._valid_output(self.output, record))

    def publish_output(self):
        if not self._running or self._output_lock is None:
            raise RuntimeError('Output publication requires ownership')
        context = current_stop_context()
        if context is not None:
            context.check()
        try:
            size = self.output.stat().st_size
            if size <= 0:
                raise RuntimeError('Empty output cannot be marked as this resource')
            digest = file_digest(self.output, stop_context=context)
        except OSError as exc:
            raise StorageWriteError(self.output, exc, operation='validate published output') from exc
        _write_json(self.output_record, dict(schema=SCHEMA,
            owner=identity_hash([self.task_key, self.media_key]), state='published',
            bytes=size, sha256=digest), stop_context=context)

    def _cleanup_locked(self):
        if self.media_key is None:
            return False
        if self.output_exists():
            # The small manifest remains the provenance of the published file,
            # including when cancellation happens later in subtitle processing.
            return False
        cache = self._managed(self.cache)
        manifest = _read_json(cache / 'manifest.json')
        if (cache.is_dir() and not cache.is_symlink() and manifest.get('schema') == SCHEMA
                and manifest.get('task_key') == self.task_key and manifest.get('media_key') == self.media_key
                and manifest.get('writer') == self.writer):
            try:
                shutil.rmtree(cache)
            except OSError as exc:
                raise StorageWriteError(cache, exc, operation='clean owned cache') from exc
            return True
        return False

    def cleanup(self):
        with self._state_lock:
            if self._running:
                self._cleanup_requested = True
                return False
            self._managed(self._task_lock.path)
            try:
                with self._task_lock:
                    return self._cleanup_locked()
            except ResourceBusyError:
                return False
