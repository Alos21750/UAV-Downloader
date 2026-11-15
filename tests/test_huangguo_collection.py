"""Offline ownership, retry and publication contracts for series collections."""
from concurrent.futures import CancelledError
import json
import os
from pathlib import Path

import pytest

from uav_downloader.apps import huangguo_collection as collection
from uav_downloader.core.storage import StorageWriteError


def episodes(numbers=(1, 2, 3), series='123', title='Public drama'):
    return [dict(url=f'https://huangguoai.com/video/{series}/' +
                 (f'ep-{number}/' if number > 1 else ''),
                 title=f'{title} - EP {number}', episode=number,
                 series_id=series, date='2026-11-15') for number in numbers]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    downloaded, merged, logs = [], [], []

    def inspect(path, **_kwargs):
        if not Path(path).read_bytes().startswith(b'mp4:'):
            raise collection.MediaConcatError('Invalid MP4')

    def download(row, attempt):
        downloaded.append((row['episode'], attempt))
        output = attempt / 'episode.mp4'
        output.write_bytes(f'mp4:{row["episode"]}'.encode())
        return output

    def concat(inputs, output, **_kwargs):
        assert not output.exists()
        merged.append(list(inputs))
        output.write_bytes(b'mp4:' + b'|'.join(path.read_bytes()[4:] for path in inputs))

    monkeypatch.setattr(collection, 'inspect_mp4', inspect)
    monkeypatch.setattr(collection, 'concat_mp4', concat)

    def build(rows=None, **kwargs):
        return collection.build_collection(
            rows if rows is not None else episodes(), tmp_path,
            resolution=kwargs.pop('resolution', 'highest'),
            download_episode=kwargs.pop('download_episode', download),
            ffmpeg='reviewed-ffmpeg', log=logs.append, **kwargs)

    return tmp_path, downloaded, merged, logs, build, download


def state(root):
    return json.loads((root / '.uav-huangguo/123/state.json').read_text(encoding='utf-8'))


def test_grouping_is_numeric_canonical_and_preserves_series_metadata():
    rows = episodes((10, 2, 1)) + episodes((1,), '456')
    rows[2]['url'] = 'https://www.huangguoai.com/video/123/ep-1/'
    candidates = collection.group_collection_rows(rows)
    assert len(candidates) == 2
    first = candidates[0]
    assert first['_huangguo_collection'] is True
    assert first['url'] == 'https://huangguoai.com/video/123/'
    assert first['title'] == 'Public drama'
    assert first['date'] == '2026-11-15'
    assert 'episode' not in first
    assert [row['episode'] for row in first['_collection_episodes']] == [1, 2, 10]


def test_grouping_deduplicates_episode_aliases():
    rows = episodes((1, 1, 2))
    assert len(collection.group_collection_rows(rows)[0]['_collection_episodes']) == 2


def test_grouping_limit_counts_unique_episodes_not_category_duplicates():
    rows = episodes(range(1, 1001)) * 2
    assert len(collection.group_collection_rows(rows)[0]['_collection_episodes']) == 1000


@pytest.mark.parametrize('bad', [
    {'url': 'https://evil.example/video/123/'},
    {'url': 'https://huangguoai.com/video/123/', 'series_id': '456'},
    {'url': 'https://huangguoai.com/video/123/ep-2/', 'episode': 1},
    {'url': 'https://huangguoai.com/video/123/ep-1001/'},
    {'url': 'https://huangguoai.com/video/123/?token=secret'},
])
def test_grouping_rejects_untrusted_identity(bad):
    with pytest.raises(collection.CollectionError):
        collection.group_collection_rows([bad])


@pytest.mark.parametrize('rows', [[], episodes((2,)), episodes((1, 3)), episodes((1,)) + episodes((2,), '456')])
def test_missing_or_mixed_episodes_fail_before_download(rig, rows):
    root, downloaded, merged, _logs, build, _download = rig
    with pytest.raises(collection.CollectionError):
        build(rows)
    assert not downloaded and not merged
    assert not list(root.glob('*.mp4'))


def test_numeric_order_and_current_collection_reuse(rig):
    root, downloaded, merged, _logs, build, _download = rig
    output = build(episodes((10, 9, 8, 7, 6, 5, 4, 3, 2, 1)))
    assert output.read_bytes() == b'mp4:1|2|3|4|5|6|7|8|9|10'
    assert [number for number, _attempt in downloaded] == list(range(1, 11))
    assert build(episodes(range(1, 11))) == output
    assert len(downloaded) == 10 and len(merged) == 1
    assert all(attempt.is_relative_to(root / '.uav-huangguo/123') for _, attempt in downloaded)
    assert len(list(root.glob('*.mp4'))) == 1


