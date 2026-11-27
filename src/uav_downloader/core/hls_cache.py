"""Bind resumable HLS bytes to their playlist; serialize publishers per output."""
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit, urljoin

from .storage import StorageWriteError


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _resource(url):
    p = urlsplit(url)
    # Keep unknown and query-only selectors verbatim; discard only known
    # transport credentials. No URL, key material or token is written to disk.
    args = [arg for arg in p.query.split('&') if arg.partition('=')[0].lower()
            not in {'token', 'expires', 'signature', 'x-amz-signature', 'x-amz-date',
                    'x-amz-credential', 'x-amz-security-token', 'x-amz-expires'}]
    return (p.scheme, p.netloc, p.path, args)


def fingerprint(job, playlist, variant_url, variant):
    rows = []
    for seg, url, (key, iv) in zip(playlist.segments, job._tsList, job._segment_crypto, strict=True):
        key_uri = getattr(seg.key, 'uri', None)
        rows.append([_resource(url), getattr(seg, 'duration', None), getattr(seg, 'byterange', None),
                     getattr(seg, 'discontinuity', False), _resource(urljoin(variant_url, key_uri)) if key_uri else None,
                     hashlib.sha256(key).hexdigest() if key else None, iv])
    info = getattr(variant, 'stream_info', None)
    return _hash([type(job).__name__, getattr(job, '_hls_source_name', ''), getattr(job, '_resolution_pref', ''),
                  _resource(job._m3u8url), _resource(variant_url), getattr(info, 'resolution', None),
                  getattr(info, 'bandwidth', None), getattr(info, 'codecs', None), job._media_sequence, rows])


def namespace(slug):
    return '.uav-hls-' + _hash(slug[:80])[:12] + '-'


def cache_name(slug, target, digest):
    return namespace(slug) + _hash([os.path.normcase(target), digest])[:32]


def plain_dir(path):
    path = Path(path)
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400)


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
        yield  # Closing the handle releases the lock on all exit paths.
