"""Owned, resumable Huangguo episode collections for UAV Watcher.

Only public episode identities are persisted, never playback URLs/headers.
Original episode downloads and Watcher history are deliberately not consulted.
"""
from concurrent.futures import CancelledError
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from uav_downloader.core.media_concat import concat_mp4, inspect_mp4, MediaConcatError
from uav_downloader.core.storage import StorageWriteError
from uav_downloader.core.video_identity import huangguo_video_code


_MAX_EPISODES = 1000
_MAX_STATE_BYTES = 4 * 1024 * 1024
_RESOLUTIONS = frozenset({'highest', 'lowest', '1080', '720', '480', '360'})


class CollectionError(ValueError):
    """A series cannot safely be collected; keep previous files and retry."""


class CollectionBusyError(CollectionError):
    """Another process owns this collection job."""


def _cancel(check):
    if check is not None and check():
        raise CancelledError('Huangguo collection cancelled')


def _episode(row):
    if not isinstance(row, dict):
        raise CollectionError('Invalid Huangguo episode metadata')
    code = huangguo_video_code(row.get('url'))
    match = re.fullmatch(r'huangguo-([1-9][0-9]*)-ep-([1-9][0-9]*)', code or '')
    if not match:
        raise CollectionError('Invalid Huangguo episode URL')
    series, number_text = match.groups()
    number = int(number_text)
    if len(series) > 19 or number > _MAX_EPISODES:
        raise CollectionError('Huangguo collection exceeds episode/identity bounds')
    if (row.get('series_id') is not None and str(row['series_id']) != series
            or row.get('episode') is not None and str(row['episode']) != str(number)):
        raise CollectionError('Huangguo episode identity mismatch')
    url = f'https://huangguoai.com/video/{series}/'
    if number > 1:
        url += f'ep-{number}/'
    return dict(row, url=url, series_id=series, episode=number)


def _title(row):
    title = re.sub(r'\s*-\s*EP\s+\d+\s*$', '', str(row.get('title') or ''), flags=re.I)
    return title.strip()[:300] or f'Huangguo {row["series_id"]}'


def _episodes(rows, *, require_contiguous):
    if not isinstance(rows, (list, tuple)) or not rows or len(rows) > 4000:
        raise CollectionError('Huangguo collection requires 1–1000 public episodes')
    found = {}
    series = None
    for raw in rows:
        row = _episode(raw)
        if series is not None and series != row['series_id']:
            raise CollectionError('Huangguo collection contains different series')
        series = row['series_id']
        found.setdefault(row['episode'], row)
        if len(found) > _MAX_EPISODES:
            raise CollectionError('Huangguo collection exceeds 1000 public episodes')
    ordered = [found[number] for number in sorted(found)]
    if require_contiguous and [row['episode'] for row in ordered] != list(range(1, len(ordered) + 1)):
        raise CollectionError('Huangguo collection has missing episodes; waiting for a complete EP 1…N list')
    return ordered


def group_collection_rows(rows):
    """Group expanded listing rows before individual-episode history filtering.

    Missing episode numbers are rejected by build_collection, not here, so a
    malformed/incomplete series need not prevent unrelated series from running.
    """
    if not isinstance(rows, (list, tuple)) or len(rows) > 4000:
        raise CollectionError('Huangguo listing exceeds collection bounds')
    groups = {}
    for raw in rows:
        row = _episode(raw)
        groups.setdefault(row['series_id'], []).append(row)
    result = []
    for series, group in groups.items():
        ordered = _episodes(group, require_contiguous=False)
        candidate = dict(ordered[0])
        candidate.update(url=f'https://huangguoai.com/video/{series}/',
                         title=_title(ordered[0]), _huangguo_collection=True,
                         _collection_episodes=ordered, series_id=series)
        candidate.pop('episode', None)
        result.append(candidate)
    return result


def _safe_path(base, relative):
    """Reject writable-state path escapes and symlink/junction components."""
    if not isinstance(relative, str) or len(relative) > 2048 or '\\' in relative:
        raise CollectionError('Unsafe collection state path')
    bits = relative.split('/')
    if not bits or any(bit in ('', '.', '..') or ':' in bit for bit in bits):
        raise CollectionError('Unsafe collection state path')
    path = base
    for bit in bits:
        path = path / bit
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise CollectionError('Linked collection paths are not supported')
    if not path.resolve().is_relative_to(base.resolve()):
        raise CollectionError('Collection state path escapes its directory')
    return path


def _digest(path, check):
    _cancel(check)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise CollectionError('Collection media file is missing or empty')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            _cancel(check)
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise CollectionError('Collection media changed during validation')
    _cancel(check)
    return {'size': after.st_size, 'sha256': digest.hexdigest()}