def test_new_episode_reuses_old_parts_and_stable_filename(rig):
    root, downloaded, merged, _logs, build, _download = rig
    output = build(episodes((1, 2), title='Original title'))
    updated = build(episodes((1, 2, 3), title='New title'))
    assert updated == output
    assert updated.read_bytes() == b'mp4:1|2|3'
    assert [number for number, _attempt in downloaded] == [1, 2, 3]
    assert len(merged) == 2
    assert len(list(root.glob('*.mp4'))) == 1


@pytest.mark.parametrize('resolution', ['highest', '720'])
def test_refresh_cannot_shrink_a_published_collection(rig, resolution):
    root, downloaded, merged, _logs, build, _download = rig
    output = build()
    original_state = state(root)
    with pytest.raises(collection.CollectionError, match='shrank'):
        build(episodes((1, 2)), resolution=resolution)
    assert output.read_bytes() == b'mp4:1|2|3'
    assert len(downloaded) == 3 and len(merged) == 1
    assert state(root) == original_state


@pytest.mark.skipif(os.name != 'nt', reason='Windows atomic no-clobber rename fallback')
def test_first_publication_supports_filesystems_without_hardlinks(rig, monkeypatch):
    _root, _downloaded, _merged, _logs, build, _download = rig

    def no_hardlinks(*_args):
        raise OSError('Filesystem does not support hardlinks')

    monkeypatch.setattr(collection.os, 'link', no_hardlinks)
    assert build().read_bytes() == b'mp4:1|2|3'


@pytest.mark.skipif(os.name != 'nt', reason='Windows atomic no-clobber rename fallback')
def test_first_publication_fallback_preserves_a_raced_in_user_file(rig, monkeypatch):
    root, _downloaded, _merged, _logs, build, _download = rig

    def raced(_staged, output):
        output.write_bytes(b'user created this while building')
        raise OSError('Cannot create hardlink over existing destination')

    monkeypatch.setattr(collection.os, 'link', raced)
    with pytest.raises(StorageWriteError):
        build()
    assert (root / 'HG123 - Public drama - Collection.mp4').read_bytes() == b'user created this while building'
    assert 'pending' in state(root)


def test_failure_keeps_completed_cache_and_orphan_attempt_is_not_reused(rig):
    root, downloaded, merged, _logs, build, download = rig
    orphan = []

    def fail_second(row, attempt):
        result = download(row, attempt)
        if row['episode'] == 2:
            orphan.append(result)
            raise ValueError('Interrupted transfer')
        return result

    with pytest.raises(ValueError, match='Interrupted'):
        build(download_episode=fail_second)
    assert not merged and not list(root.glob('*.mp4'))
    output = build()
    assert output.read_bytes() == b'mp4:1|2|3'
    assert [number for number, _attempt in downloaded] == [1, 2, 2, 3]
    second_attempts = [attempt for number, attempt in downloaded if number == 2]
    assert second_attempts[0] != second_attempts[1]
    assert orphan[0].exists()


def test_resolution_change_uses_a_separate_cache(rig):
    _root, downloaded, merged, _logs, build, _download = rig
    output = build(episodes((1, 2)))
    assert build(episodes((1, 2)), resolution='720') == output
    assert len(downloaded) == 4 and len(merged) == 2
    assert 'highest' in downloaded[0][1].parts
    assert '720' in downloaded[-1][1].parts


def test_damaged_cache_is_preserved_and_redownloaded(rig):
    root, downloaded, _merged, _logs, build, _download = rig
    build(episodes((1, 2)))
    old_part = downloaded[0][1] / 'episode.mp4'
    old_part.write_bytes(b'broken')
    output = build(episodes((1, 2, 3)))
    assert output.read_bytes() == b'mp4:1|2|3'
    assert old_part.read_bytes() == b'broken'
    assert [number for number, _attempt in downloaded] == [1, 2, 1, 3]


def test_user_modified_final_is_never_overwritten(rig):
    _root, downloaded, _merged, _logs, build, _download = rig
    output = build(episodes((1, 2)))
    output.write_bytes(b'user video')
    with pytest.raises(collection.CollectionError, match='modified'):
        build()
    assert output.read_bytes() == b'user video'
    assert len(downloaded) == 2


