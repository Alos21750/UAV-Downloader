"""Manual, read-only recovery of a verified prefix of an incomplete HLS job."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import time


@dataclass(frozen=True)
class Prefix:
    cache: Path
    target: Path
    total: int
    hashes: tuple


def _plain(name):
    # Only a single local component; reject device names and NTFS streams.
    return (isinstance(name, str) and 0 < len(name) <= 255 and
            name not in ('.', '..') and not name.endswith((' ', '.')) and
            not any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name) and
            not Path(name).is_reserved())


def _linked(path):
    return path.is_symlink() or (getattr(path, 'is_junction', lambda: False)())


def receipt_binding(item):
    values = [item.job_id, item.url, str(Path(item.dest).absolute()), item.resolution, item.filename_template]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def store_prefix(queue, item, plan):
    """Store a bounded content-addressed receipt beside the *application queue*.

    The CSV pins the digest. Never discover or adopt receipts from media folders.
    A crash between receipt and CSV publication leaves the previous pair usable.
    """
    dest = Path(item.dest).absolute()
    if (plan.cache.parent != dest or plan.target.parent != dest or
            not _plain(plan.cache.name) or not _plain(plan.target.name) or
            plan.target.suffix.lower() != '.mp4' or not 0 < len(plan.hashes) < plan.total <= 100_000):
        raise ValueError('Invalid recovery location or count')
    payload = json.dumps([1, receipt_binding(item), plan.cache.name, plan.target.name, plan.total,
                          [[n, h.hex()] for n, h in plan.hashes]], separators=(',', ':')).encode()
    key = hashlib.sha256(payload).hexdigest()
    folder = Path(str(queue) + '.receipts')
    if _linked(folder):
        raise ValueError('Invalid receipt directory')
    folder.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, folder / key)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return key


def discard_receipt(queue, key):
    """Forget only a known, byte-identical app receipt, never any cached media."""
    if not re.fullmatch(r'[0-9a-f]{64}', key):
        return
    file = Path(str(queue) + '.receipts') / key
    try:
        if not _linked(file.parent) and not _linked(file) and file.is_file():
            with file.open('rb') as f:
                if os.fstat(f.fileno()).st_size > 9_000_000 or hashlib.sha256(f.read(9_000_001)).hexdigest() != key:
                    return
            file.unlink()
    except OSError:
        pass  # Optional metadata cleanup must not invalidate the committed queue.


def restore_prefix(queue, item, key, slug):
    if not slug or not re.fullmatch(r'[0-9a-f]{64}', key or ''):
        return None
    dest = Path(item.dest).absolute()
    source = Path(str(queue) + '.receipts') / key
    try:
        if _linked(source.parent) or _linked(source) or not source.is_file():
            return None
        with source.open('rb') as f:
            if os.fstat(f.fileno()).st_size > 9_000_000:
                return None
            data = f.read(9_000_001)
        if hashlib.sha256(data).hexdigest() != key:
            return None
        entry = json.loads(data)
        if (not isinstance(entry, list) or len(entry) != 6 or
                entry[:2] != [1, receipt_binding(item)]):
            return None
        cache, target, total, hashes = entry[2:]
        if (cache != slug[:80] or not _plain(cache) or not _plain(target) or
                not target.lower().endswith('.mp4') or type(total) is not int or
                not isinstance(hashes, list) or not 0 < len(hashes) < total <= 100_000 or
                any(type(n) is not int or not 0 < n <= 2**32 or
                    not re.fullmatch(r'[0-9a-f]{64}', h) for n, h in hashes)):
            return None
        return Prefix(dest / cache, dest / target, total,
                      tuple((n, bytes.fromhex(h)) for n, h in hashes))
    except (ValueError, TypeError, RecursionError, OSError):
        return None


class Receipts:
    """Volatile receipts for bytes this crawler actually published. Never trust names alone."""
    def __init__(self):
        self._hashes = {}
        self._lock = threading.Lock()

    def record(self, index, payload):
        if 0 <= index < 100_000:
            receipt = (len(payload), hashlib.sha256(payload).digest())
            with self._lock:
                self._hashes[index] = receipt

    def plan(self, cache, target, total):
        with self._lock:
            hashes = []
            for index in range(total):
                if index not in self._hashes:
                    break
                hashes.append(self._hashes[index])
        if not hashes or len(hashes) == total:
            return None
        return Prefix(Path(cache).absolute(), Path(target).absolute(), total, tuple(hashes))


def export_prefix(plan, ffmpeg, cancelled=lambda: False):
    def check():
        if cancelled():
            raise InterruptedError('Export cancelled')

    check()
    if not ffmpeg:
        raise ValueError('FFmpeg is unavailable')
    if _linked(plan.cache):
        raise ValueError('Cached segment directory changed')
    with tempfile.TemporaryDirectory(prefix='.uav-partial-', dir=plan.target.parent) as temp:
        ts = Path(temp, 'prefix.ts')
        mp4 = Path(temp, 'prefix.mp4')
        with ts.open('xb') as out:
            for index, (size, expected) in enumerate(plan.hashes):
                check()
                candidate = plan.cache / f'{index:06d}.mp4'
                if not stat.S_ISREG(candidate.lstat().st_mode):
                    raise ValueError('Cached segment is not a file')
                digest = hashlib.sha256()
                with candidate.open('rb') as source:
                    if os.fstat(source.fileno()).st_size != size:
                        raise ValueError('Cached segment size changed')
                    remaining = size
                    while remaining:
                        check()
                        block = source.read(min(remaining, 1024 * 1024))
                        if not block:
                            raise ValueError('Cached segment truncated')
                        remaining -= len(block)
                        digest.update(block)
                        out.write(block)
                    if digest.digest() != expected:
                        raise ValueError('Cached segment changed')
        check()
        args = [ffmpeg, '-nostdin', '-loglevel', 'error', '-xerror',
                '-protocol_whitelist', 'file', '-f', 'mpegts', '-i', str(ts),
                '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-movflags', '+faststart',
                '-f', 'mp4', str(mp4)]
        with subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ) as process:
            deadline = time.monotonic() + 1800
            try:
                while process.poll() is None:
                    check()
                    if time.monotonic() > deadline:
                        raise TimeoutError('FFmpeg export timed out')
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    raise ValueError('FFmpeg could not remux this prefix')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        with mp4.open('rb') as output:
            if output.read(8)[4:] != b'ftyp':
                raise ValueError('Missing MP4 file header')
        check()
        for number in range(1, 1001):
            suffix = '' if number == 1 else f'-{number}'
            target = plan.target.with_name(f'{plan.target.stem}.partial{suffix}.mp4')
            try:
                # Both operations are atomic and refuse existing destinations.
                if os.name == 'nt':
                    os.rename(mp4, target)
                else:
                    os.link(mp4, target)
                return target
            except FileExistsError:
                continue
        raise FileExistsError('No available partial-export filename')
