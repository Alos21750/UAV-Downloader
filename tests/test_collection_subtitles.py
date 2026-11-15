"""Collection timeline updates invalidate only unchanged owned subtitles."""
import json
from pathlib import Path

import pytest

from uav_downloader.core.storage import StorageWriteError
from uav_downloader.subtitles import engine


@pytest.fixture
def media(tmp_path):
    video = tmp_path / 'HG123 - Collection.mp4'
    video.write_bytes(b'original episode timeline')
    paths = {language: Path(path) for language, path in engine.subtitle_paths(video).items()}
    manifest = engine._empty_subtitle_provenance()
    old_identity = engine._media_identity(video)
    for language, path in paths.items():
        path.write_text('1\n00:00:01,000 --> 00:00:02,000\n' + language + '\n', encoding='utf-8')
        engine._record_asr_track(manifest, language, str(path), 'old-settings', old_identity)
    provenance = Path(engine._subtitle_provenance_path(video))
    engine._save_subtitle_provenance(str(provenance), manifest)
    return video, paths, provenance, manifest


def test_replaced_collection_removes_all_stale_owned_languages_without_models(media, monkeypatch):
    video, paths, provenance, _manifest = media
    video.write_bytes(b'extended timeline with new episodes')
    monkeypatch.setattr(engine, '_prepare_runtime', lambda *_args: pytest.fail('No model setup expected'))
    removed = engine.invalidate_stale_managed_subtitles(video)
    assert set(removed) == {str(path) for path in paths.values()}
    assert all(not path.exists() for path in paths.values())
    assert json.loads(provenance.read_text())['tracks'] == {}
    assert engine.invalidate_stale_managed_subtitles(video) == ()


def test_current_media_preserves_tracks_even_with_different_runtime_settings(media):
    video, paths, provenance, _manifest = media
    original = provenance.read_bytes()
    assert engine.invalidate_stale_managed_subtitles(video) == ()
    assert all(path.exists() for path in paths.values())
    assert provenance.read_bytes() == original


def test_manual_edits_survive_and_lose_managed_ownership(media):
    video, paths, provenance, _manifest = media
    paths['zh-TW'].write_text('Hand corrected translation', encoding='utf-8')
    video.write_bytes(b'new timeline')
    removed = engine.invalidate_stale_managed_subtitles(video)
    assert set(removed) == {str(paths['ja']), str(paths['en'])}
    assert paths['zh-TW'].read_text() == 'Hand corrected translation'
    assert json.loads(provenance.read_text())['tracks'] == {}


@pytest.mark.parametrize('provenance_state', ['missing', 'corrupt', 'oversized'])
def test_untrustworthy_provenance_never_authorizes_deletion(media, provenance_state):
    video, paths, provenance, _manifest = media
    if provenance_state == 'missing':
        provenance.unlink()
    elif provenance_state == 'corrupt':
        provenance.write_text('{broken')
    else:
        provenance.write_bytes(b' ' * (engine.MAX_SUBTITLE_PROVENANCE_BYTES + 1))
    video.write_bytes(b'new timeline')
    assert engine.invalidate_stale_managed_subtitles(video) == ()
    assert all(path.exists() for path in paths.values())


@pytest.mark.parametrize('field,value', [('media_identity', None), ('srt_sha256', 'invalid'), ('generator', 'manual')])
def test_incomplete_or_foreign_ownership_preserves_track(media, field, value):
    video, paths, provenance, manifest = media
    manifest['tracks']['ja'][field] = value
    engine._save_subtitle_provenance(str(provenance), manifest)
    video.write_bytes(b'new timeline')
    engine.invalidate_stale_managed_subtitles(video)
    assert paths['ja'].exists()


def test_cancellation_before_cleanup_preserves_all_tracks(media):
    video, paths, _provenance, _manifest = media
    video.write_bytes(b'new timeline')
    with pytest.raises(engine.SubtitleCancelled):
        engine.invalidate_stale_managed_subtitles(video, cancel_check=lambda: True)
    assert all(path.exists() for path in paths.values())


