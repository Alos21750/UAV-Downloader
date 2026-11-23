"""Manual, read-only recovery of a verified prefix of an incomplete HLS job."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
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
