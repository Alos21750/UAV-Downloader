"""Conservative, cancellable stream-copy concatenation of local MP4 episodes.

Only self-contained, non-fragmented H.264/HEVC with optional AAC is accepted.
No ffprobe, encoder, network protocol, shell, or user-file overwrite is needed.
The caller chooses episode order and publishes/replaces its final collection.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
import errno
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import time

from uav_downloader.core.storage import StorageWriteError, storage_failure_message


class MediaConcatError(ValueError):
    """Media cannot safely be concatenated without transcoding."""


@dataclass(frozen=True)
class MP4Info:
    duration: float
    streams: tuple


_MAX_MOOV = 32 * 1024 * 1024
_MAX_BOXES = 10000
_MAX_EPISODES = 1000
_MAX_DURATION = 7 * 24 * 60 * 60


def _cancel(check):
    if check is not None and check():
        raise CancelledError('Collection cancelled')


def _boxes(data):
    """Yield bounded child boxes; never trust sizes or allow trailing garbage."""
    pos = 0
    count = 0
    while pos < len(data):
        count += 1
        if count > _MAX_BOXES or len(data) - pos < 8:
            raise MediaConcatError('Invalid MP4 box structure')
        size, kind = struct.unpack_from('>I4s', data, pos)
        header = 8
        if size == 1:
            if len(data) - pos < 16:
                raise MediaConcatError('Invalid extended MP4 box')
            size = struct.unpack_from('>Q', data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = len(data) - pos
        if size < header or size > len(data) - pos:
            raise MediaConcatError('Invalid MP4 box size')
        yield kind, data[pos + header:pos + size]
        pos += size


def _one(data, kind):
    found = [body for tag, body in _boxes(data) if tag == kind]
    if len(found) != 1:
        raise MediaConcatError('Missing or ambiguous MP4 metadata')
    return found[0]


def _duration(header):
    if not header:
        raise MediaConcatError('Missing MP4 duration')
    version = header[0]
    if version == 0 and len(header) >= 20:
        scale, ticks = struct.unpack_from('>II', header, 12)
    elif version == 1 and len(header) >= 32:
        scale, ticks = struct.unpack_from('>IQ', header, 20)
    else:
        raise MediaConcatError('Unsupported MP4 duration')
    seconds = ticks / scale if scale else 0
    if not math.isfinite(seconds) or not 0 < seconds <= _MAX_DURATION:
        raise MediaConcatError('Invalid MP4 duration')
    return scale, seconds


def _descriptors(data):
    pos = 0
    count = 0
    while pos < len(data):
        count += 1
        if count > 64:
            raise MediaConcatError('Too many AAC descriptors')
        tag = data[pos]
        pos += 1
        length = 0
        for _ in range(4):
            if pos >= len(data):
                raise MediaConcatError('Invalid AAC descriptor')
            value = data[pos]
            pos += 1
            length = (length << 7) | (value & 127)
            if not value & 128:
                break
        else:
            raise MediaConcatError('Invalid AAC descriptor length')
        if length > len(data) - pos:
            raise MediaConcatError('Truncated AAC descriptor')
        yield tag, data[pos:pos + length]
        pos += length


def _aac_config(esds):
    if len(esds) < 4 or esds[:4] != b'\0\0\0\0':
        raise MediaConcatError('Unsupported AAC configuration')
    top = list(_descriptors(esds[4:]))
    if len(top) != 1 or top[0][0] != 3:
        raise MediaConcatError('Unsupported AAC descriptors')
    body = top[0][1]
    if len(body) < 3:
        raise MediaConcatError('Truncated AAC configuration')
    flags = body[2]
    # URL, dependency and OCR references are not needed by self-contained AAC.
    if flags & 0xE0:
        raise MediaConcatError('External AAC references are unsupported')
    configs = [item for tag, item in _descriptors(body[3:]) if tag == 4]
    if len(configs) != 1 or len(configs[0]) < 13:
        raise MediaConcatError('Missing AAC decoder configuration')
    config = configs[0]
    if config[0] != 0x40 or (config[1] >> 2) != 5:
        raise MediaConcatError('Only AAC audio is supported for collections')
    specific = [item for tag, item in _descriptors(config[13:]) if tag == 5]
    if len(specific) != 1 or not 2 <= len(specific[0]) <= 64:
        raise MediaConcatError('Missing AAC AudioSpecificConfig')
    return specific[0]


def _stream(trak):
    mdia = _one(trak, b'mdia')
    scale, _seconds = _duration(_one(mdia, b'mdhd'))
    handler = _one(mdia, b'hdlr')
    if len(handler) < 12:
        raise MediaConcatError('Invalid MP4 stream handler')
    media_type = handler[8:12]
    if media_type not in (b'vide', b'soun'):
        raise MediaConcatError('Collections support video and AAC audio only')
    minf = _one(mdia, b'minf')
    dref = _one(_one(minf, b'dinf'), b'dref')
    if len(dref) < 8 or dref[:8] != b'\0\0\0\0\0\0\0\1':
        raise MediaConcatError('Unsupported MP4 data references')
    refs = list(_boxes(dref[8:]))
    if refs != [(b'url ', b'\0\0\0\1')]:
        raise MediaConcatError('External MP4 data references are forbidden')
    stsd = _one(_one(minf, b'stbl'), b'stsd')
    if len(stsd) < 8 or stsd[:8] != b'\0\0\0\0\0\0\0\1':
        raise MediaConcatError('Multiple MP4 sample descriptions are unsupported')
    samples = list(_boxes(stsd[8:]))
    if len(samples) != 1:
        raise MediaConcatError('Invalid MP4 sample description')
    codec, sample = samples[0]
    if len(sample) < 8 or sample[6:8] != b'\0\1':
        raise MediaConcatError('Unsupported MP4 sample data reference')
    if media_type == b'vide':
        if codec not in (b'avc1', b'avc3', b'hvc1', b'hev1') or len(sample) < 78:
            raise MediaConcatError('Only H.264/HEVC video is supported for collections')
        width, height = struct.unpack_from('>HH', sample, 24)
        if not width or not height:
            raise MediaConcatError('Invalid video dimensions')
        track_header = _one(trak, b'tkhd')
        matrix_offset = 40 if track_header and track_header[0] == 0 else 52
        required_size = matrix_offset + 44
        if (len(track_header) < required_size or track_header[0] not in (0, 1)
                or not track_header[3] & 1):
            raise MediaConcatError('Unsupported video display header')
        display_matrix = track_header[matrix_offset:matrix_offset + 44]
        children = list(_boxes(sample[78:]))
        if any(kind not in (b'avcC', b'hvcC', b'btrt', b'pasp', b'colr', b'fiel')
               for kind, _ in children):
            raise MediaConcatError('Unsupported video sample metadata')
        required = b'avcC' if codec in (b'avc1', b'avc3') else b'hvcC'
        config = [body for kind, body in children if kind == required]
        if len(config) != 1 or not config[0] or len(config[0]) > 65536:
            raise MediaConcatError('Missing video decoder configuration')
        # Bitrate varies by episode; codec configuration and display metadata
        # must not. Compressor names and sample-entry timing fields are ignored.
        display = tuple(sorted((kind, body) for kind, body in children
                               if kind in (b'pasp', b'colr', b'fiel')))
        return media_type, codec, scale, width, height, config[0], display, display_matrix
    if codec != b'mp4a' or len(sample) < 28 or sample[8:10] != b'\0\0':
        raise MediaConcatError('Only standard AAC audio is supported for collections')
    channels, bits = struct.unpack_from('>HH', sample, 16)
    rate = struct.unpack_from('>I', sample, 24)[0]
    if not 1 <= channels <= 8 or bits != 16 or not 8000 << 16 <= rate <= 192000 << 16:
        raise MediaConcatError('Unsupported AAC audio parameters')
    children = list(_boxes(sample[28:]))
    if any(kind not in (b'esds', b'btrt') for kind, _ in children):
        raise MediaConcatError('Unsupported audio sample metadata')
    esds = [body for kind, body in children if kind == b'esds']
    if len(esds) != 1:
        raise MediaConcatError('Missing AAC sample metadata')
    return media_type, codec, scale, channels, bits, rate, _aac_config(esds[0])


def inspect_mp4(path, *, cancel_check=None):
    """Read bounded MP4 headers without loading media payloads into memory."""
    path = Path(path)
    moov = None
    has_media = False
    try:
        with path.open('rb') as stream:
            length = os.fstat(stream.fileno()).st_size
            pos = 0
            count = 0
            while pos < length:
                _cancel(cancel_check)
                count += 1
                if count > _MAX_BOXES or length - pos < 8:
                    raise MediaConcatError('Invalid MP4 top-level boxes')
                stream.seek(pos)
                header = stream.read(8)
                if len(header) != 8:
                    raise MediaConcatError('Truncated MP4')
                size, kind = struct.unpack('>I4s', header)
                head_size = 8
                if size == 1:
                    extra = stream.read(8)
                    if len(extra) != 8:
                        raise MediaConcatError('Truncated MP4 box')
                    size = struct.unpack('>Q', extra)[0]
                    head_size = 16
                elif size == 0:
                    size = length - pos
                if size < head_size or size > length - pos:
                    raise MediaConcatError('Invalid MP4 box size')
                if kind in (b'moof', b'mvex'):
                    raise MediaConcatError('Fragmented MP4 is unsupported for collections')
                if kind == b'moov':
                    if moov is not None or size - head_size > _MAX_MOOV:
                        raise MediaConcatError('MP4 metadata is too large or ambiguous')
                    moov = stream.read(size - head_size)
                    if len(moov) != size - head_size:
                        raise MediaConcatError('Truncated MP4 metadata')
                if kind == b'mdat' and size > head_size:
                    has_media = True
                pos += size
    except OSError as exc:
        raise StorageWriteError(path, exc, operation='read collection media') from exc
    if moov is None or not has_media:
        raise MediaConcatError('MP4 is incomplete or has no media')
    children = list(_boxes(moov))
    if any(kind == b'mvex' for kind, _ in children):
        raise MediaConcatError('Fragmented MP4 is unsupported for collections')
    _, duration = _duration(_one(moov, b'mvhd'))
    tracks = [body for kind, body in children if kind == b'trak']
    if not 1 <= len(tracks) <= 2:
        raise MediaConcatError('Collections require one video and optional AAC track')
    streams = tuple(_stream(track) for track in tracks)
    if tuple(item[0] for item in streams) not in ((b'vide',), (b'vide', b'soun')):
        raise MediaConcatError('Unsupported MP4 track ordering')
    _cancel(cancel_check)
    return MP4Info(duration, streams)


def _stop_process(process):
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _run_ffmpeg(command, *, cancel_check, timeout):
    error_tail = bytearray()
    storage_error = [False]
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    except OSError:
        raise MediaConcatError('FFmpeg could not start for collection assembly') from None

    def drain():
        try:
            while True:
                block = process.stderr.read(4096)
                if not block:
                    break
                error_tail.extend(block)
                if len(error_tail) > 65536:
                    del error_tail[:-65536]
                diagnostic = error_tail.decode('utf-8', errors='replace')
                if (storage_failure_message(diagnostic)
                        or 'error opening output' in diagnostic.lower()):
                    storage_error[0] = True
        finally:
            process.stderr.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        while process.poll() is None:
            _cancel(cancel_check)
            if time.monotonic() - started > timeout:
                raise MediaConcatError('Collection assembly timed out')
            time.sleep(0.1)
        _cancel(cancel_check)
    finally:
        _stop_process(process)
        reader.join(timeout=5)
    if storage_error[0]:
        raise StorageWriteError('collection temporary output',
                                OSError(errno.EIO, 'FFmpeg reported a storage write failure'))
    if process.returncode:
        # Never relay FFmpeg diagnostics: they can include full paths or URLs
        # stored inside malicious input metadata.
        raise MediaConcatError('FFmpeg could not concatenate these episode formats')


def _copy(source, target, check):
    with source.open('rb') as inp, target.open('xb') as out:
        while True:
            _cancel(check)
            block = inp.read(1024 * 1024)
            if not block:
                break
            out.write(block)
        out.flush()
        os.fsync(out.fileno())


def concat_mp4(inputs, output, *, ffmpeg, cancel_check=None):
    """Concatenate ordered compatible episodes; never overwrite ``output``.

    The output path is caller-owned staging, not the final collection name.
    On success it contains a validated MP4. On failure/cancellation only files
    created by this call are removed; all original episodes remain untouched.
    """
    paths = [Path(value).resolve() for value in inputs]
    output = Path(output).absolute()
    if not 1 <= len(paths) <= _MAX_EPISODES or len(set(paths)) != len(paths):
        raise MediaConcatError('Collection requires 1 to 1000 distinct episode files')
    if output.exists() or output.is_symlink():
        raise MediaConcatError('Collection staging output already exists')
    _cancel(cancel_check)
    def identity(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    try:
        identities = [identity(path) for path in paths]
    except OSError as exc:
        raise StorageWriteError(output, exc, operation='read collection media') from exc
    infos = [inspect_mp4(path, cancel_check=cancel_check) for path in paths]
    if any(info.streams != infos[0].streams for info in infos[1:]):
        raise MediaConcatError('Episode codecs, resolution or audio settings differ; stream-copy collection is unsafe')
    duration = sum(info.duration for info in infos)
    if duration > _MAX_DURATION:
        raise MediaConcatError('Collection exceeds the supported duration')
    published_identity = None
    try:
        with tempfile.TemporaryDirectory(prefix='.uav-concat-', dir=output.parent) as temporary:
            work = Path(temporary)
            for index, path in enumerate(paths):
                _cancel(cancel_check)
                stage = work / f'part-{index:04d}.mp4'
                try:
                    os.link(path, stage)
                except OSError:
                    _copy(path, stage, cancel_check)
            # Generated ASCII names eliminate concat-file escaping/injection,
            # including Unicode, quote and newline characters in source paths.
            listing = work / 'episodes.txt'
            listing.write_text('ffconcat version 1.0\n' + ''.join(
                f"file 'part-{index:04d}.mp4'\n" for index in range(len(paths))), encoding='ascii')
            staged_output = work / 'result.mp4'
            command = [str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
                       '-protocol_whitelist', 'file', '-f', 'concat', '-safe', '1',
                       '-i', str(listing), '-map', '0:v:0', '-map', '0:a:0?',
                       '-map_metadata', '-1', '-map_chapters', '-1', '-c', 'copy',
                       '-movflags', '+faststart', str(staged_output)]
            _cancel(cancel_check)
            _run_ffmpeg(command, cancel_check=cancel_check,
                        timeout=max(120, min(7200, duration * 2 + 60)))
            merged = inspect_mp4(staged_output, cancel_check=cancel_check)
            if merged.streams != infos[0].streams:
                raise MediaConcatError('Merged MP4 stream metadata did not match its episodes')
            if abs(merged.duration - duration) > max(0.25, len(paths) * 0.05):
                raise MediaConcatError('Merged MP4 duration did not match all episodes')
            if identities != [identity(path) for path in paths]:
                raise MediaConcatError('Episode file changed during collection assembly')
            _cancel(cancel_check)
            # Atomic, no-clobber publication where hardlinks are available.
            # Exclusive copy fallback supports FAT/exFAT and is safe because
            # output is a caller-owned staging filename, not the final MP4.
            try:
                os.link(staged_output, output)
                published_identity = identity(output)[:2]
            except FileExistsError:
                raise MediaConcatError('Collection staging output already exists') from None
            except OSError:
                created = False
                try:
                    with output.open('xb') as out, staged_output.open('rb') as inp:
                        created = True
                        stat = os.fstat(out.fileno())
                        published_identity = stat.st_dev, stat.st_ino
                        while True:
                            _cancel(cancel_check)
                            block = inp.read(1024 * 1024)
                            if not block:
                                break
                            out.write(block)
                        out.flush()
                        os.fsync(out.fileno())
                except BaseException:
                    if created:
                        output.unlink(missing_ok=True)
                    raise
            _cancel(cancel_check)
    except FileExistsError:
        raise MediaConcatError('Collection staging output already exists') from None
    except OSError as exc:
        if published_identity is not None:
            try:
                if identity(output)[:2] == published_identity:
                    output.unlink()
            except OSError:
                pass
        if isinstance(exc, StorageWriteError):
            raise
        raise StorageWriteError(output, exc, operation='assemble collection') from exc
    except BaseException:
        if published_identity is not None:
            try:
                if identity(output)[:2] == published_identity:
                    output.unlink()
            except OSError:
                pass
        raise
