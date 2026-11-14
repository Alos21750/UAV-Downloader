"""Model activation must retain a recoverable previous installation on stop."""
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from uav_downloader.subtitles import engine


@pytest.fixture(params=['translation', 'reazon'])
def installation(request, tmp_path, monkeypatch):
    kind = request.param
    if kind == 'translation':
        runtime = tmp_path / 'translation' / f'v{engine.TRANSLATION_PACK_VERSION}'
        prepare = engine._prepare_translation_runtime_locked
        verifier = '_verify_translation_install'
    else:
        runtime = tmp_path / 'asr' / f'reazonspeech-v{engine.REAZONSPEECH_PACK_VERSION}'
        prepare = engine._prepare_reazonspeech_runtime_locked
        verifier = '_verify_reazonspeech_install'
    runtime.mkdir(parents=True)
    (runtime / 'original.bin').write_bytes(b'preserve original installation')
    cancelled = threading.Event()
    monkeypatch.setattr(engine, '_cache_root', lambda: str(tmp_path))
    monkeypatch.setattr(engine, '_component_install_candidates', lambda *a: [])
    monkeypatch.setattr(engine, '_find_verified_component_archive', lambda *a: 'controlled.zip')
    monkeypatch.setattr(engine, '_ensure_asr_temp_space', lambda *a: None)

    def extract(archive, destination, *args, **kwargs):
        (Path(destination) / 'new.bin').write_bytes(b'new verified installation')

    def verify(path, **kwargs):
        if (Path(path) / 'new.bin').is_file():
            return {} if kind == 'translation' else str(path)
        raise engine.SubtitleError('controlled invalid previous installation')

    monkeypatch.setattr(engine, '_safe_extract_zip', extract)
    monkeypatch.setattr(engine, verifier, verify)
    return prepare, runtime, cancelled, verifier


@pytest.mark.parametrize('boundary', ['old_moved', 'new_activated', 'activation_failed'])
def test_interrupted_model_activation_restores_previous_installation(
        installation, monkeypatch, boundary):
    prepare, runtime, cancelled, _ = installation
    previous = Path(str(runtime) + '.previous')
    real_replace = os.replace

    def replace(source, destination):
        activating = Path(destination) == runtime and '-install-' in str(source)
        if activating and boundary == 'activation_failed':
            raise OSError('controlled activation failure')
        result = real_replace(source, destination)
        if ((Path(source) == runtime and boundary == 'old_moved')
                or (activating and boundary == 'new_activated')):
            cancelled.set()
        return result

    monkeypatch.setattr(engine.os, 'replace', replace)
    expected = OSError if boundary == 'activation_failed' else engine.SubtitleCancelled
    with pytest.raises(expected):
        prepare(None, cancelled.is_set)
    assert (runtime / 'original.bin').read_bytes() == b'preserve original installation'
    assert not (runtime / 'new.bin').exists()
    assert not previous.exists()
    assert not list(runtime.parent.glob('*-install-*'))


def test_stop_after_backup_verification_does_not_replace_active_tree(
        installation, monkeypatch):
    prepare, runtime, cancelled, verifier = installation
    previous = Path(str(runtime) + '.previous')
    previous.mkdir()
    (previous / 'backup.bin').write_bytes(b'verified backup')
    old_verify = getattr(engine, verifier)

    def verify(path, **kwargs):
        if Path(path) == previous:
            cancelled.set()
            return {} if verifier == '_verify_translation_install' else str(path)
        return old_verify(path, **kwargs)

    monkeypatch.setattr(engine, verifier, verify)
    with pytest.raises(engine.SubtitleCancelled):
        prepare(None, cancelled.is_set)
    assert (runtime / 'original.bin').read_bytes() == b'preserve original installation'
    assert (previous / 'backup.bin').read_bytes() == b'verified backup'


def test_failed_model_rollback_keeps_backup_for_next_preparation(installation, monkeypatch):
    prepare, runtime, cancelled, verifier = installation
    previous = Path(str(runtime) + '.previous')
    real_replace = os.replace

    def replace(source, destination):
        if Path(source) == previous and Path(destination) == runtime:
            raise OSError('controlled rollback failure')
        result = real_replace(source, destination)
        if Path(destination) == runtime and '-install-' in str(source):
            cancelled.set()
        return result

    monkeypatch.setattr(engine.os, 'replace', replace)
    with pytest.raises(OSError, match='controlled rollback failure'):
        prepare(None, cancelled.is_set)
    assert not runtime.exists()
    assert (previous / 'original.bin').read_bytes() == b'preserve original installation'
    assert not list(runtime.parent.glob('*-install-*'))
    monkeypatch.setattr(engine.os, 'replace', real_replace)
    cancelled.clear()

    def verify_restored(path, **kwargs):
        assert (Path(path) / 'original.bin').is_file()
        return {} if verifier == '_verify_translation_install' else str(path)

    monkeypatch.setattr(engine, verifier, verify_restored)
    prepare(None, cancelled.is_set)
    assert (runtime / 'original.bin').is_file() and not previous.exists()