def test_cancel_after_first_removal_is_retryable(media, monkeypatch):
    video, paths, provenance, _manifest = media
    video.write_bytes(b'new timeline')
    stopped = [False]
    remove = engine.os.remove

    def stop_after_remove(path):
        remove(path)
        stopped[0] = True

    monkeypatch.setattr(engine.os, 'remove', stop_after_remove)
    with pytest.raises(engine.SubtitleCancelled):
        engine.invalidate_stale_managed_subtitles(video, cancel_check=lambda: stopped[0])
    assert not paths['ja'].exists() and paths['en'].exists()
    assert 'ja' not in json.loads(provenance.read_text())['tracks']
    monkeypatch.setattr(engine.os, 'remove', remove)
    assert len(engine.invalidate_stale_managed_subtitles(video)) == 2


def test_manifest_failure_after_removal_recovers_on_retry(media, monkeypatch):
    video, paths, provenance, _manifest = media
    video.write_bytes(b'new timeline')
    save = engine._save_subtitle_provenance

    def fail_save(*_args):
        raise StorageWriteError(provenance, OSError('Disk full'))

    monkeypatch.setattr(engine, '_save_subtitle_provenance', fail_save)
    with pytest.raises(StorageWriteError):
        engine.invalidate_stale_managed_subtitles(video)
    assert not paths['ja'].exists() and paths['en'].exists()
    monkeypatch.setattr(engine, '_save_subtitle_provenance', save)
    engine.invalidate_stale_managed_subtitles(video)
    assert all(not path.exists() for path in paths.values())
    assert json.loads(provenance.read_text())['tracks'] == {}


def test_removal_io_error_stops_and_preserves_ownership_for_retry(media, monkeypatch):
    video, paths, provenance, _manifest = media
    video.write_bytes(b'new timeline')
    original = provenance.read_bytes()

    def fail_remove(_path):
        raise PermissionError('Locked by another process')

    monkeypatch.setattr(engine.os, 'remove', fail_remove)
    with pytest.raises(StorageWriteError):
        engine.invalidate_stale_managed_subtitles(video)
    assert all(path.exists() for path in paths.values())
    assert provenance.read_bytes() == original


def test_file_changed_during_hash_is_preserved(media, monkeypatch):
    video, paths, _provenance, _manifest = media
    video.write_bytes(b'new timeline')
    sha256 = engine._sha256

    def edit_during_hash(path, cancel_check=None):
        result = sha256(path, cancel_check)
        if str(path) == str(paths['ja']):
            Path(path).write_text('Concurrent manual edits', encoding='utf-8')
        return result

    monkeypatch.setattr(engine, '_sha256', edit_during_hash)
    engine.invalidate_stale_managed_subtitles(video)
    assert paths['ja'].read_text() == 'Concurrent manual edits'


def test_legacy_owned_provenance_is_recognized(media):
    video, paths, provenance, manifest = media
    legacy = Path(engine._legacy_subtitle_provenance_path(str(provenance)))
    manifest['kind'] = 'jable_subtitle_provenance'
    legacy.write_text(json.dumps(manifest))
    provenance.unlink()
    video.write_bytes(b'new timeline')
    assert len(engine.invalidate_stale_managed_subtitles(video)) == 3
    assert all(not path.exists() for path in paths.values())


def test_cleanup_holds_generation_lock_and_halts_if_media_changes(media, monkeypatch):
    video, paths, _provenance, _manifest = media
    video.write_bytes(b'new timeline')
    identity = engine._media_identity
    calls = [0]

    def changed(path):
        assert engine._generation_lock.locked()
        calls[0] += 1
        if calls[0] == 2:
            video.write_bytes(b'another collection update')
        return identity(path)

    monkeypatch.setattr(engine, '_media_identity', changed)
    with pytest.raises(engine.SubtitleError, match='changed'):
        engine.invalidate_stale_managed_subtitles(video)
    assert all(path.exists() for path in paths.values())


def test_media_storage_failure_during_cleanup_propagates(media, monkeypatch):
    video, paths, _provenance, _manifest = media
    video.write_bytes(b'new timeline')
    identity = engine._media_identity
    calls = [0]

    def failed(path):
        calls[0] += 1
        if calls[0] == 2:
            raise engine.SubtitleError('Media unavailable') from OSError('Drive disconnected')
        return identity(path)

    monkeypatch.setattr(engine, '_media_identity', failed)
    with pytest.raises(StorageWriteError):
        engine.invalidate_stale_managed_subtitles(video)
    assert all(path.exists() for path in paths.values())
