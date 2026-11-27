"""Bind resumable HLS bytes to their playlist; serialize publishers per output."""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit, urljoin

from .storage import StorageWriteError


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _resource(url, playlist_auth=()):
    p = urlsplit(url)
    # Keep unknown and query-only selectors verbatim; discard only known
    # transport credentials. No URL, key material or token is written to disk.
    parts = p.query.split('&') if p.query else []
    ignored = {'token', 'expires', 'signature', 'x-amz-signature', 'x-amz-date',
               'x-amz-credential', 'x-amz-security-token', 'x-amz-expires'}
    # Only a complete, site-declared signing tuple on a named playlist.
    # Never discard an isolated selector such as ?s=episode or a segment ID.
    if p.path.lower().endswith('.m3u8') and set(playlist_auth).issubset(
            arg.partition('=')[0].lower() for arg in parts):
        ignored.update(playlist_auth)
    args = [arg for arg in parts if arg.partition('=')[0].lower() not in ignored]
    return (p.scheme, p.netloc, p.path, args)


def fingerprint(job, playlist, variant_url, variant):
    rows = []
    for seg, url, (key, iv) in zip(playlist.segments, job._tsList, job._segment_crypto, strict=True):
        key_uri = getattr(seg.key, 'uri', None)
        rows.append([_resource(url), getattr(seg, 'duration', None), getattr(seg, 'byterange', None),
                     getattr(seg, 'discontinuity', False), _resource(urljoin(variant_url, key_uri)) if key_uri else None,
                     hashlib.sha256(key).hexdigest() if key else None, iv])
    info = getattr(variant, 'stream_info', None)
    auth = getattr(job, 'playlist_auth_params', ())
    return _hash([type(job).__name__, getattr(job, '_hls_source_name', ''), getattr(job, '_resolution_pref', ''),
                  _resource(job._m3u8url, auth), _resource(variant_url, auth), getattr(info, 'resolution', None),
                  getattr(info, 'bandwidth', None), getattr(info, 'codecs', None), job._media_sequence, rows])


def namespace(slug):
    return '.uav-hls-' + _hash(slug[:80])[:12] + '-'


def cache_name(slug, target, digest):
    return namespace(slug) + _hash([os.path.normcase(target), digest])[:32]


def plain_dir(path):
    path = Path(path)
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400)


def _read_cursor(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or info.st_size > 256):
        raise ValueError('Invalid cache owner record')
    with path.open('rb') as f:
        value = json.loads(f.read(257))
    if (not isinstance(value, list) or len(value) != 3
            or not isinstance(value[0], str) or not re.fullmatch('[0-9a-f]{12}', value[0])
            or not isinstance(value[1], str) or not re.fullmatch('[0-9a-f]{64}', value[1])
            or type(value[2]) is not int or value[2] <= 0):
        raise ValueError('Invalid cache owner record')
    return value


def select_cache(folder, slug, target, digest, count):
    """Called under output_lease. Retire only the previous recorded generation.

    The old cursor stays authoritative until retirement succeeds. Publish the
    new cursor atomically before any segment writer can run.
    """
    root = Path(folder)
    control = root / '.uav-locks'
    cursor = control / (_hash(os.path.normcase(target)) + '.json')
    desired = [_hash(slug[:80])[:12], digest, count]
    directory = root / cache_name(slug, target, digest)
    try:
        old = _read_cursor(cursor)
        if old != desired:
            if old:
                previous = root / ('.uav-hls-' + old[0] + '-' + _hash([os.path.normcase(target), old[1]])[:32])
                try:
                    ordinary = plain_dir(previous)
                except FileNotFoundError:
                    ordinary = None
                if ordinary is False:
                    raise ValueError('Unsafe previous transfer directory')
                if ordinary:
                    for entry in previous.iterdir():
                        idx = re.fullmatch(r'([0-9]{6,})\.mp4(?:\.part)?', entry.name)
                        if idx and int(idx[1]) < old[2] and idx[1] == f'{int(idx[1]):06d}':
                            entry.unlink()
                    if not any(previous.iterdir()):
                        previous.rmdir()
            if directory.exists() and (not plain_dir(directory) or any(directory.iterdir())):
                raise ValueError('Refusing to adopt an unowned segment cache')
            with tempfile.NamedTemporaryFile(dir=control, delete=False) as f:
                tmp = Path(f.name)
            try:
                tmp.write_text(json.dumps(desired), encoding='ascii')
                os.replace(tmp, cursor)
            finally:
                tmp.unlink(missing_ok=True)
        directory.mkdir(exist_ok=True)
        if not plain_dir(directory):
            raise ValueError('Unsafe segment cache')
        return str(directory)
    except (ValueError, OSError) as exc:
        error = exc if isinstance(exc, OSError) else OSError(errno.EINVAL, str(exc))
        raise StorageWriteError(str(directory), error) from exc


@contextmanager
def output_lease(folder, target):
    """Persistent lock files avoid unlink/recreate races; OS releases on crash."""
    parent = Path(folder) / '.uav-locks'
    lock = parent / _hash(os.path.normcase(target))
    try:
        parent.mkdir(exist_ok=True)
        if not plain_dir(parent) or lock.is_symlink() or (lock.exists() and not lock.is_file()):
            raise OSError(errno.EINVAL, 'Unsafe transfer lock')
        handle = lock.open('a+b')
    except OSError as exc:
        raise StorageWriteError(str(lock), exc) from exc
    with handle:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise RuntimeError('Another download is using this output; retry after it finishes.') from exc
            raise StorageWriteError(str(lock), exc) from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