def test_unowned_final_is_never_overwritten(rig):
    root, downloaded, _merged, _logs, build, _download = rig
    output = root / 'HG123 - Public drama - Collection.mp4'
    output.write_bytes(b'foreign')
    with pytest.raises(collection.CollectionError, match='not created'):
        build()
    assert output.read_bytes() == b'foreign'
    assert not downloaded


def test_cancel_before_download_does_not_create_state(rig):
    root, downloaded, merged, _logs, build, _download = rig
    with pytest.raises(CancelledError):
        build(cancel_check=lambda: True)
    assert not downloaded and not merged
    assert not (root / '.uav-huangguo').exists()


def test_cancel_during_download_does_not_record_completion(rig):
    root, _downloaded, merged, _logs, build, download = rig
    stopped = [False]

    def cancelled(row, attempt):
        output = download(row, attempt)
        stopped[0] = True
        return output

    with pytest.raises(CancelledError):
        build(download_episode=cancelled, cancel_check=lambda: stopped[0])
    assert not merged and not list(root.glob('*.mp4'))
    assert not (root / '.uav-huangguo/123/state.json').exists()


def test_cancel_during_concat_preserves_previous_output(rig, monkeypatch):
    root, _downloaded, _merged, _logs, build, _download = rig
    output = build(episodes((1, 2)))
    previous = output.read_bytes()

    def cancelled(_inputs, staged, **_kwargs):
        staged.write_bytes(b'mp4:partial')
        raise CancelledError()

    monkeypatch.setattr(collection, 'concat_mp4', cancelled)
    with pytest.raises(CancelledError):
        build()
    assert output.read_bytes() == previous
    assert state(root)['complete']['signature'] == collection._signature(episodes((1, 2)), 'highest')


def test_crash_after_replace_recovers_pending_receipt_without_redownload(rig, monkeypatch):
    root, downloaded, merged, _logs, build, _download = rig
    real_write = collection._write_state
    failed = [False]

    def crash(folder, value):
        if 'complete' in value and 'pending' not in value and not failed[0]:
            failed[0] = True
            raise OSError('simulated state write failure after publication')
        real_write(folder, value)

    monkeypatch.setattr(collection, '_write_state', crash)
    with pytest.raises(StorageWriteError):
        build()
    assert 'pending' in state(root)
    output = build()
    assert output.read_bytes() == b'mp4:1|2|3'
    assert len(downloaded) == 3 and len(merged) == 1
    assert 'pending' not in state(root)


def test_recovered_pending_publication_still_prevents_episode_shrink(rig, monkeypatch):
    root, downloaded, merged, _logs, build, _download = rig
    real_write = collection._write_state

    def crash(folder, value):
        if 'complete' in value and 'pending' not in value:
            raise OSError('simulated crash after publication')
        real_write(folder, value)

    monkeypatch.setattr(collection, '_write_state', crash)
    with pytest.raises(StorageWriteError):
        build()
    monkeypatch.setattr(collection, '_write_state', real_write)
    with pytest.raises(collection.CollectionError, match='shrank'):
        build(episodes((1, 2)))
    assert list(root.glob('*.mp4'))[0].read_bytes() == b'mp4:1|2|3'
    assert len(downloaded) == 3 and len(merged) == 1
    assert state(root)['complete']['episode_urls'] == [row['url'] for row in episodes()]


def test_pending_before_publish_recovers_staged_media(rig, monkeypatch):
    root, downloaded, merged, _logs, build, _download = rig
    real_publish = collection._publish
    monkeypatch.setattr(collection, '_publish', lambda *_args: (_ for _ in ()).throw(CancelledError()))
    with pytest.raises(CancelledError):
        build()
    assert 'pending' in state(root)
    assert not list(root.glob('*.mp4'))
    monkeypatch.setattr(collection, '_publish', real_publish)
    assert build().read_bytes() == b'mp4:1|2|3'
    assert len(downloaded) == 3 and len(merged) == 1


def test_corrupt_state_is_preserved_fail_closed(rig):
    root, downloaded, _merged, _logs, build, _download = rig
    folder = root / '.uav-huangguo/123'
    folder.mkdir(parents=True)
    path = folder / 'state.json'
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(collection.CollectionError, match='corrupt'):
        build()
    assert path.read_text() == '{broken' and not downloaded