def test_failed_whisper_activation_preserves_previous_tree(tmp_path, monkeypatch):
    runtime = tmp_path / 'whisper' / engine.WHISPER_VERSION
    runtime.mkdir(parents=True)
    (runtime / 'original.bin').write_bytes(b'preserve original installation')
    monkeypatch.setattr(engine, '_cache_root', lambda: str(tmp_path))
    monkeypatch.setattr(engine, '_download_verified', lambda *a, **kw: None)
    monkeypatch.setattr(engine, '_safe_extract_zip', lambda *a, **kw: None)
    monkeypatch.setattr(engine, '_verify_whisper_install',
                        lambda path, **kw: 'controlled-exe' if '-install-' in str(path) else None)
    real_replace = os.replace

    def replace(source, destination):
        if Path(destination) == runtime and '-install-' in str(source):
            raise OSError('controlled activation failure')
        return real_replace(source, destination)

    monkeypatch.setattr(engine.os, 'replace', replace)
    with pytest.raises(OSError, match='controlled activation failure'):
        engine._prepare_runtime_locked(None, None)
    assert (runtime / 'original.bin').read_bytes() == b'preserve original installation'
    assert not list(runtime.parent.glob('*-install-*'))


def test_whisper_recovers_verified_backup_instead_of_reinstalling(tmp_path, monkeypatch):
    runtime = tmp_path / 'whisper' / engine.WHISPER_VERSION
    runtime.mkdir(parents=True)
    (runtime / 'invalid.bin').write_bytes(b'invalid active installation')
    previous = Path(str(runtime) + '.previous')
    previous.mkdir()
    (previous / 'original.bin').write_bytes(b'verified previous installation')
    monkeypatch.setattr(engine, '_cache_root', lambda: str(tmp_path))
    monkeypatch.setattr(engine, '_verify_whisper_install', lambda path, **kw:
                        str(Path(path) / 'whisper.exe') if (Path(path) / 'original.bin').exists() else None)
    monkeypatch.setattr(engine, '_safe_extract_zip', lambda *a, **kw: pytest.fail('backup should be recovered'))
    monkeypatch.setattr(engine, '_download_verified', lambda *a, **kw: None)
    monkeypatch.setattr(engine, 'recognition_profile', lambda: SimpleNamespace(engine='whisper'))
    monkeypatch.setattr(engine, '_prepare_whisper_model', lambda *a: 'controlled-model')
    executable, _, _ = engine._prepare_runtime_locked(None, None)
    assert executable == str(runtime / 'whisper.exe')
    assert (runtime / 'original.bin').read_bytes() == b'verified previous installation'
    assert not previous.exists()


@pytest.mark.parametrize('function,lock_name', [
    ('_prepare_translation_runtime_locked', '_translation_runtime_lock'),
    ('_prepare_reazonspeech_runtime_locked', '_reazonspeech_runtime_lock'),
    ('_prepare_runtime_locked', '_runtime_lock'),
    ('_prepare_whisper_fallback_model', '_runtime_lock'),
])
def test_stopped_model_waiter_exits_without_waiting_for_other_installer(
        tmp_path, monkeypatch, function, lock_name):
    lock = threading.Lock()
    lock.acquire()
    cancelled, entered = threading.Event(), threading.Event()

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            entered.set()
            return lock.acquire(*args, **kwargs)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *args):
            self.release()

    monkeypatch.setattr(engine, lock_name, ObservedLock())
    monkeypatch.setattr(engine, '_cache_root', lambda: str(tmp_path))
    results = []

    def run():
        try:
            getattr(engine, function)(None, cancelled.is_set)
        except engine.SubtitleCancelled:
            results.append('stopped')

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2)
        cancelled.set()
        worker.join(0.5)
        exited_before_unlock = not worker.is_alive()
    finally:
        lock.release()
        worker.join(3)
    assert exited_before_unlock, 'stopped installer must not wait for another model owner'
    assert results == ['stopped']