def _valid_receipt(receipt):
    return (isinstance(receipt, dict) and type(receipt.get('size')) is int
            and receipt['size'] > 0 and isinstance(receipt.get('sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', receipt['sha256']) is not None)


def _matches(path, receipt, check, *, structural=True):
    if not _valid_receipt(receipt):
        return False
    try:
        if not path.is_file() or path.stat().st_size != receipt['size']:
            return False
        if _digest(path, check) != {key: receipt[key] for key in ('size', 'sha256')}:
            return False
        if structural:
            inspect_mp4(path, cancel_check=check)
        return True
    except (FileNotFoundError, MediaConcatError):
        return False


def _filename(series, title):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title)
    clean = clean.strip(' .')[:72].rstrip(' .') or 'Huangguo'
    return f'HG{series} - {clean} - Collection.mp4'


def _signature(episodes, resolution):
    value = [resolution, [row['url'] for row in episodes]]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def _write_state(folder, state):
    path = _safe_path(folder, 'state.json')
    data = json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) > _MAX_STATE_BYTES:
        raise CollectionError('Collection state exceeds the safety limit')
    temporary = _safe_path(folder, f'state-{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_state(folder, series, title):
    path = _safe_path(folder, 'state.json')
    if not path.exists():
        return {'schema': 1, 'series_id': series, 'filename': _filename(series, title), 'episodes': {}}
    if path.stat().st_size > _MAX_STATE_BYTES:
        raise CollectionError('Collection state exceeds the safety limit')
    try:
        with path.open('rb') as handle:
            state = json.loads(handle.read(_MAX_STATE_BYTES + 1))
    except (ValueError, UnicodeError):
        raise CollectionError('Collection state is corrupt; existing files were preserved') from None
    if (not isinstance(state, dict) or state.get('schema') != 1
            or state.get('series_id') != series or not isinstance(state.get('filename'), str)
            or not state['filename'].startswith(f'HG{series} - ')
            or not state['filename'].endswith(' - Collection.mp4')
            or len(state['filename']) > 160 or '/' in state['filename']
            or not isinstance(state.get('episodes'), dict)):
        raise CollectionError('Collection state is invalid; existing files were preserved')
    for resolution, entries in state['episodes'].items():
        if resolution not in _RESOLUTIONS or not isinstance(entries, dict) or len(entries) > _MAX_EPISODES:
            raise CollectionError('Invalid collection cache records')
        for url, receipt in entries.items():
            row = _episode({'url': url})
            if row['series_id'] != series or not _valid_receipt(receipt):
                raise CollectionError('Invalid collection cache receipt')
            relative = receipt.get('path')
            prefix = f'parts/{resolution}/ep-{row["episode"]}/'
            if not isinstance(relative, str) or not relative.startswith(prefix):
                raise CollectionError('Invalid collection cache path')
            _safe_path(folder, relative)
    for key in ('complete', 'pending'):
        receipt = state.get(key)
        if receipt is not None:
            if (not _valid_receipt(receipt)
                    or not isinstance(receipt.get('signature'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', receipt['signature'])):
                raise CollectionError('Invalid collection publication receipt')
            urls = receipt.get('episode_urls')
            if not isinstance(urls, list) or not urls or len(urls) > _MAX_EPISODES:
                raise CollectionError('Invalid published collection episode list')
            published = _episodes([{'url': url} for url in urls], require_contiguous=True)
            if (published[0]['series_id'] != series
                    or [row['url'] for row in published] != urls):
                raise CollectionError('Invalid published collection episode identity')
            if key == 'pending':
                if not re.fullmatch(r'build-[a-f0-9]{32}\.mp4', str(receipt.get('staged') or '')):
                    raise CollectionError('Invalid pending collection path')
    return state


@contextmanager
def _series_lock(folder):
    path = _safe_path(folder, 'collection.lock')
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise CollectionBusyError('Another Watcher is building this Huangguo collection') from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise CollectionBusyError('Another Watcher is building this Huangguo collection') from None
            acquired = True
            yield
        finally:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _publish(staged, output, previous, check):
    _cancel(check)
    if output.exists():
        if not _matches(output, previous, check, structural=False):
            raise CollectionError('Existing collection was not created by this job or was modified; not overwritten')
        _cancel(check)
        os.replace(staged, output)
    else:
        # Linking within this destination is atomic and refuses a raced-in file.
        # The staging path is app-owned and removed only after publication.
        _cancel(check)
        try:
            os.link(staged, output)
        except OSError:
            if os.name != 'nt':
                raise
            # FAT/exFAT do not support hardlinks. Windows rename is atomic and
            # refuses an existing destination, unlike POSIX rename.
            _cancel(check)
            os.rename(staged, output)
            return
        staged.unlink()


def _recover_pending(folder, state, output, check):
    pending = state.get('pending')
    if not pending:
        return
    _cancel(check)
    if not _matches(output, pending, check):
        if output.exists() and not _matches(output, state.get('complete'), check, structural=False):
            raise CollectionError('Collection publication conflicts with a modified file; files preserved')
        staged = _safe_path(folder, pending['staged'])
        if not staged.exists():
            state.pop('pending')
            _write_state(folder, state)
            return
        if not _matches(staged, pending, check):
            raise CollectionError('Pending collection is damaged; files preserved')
        _publish(staged, output, state.get('complete'), check)
    _cancel(check)
    state['complete'] = {key: pending[key] for key in ('signature', 'size', 'sha256', 'episode_urls')}
    state.pop('pending')
    _write_state(folder, state)


def build_collection(episodes, destination, *, resolution, download_episode,
                     ffmpeg, cancel_check=None, log=None, on_ready=None):
    """Build/update one current public EP1…N collection and return its MP4.

    download_episode(row, unique_attempt_directory) must return only a complete
    downloaded MP4 path. Failed attempt directories and previous valid outputs
    are preserved. No individual episode is marked in normal Watcher history.
    Optional on_ready(output) runs under the series lock after the complete
    receipt is saved, including cache hits. Its failure preserves the finished
    video so subtitle or other follow-up work can retry without redownloading.
    """
    ordered = _episodes(episodes, require_contiguous=True)
    if not isinstance(resolution, str) or resolution not in _RESOLUTIONS:
        raise CollectionError('Invalid collection resolution')
    root = Path(destination).expanduser().resolve()
    series = ordered[0]['series_id']
    signature = _signature(ordered, resolution)
    say = log or (lambda _message: None)
    try:
        _cancel(cancel_check)
        root.mkdir(parents=True, exist_ok=True)
        folder = _safe_path(root, f'.uav-huangguo/{series}')
        folder.mkdir(parents=True, exist_ok=True)
        with _series_lock(folder):
            _cancel(cancel_check)
            state = _load_state(folder, series, _title(ordered[0]))
            output = _safe_path(root, state['filename'])
            _recover_pending(folder, state, output, cancel_check)
            complete = state.get('complete')
            episode_urls = [row['url'] for row in ordered]
            if complete and not set(complete['episode_urls']).issubset(episode_urls):
                raise CollectionError('Public episode list shrank; previous collection preserved until all episodes return')
            if (complete and complete['signature'] == signature
                    and _matches(output, complete, cancel_check)):
                say(f'[COLLECTION] HG{series}: current {len(ordered)}-episode collection retained')
                if on_ready is not None:
                    _cancel(cancel_check)
                    on_ready(output)
                _cancel(cancel_check)
                return output
            if output.exists() and not _matches(output, complete, cancel_check, structural=False):
                raise CollectionError('Existing collection was not created by this job or was modified; not overwritten')
            cache = state['episodes'].setdefault(resolution, {})
            paths = []
            for row in ordered:
                _cancel(cancel_check)
                receipt = cache.get(row['url'])
                part = _safe_path(folder, receipt['path']) if receipt else None
                if part is not None and _matches(part, receipt, cancel_check):
                    say(f'[COLLECTION] EP {row["episode"]}: verified cache retained')
                else:
                    parent = _safe_path(folder, f'parts/{resolution}/ep-{row["episode"]}')
                    parent.mkdir(parents=True, exist_ok=True)
                    attempt = Path(tempfile.mkdtemp(prefix='attempt-', dir=parent))
                    say(f'[COLLECTION] EP {row["episode"]}/{len(ordered)}: downloading')
                    part = Path(download_episode(dict(row), attempt)).absolute()
                    _cancel(cancel_check)
                    if not part.is_relative_to(attempt) or part == attempt:
                        raise CollectionError('Episode downloader returned a path outside its isolated attempt')
                    relative = part.relative_to(folder).as_posix()
                    part = _safe_path(folder, relative)
                    inspect_mp4(part, cancel_check=cancel_check)
                    receipt = dict(_digest(part, cancel_check), path=relative)
                    cache[row['url']] = receipt
                    _write_state(folder, state)
                paths.append(part)
            _cancel(cancel_check)
            say(f'[COLLECTION] HG{series}: merging {len(paths)} episodes in numeric EP order')
            staged = _safe_path(folder, f'build-{uuid.uuid4().hex}.mp4')
            concat_mp4(paths, staged, ffmpeg=ffmpeg, cancel_check=cancel_check)
            _cancel(cancel_check)
            inspect_mp4(staged, cancel_check=cancel_check)
            pending = dict(_digest(staged, cancel_check), signature=signature,
                           staged=staged.name, episode_urls=episode_urls)
            state['pending'] = pending
            _write_state(folder, state)
            _publish(staged, output, complete, cancel_check)
            _cancel(cancel_check)
            state['complete'] = {key: pending[key] for key in ('signature', 'size', 'sha256', 'episode_urls')}
            state.pop('pending')
            _write_state(folder, state)
            say(f'[COLLECTION] HG{series}: {len(paths)}-episode MP4 ready')
            if on_ready is not None:
                _cancel(cancel_check)
                on_ready(output)
            _cancel(cancel_check)
            return output
    except StorageWriteError:
        raise
    except OSError as exc:
        raise StorageWriteError(root, exc, operation='collection') from exc