def test_oversized_state_is_rejected_before_json_parse(rig):
    root, downloaded, _merged, _logs, build, _download = rig
    folder = root / '.uav-huangguo/123'
    folder.mkdir(parents=True)
    (folder / 'state.json').write_bytes(b' ' * (collection._MAX_STATE_BYTES + 1))
    with pytest.raises(collection.CollectionError, match='safety limit'):
        build()
    assert not downloaded


def test_state_cannot_escape_output_root(rig):
    root, downloaded, _merged, _logs, build, _download = rig
    build()
    payload = state(root)
    payload['filename'] = 'HG123 - ../outside - Collection.mp4'
    (root / '.uav-huangguo/123/state.json').write_text(json.dumps(payload))
    with pytest.raises(collection.CollectionError):
        build()
    assert len(downloaded) == 3


def test_download_callback_cannot_publish_external_file(rig):
    root, _downloaded, merged, _logs, build, _download = rig
    external = root / 'user.mp4'
    external.write_bytes(b'mp4:foreign')
    with pytest.raises(collection.CollectionError, match='outside'):
        build(download_episode=lambda _row, _attempt: external)
    assert external.read_bytes() == b'mp4:foreign'
    assert not merged


def test_collection_lock_prevents_a_second_job(rig):
    root, downloaded, _merged, _logs, build, _download = rig
    folder = root / '.uav-huangguo/123'
    folder.mkdir(parents=True)
    with collection._series_lock(folder):
        with pytest.raises(collection.CollectionBusyError):
            build()
    assert not downloaded
    assert build().is_file()


@pytest.mark.parametrize('cached', [False, True])
def test_ready_callback_holds_series_lock_after_complete_receipt(rig, cached):
    root, downloaded, merged, _logs, build, _download = rig
    if cached:
        build()
    calls = []

    def ready(output):
        calls.append(output)
        assert output.read_bytes() == b'mp4:1|2|3'
        saved = state(root)
        assert 'complete' in saved and 'pending' not in saved
        with pytest.raises(collection.CollectionBusyError):
            build(episodes((1, 2, 3, 4)))

    output = build(on_ready=ready)
    assert calls == [output]
    assert len(downloaded) == 3 and len(merged) == 1
    assert build() == output  # The lock is released after the callback.


@pytest.mark.parametrize('cached', [False, True])
def test_ready_callback_failure_retries_cached_video_without_redownload(rig, cached):
    root, downloaded, merged, _logs, build, _download = rig
    if cached:
        build()
    calls = []

    def failed(output):
        calls.append(output)
        raise RuntimeError('subtitle generation failed')

    with pytest.raises(RuntimeError, match='subtitle generation failed'):
        build(on_ready=failed)
    assert 'complete' in state(root) and 'pending' not in state(root)
    output = build(on_ready=calls.append)
    assert calls == [output, output]
    assert len(downloaded) == 3 and len(merged) == 1


def test_ready_callback_cancellation_keeps_completed_video_for_retry(rig):
    root, downloaded, merged, _logs, build, _download = rig
    cancelled = [False]

    def ready(_output):
        cancelled[0] = True

    with pytest.raises(CancelledError):
        build(on_ready=ready, cancel_check=lambda: cancelled[0])
    assert 'complete' in state(root)
    assert build().is_file()
    assert len(downloaded) == 3 and len(merged) == 1


def test_storage_error_propagates_with_previous_collection_preserved(rig):
    _root, _downloaded, _merged, _logs, build, _download = rig
    output = build(episodes((1, 2)))
    previous = output.read_bytes()

    def disk_full(_row, attempt):
        raise OSError(28, 'No space left on device', str(attempt))

    with pytest.raises(StorageWriteError):
        build(download_episode=disk_full)
    assert output.read_bytes() == previous


def test_filename_sanitization_does_not_create_nested_directories(rig):
    root, _downloaded, _merged, _logs, build, _download = rig
    output = build(episodes((1,), title='../../bad:*?name'))
    assert output.parent == root
    assert output.name.startswith('HG123 - ')
    assert not any(character in output.name for character in '<>:"/\\|?*')


def test_symlink_cache_is_not_followed(rig):
    root, _downloaded, _merged, _logs, build, _download = rig
    elsewhere = root / 'user'
    elsewhere.mkdir()
    try:
        (root / '.uav-huangguo').symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip('Host cannot create symlinks for this test')
    with pytest.raises(collection.CollectionError, match='Linked'):
        build()
    assert list(elsewhere.iterdir()) == []
