import hashlib
import json
import os
import shutil
import wave
import zipfile
from contextlib import contextmanager

import pytest

from uav_downloader.core import config
from uav_downloader.subtitles import engine as subtitles


def _write_pcm16_wav(path, seconds=2.0):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b'\0\0' * int(16_000 * seconds))


def _reazon_payload(
        text='今日は晴れです',
        tokens=None,
        timestamps=None):
    tokens = list(tokens or text)
    timestamps = list(
        timestamps
        or [round(index * 0.20, 2) for index in range(len(tokens))])
    return {
        'lang': '',
        'emotion': '',
        'event': '',
        'text': text,
        'timestamps': timestamps,
        'durations': [],
        'tokens': tokens,
        'ys_log_probs': [-0.1 for _ in tokens],
        'words': [],
    }


def _install_fixture(monkeypatch, tmp_path):
    files = {
        'runtime/sherpa-onnx-offline.exe': b'native runtime',
        'runtime/onnxruntime.dll': b'onnx runtime',
        'runtime/onnxruntime_providers_shared.dll': b'providers',
        'model/tokens.txt': b'tokens',
        'model/encoder-epoch-99-avg-1.int8.onnx': b'encoder',
        'model/decoder-epoch-99-avg-1.onnx': b'decoder',
        'model/joiner-epoch-99-avg-1.int8.onnx': b'joiner',
        'model/MODEL_CARD.md': b'model card',
        'licenses/Apache-2.0.txt': b'apache',
        'licenses/ONNXRuntime-MIT.txt': b'mit',
        'licenses/ONNXRuntime-ThirdPartyNotices.txt': b'notices',
    }
    expected = {
        path: (len(payload), hashlib.sha256(payload).hexdigest())
        for path, payload in files.items()
    }
    monkeypatch.setattr(
        subtitles, 'REAZONSPEECH_REQUIRED_FILES', expected)
    manifest = {
        'files': [
            {'path': path, 'size': size, 'sha256': sha256}
            for path, (size, sha256) in sorted(expected.items())
        ],
        'format': 'jable-reazonspeech-asr-pack',
        'pack_version': subtitles.REAZONSPEECH_PACK_VERSION,
        'model': {
            'path': 'model',
            'revision': subtitles.REAZONSPEECH_MODEL_REVISION,
        },
        'runtime': {
            'path': 'runtime',
            'version': subtitles.REAZONSPEECH_RUNTIME_VERSION,
        },
    }
    manifest_bytes = (
        json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True,
            separators=(',', ': '))
        + '\n'
    ).encode('utf-8')
    monkeypatch.setattr(
        subtitles, 'REAZONSPEECH_MANIFEST_SHA256',
        hashlib.sha256(manifest_bytes).hexdigest())

    root = tmp_path / 'installed'
    for path, payload in files.items():
        target = root.joinpath(*path.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (root / 'manifest.json').write_bytes(manifest_bytes)
    (root / '.source-sha256').write_text(
        subtitles.REAZONSPEECH_PACK_SHA256, encoding='ascii')
    return root, files, manifest_bytes


def test_reazonspeech_model_identity_is_the_verified_manifest():
    assert subtitles.REAZONSPEECH_MODEL_SHA256 == (
        subtitles.REAZONSPEECH_MANIFEST_SHA256)
    assert subtitles.recognition_profile('auto').engine == 'reazonspeech'
    assert subtitles.REAZONSPEECH_REQUIRED_FILES[
        'model/MODEL_CARD.md'
    ] == (
        1_188,
        '7debad4c9430f3310ad6d119fce385787c1c19f3ebc0cabe685a48dbe72a4de0',
    )


def test_prepare_runtime_auto_installs_reazon_without_whisper_model(
        monkeypatch, tmp_path):
    downloads = []
    runtime = tmp_path / 'whisper' / 'Release' / 'whisper-cli.exe'
    pack = tmp_path / 'asr' / 'reazonspeech-v1'
    monkeypatch.setattr(subtitles, '_cache_root', lambda: str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_verify_whisper_install',
        lambda *_args, **_kwargs: str(runtime))
    monkeypatch.setattr(
        subtitles, '_prepare_reazonspeech_runtime',
        lambda *_args: str(pack))
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'auto')

    def fake_download(url, destination, *_args):
        downloads.append((url, destination))
        return destination

    monkeypatch.setattr(subtitles, '_download_verified', fake_download)

    exe, selected_model, vad = subtitles._prepare_runtime_locked(
        None, None)

    assert exe == str(runtime)
    assert selected_model == str(pack)
    assert vad.endswith(subtitles.VAD_MODEL_NAME)
    assert downloads == [(
        subtitles.VAD_MODEL_URL,
        str(tmp_path / 'models' / subtitles.VAD_MODEL_NAME),
    )]


def test_generate_default_auto_dispatches_reazonspeech(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'auto')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: ('whisper.exe', 'reazon-pack', 'vad.bin'))
    monkeypatch.setattr(
        subtitles, '_extract_audio',
        lambda _video, wav, _log, _cancel: open(wav, 'wb').close())
    monkeypatch.setattr(
        subtitles, '_run_whisper',
        lambda *_args, **_kwargs: pytest.fail(
            'Auto must not dispatch directly to Whisper'))

    def fake_reazon(
            _exe, _pack, _vad, _wav, output, _log, _cancel,
            **_kwargs):
        result = output + '.srt'
        subtitles._atomic_write_text(
            result,
            '1\n00:00:00,000 --> 00:00:01,000\n日本語\n')
        return result

    monkeypatch.setattr(
        subtitles, '_run_reazonspeech', fake_reazon)

    result = subtitles.generate_subtitles(str(video), 'ja')

    assert result.generated == (str(tmp_path / 'movie.ja.srt'),)
    assert (tmp_path / 'movie.ja.srt').is_file()


def test_reazonspeech_install_verifier_requires_exact_allowlist_and_hashes(
        monkeypatch, tmp_path):
    root, _files, _manifest = _install_fixture(
        monkeypatch, tmp_path)

    assert subtitles._verify_reazonspeech_install(str(root)) == str(root)

    (root / 'runtime' / 'sherpa-onnx-offline.exe').write_bytes(
        b'tampered runt')
    subtitles._verified_paths.clear()
    with pytest.raises(subtitles.SubtitleError):
        subtitles._verify_reazonspeech_install(str(root))


def test_reazonspeech_install_verifier_rejects_manifest_not_on_allowlist(
        monkeypatch, tmp_path):
    root, _files, _manifest = _install_fixture(
        monkeypatch, tmp_path)
    manifest = json.loads(
        (root / 'manifest.json').read_text(encoding='utf-8'))
    manifest['files'].append({
        'path': 'runtime/unreviewed.dll',
        'size': 1,
        'sha256': hashlib.sha256(b'x').hexdigest(),
    })
    payload = (
        json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True,
            separators=(',', ': '))
        + '\n'
    ).encode('utf-8')
    (root / 'manifest.json').write_bytes(payload)
    monkeypatch.setattr(
        subtitles, 'REAZONSPEECH_MANIFEST_SHA256',
        hashlib.sha256(payload).hexdigest())

    with pytest.raises(subtitles.SubtitleError):
        subtitles._verify_reazonspeech_install(str(root))


def test_reazonspeech_install_verifier_rejects_unmanifested_native_file(
        monkeypatch, tmp_path):
    root, _files, _manifest = _install_fixture(
        monkeypatch, tmp_path)
    (root / 'runtime' / 'unreviewed.dll').write_bytes(b'native injection')

    with pytest.raises(subtitles.SubtitleError):
        subtitles._verify_reazonspeech_install(str(root))


def test_reazonspeech_install_is_atomic_and_removes_download(
        monkeypatch, tmp_path):
    source_root, files, manifest_bytes = _install_fixture(
        monkeypatch, tmp_path)
    archive = tmp_path / 'source-pack.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('manifest.json', manifest_bytes)
        for path, payload in files.items():
            bundle.writestr(path, payload)
    archive_bytes = archive.read_bytes()
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    monkeypatch.setattr(subtitles, 'REAZONSPEECH_PACK_SIZE', len(archive_bytes))
    monkeypatch.setattr(subtitles, 'REAZONSPEECH_PACK_SHA256', archive_sha)
    (source_root / '.source-sha256').write_text(
        archive_sha, encoding='ascii')
    cache = tmp_path / 'cache'
    monkeypatch.setattr(subtitles, '_cache_root', lambda: str(cache))
    reservations = []
    monkeypatch.setattr(
        subtitles, '_ensure_asr_temp_space',
        lambda path, additional_bytes=0: reservations.append(
            (os.path.abspath(path), additional_bytes)))

    def fake_download(
            _url, destination, expected_size, expected_sha256, *_args):
        assert expected_size == len(archive_bytes)
        assert expected_sha256 == archive_sha
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(archive, destination)
        return destination

    monkeypatch.setattr(subtitles, '_download_verified', fake_download)

    installed = subtitles._prepare_reazonspeech_runtime_locked(None, None)

    assert subtitles._verify_reazonspeech_install(installed) == installed
    assert reservations == [(
        os.path.abspath(cache / 'asr'),
        len(archive_bytes),
    )]
    assert not (cache / 'downloads' /
                subtitles.REAZONSPEECH_PACK_NAME).exists()
    assert not list((cache / 'asr').glob('*-install-*'))


def test_reazonspeech_cleanup_never_recursively_deletes_a_link_target(
        monkeypatch, tmp_path):
    parent = tmp_path / 'asr'
    target = parent / 'reazonspeech-v1-install-unsafe'
    target.mkdir(parents=True)
    sentinel = target / 'must-survive.txt'
    sentinel.write_text('keep', encoding='utf-8')
    original = subtitles._path_has_link_component
    monkeypatch.setattr(
        subtitles,
        '_path_has_link_component',
        lambda root, path: (
            os.path.abspath(path) == os.path.abspath(target)
            or original(root, path)),
    )

    with pytest.raises(subtitles.SubtitleError):
        subtitles._remove_reazonspeech_cache_path(
            str(target), str(parent))
    subtitles._cleanup_reazonspeech_install_dirs(str(parent))

    assert sentinel.is_file()


def test_reazonspeech_cli_uses_pinned_cpu_runtime_and_safe_batch_names(
        tmp_path, monkeypatch):
    monkeypatch.setattr(subtitles, '_recognition_thread_count', lambda: 3)
    args = subtitles._reazonspeech_cli_args(
        str(tmp_path), ['island-00000.wav', 'island-00001.wav'])

    assert args[0] == str(
        tmp_path / 'runtime' / 'sherpa-onnx-offline.exe')
    assert '--num-threads=3' in args
    assert '--decoding-method=greedy_search' in args
    assert '--provider=cpu' in args
    assert args[-2:] == ['island-00000.wav', 'island-00001.wav']
    with pytest.raises(subtitles.SubtitleError):
        subtitles._reazonspeech_cli_args(
            str(tmp_path), ['../private.wav'])
    with pytest.raises(subtitles.SubtitleError):
        subtitles._reazonspeech_cli_args(
            str(tmp_path),
            [f'island-{index:05d}.wav'
             for index in range(subtitles.REAZONSPEECH_BATCH_SIZE + 1)])


def test_reazonspeech_parser_preserves_text_and_groups_by_punctuation():
    payload = _reazon_payload(
        text='今日は晴れです。帰ります',
        tokens=list('今日は晴れです。帰ります'),
        timestamps=[
            0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4,
            2.0, 2.2, 2.4, 2.6,
        ],
    )
    cues = subtitles._parse_reazonspeech_payload(
        payload, subtitles.SpeechIsland(10.0, 14.0), 4.0)

    assert ''.join(cue.text for cue in cues) == payload['text']
    assert [cue.text for cue in cues] == ['今日は晴れです。', '帰ります']
    assert cues[0].start == 10.0
    assert cues[-1].end <= 14.0
    assert all(cue.end > cue.start for cue in cues)


@pytest.mark.parametrize('mutate', [
    lambda value: value.update(tokens=['different']),
    lambda value: value.update(timestamps=[0.0, 0.4, 0.2, 0.6, 0.8, 1.0, 1.2]),
    lambda value: value.update(ys_log_probs=[float('nan')] * 7),
    lambda value: value.update(text='bad\ntext'),
    lambda value: value.update(
        text='bad\u200btext',
        tokens=list('bad\u200btext'),
        timestamps=[index * 0.1 for index in range(8)],
        ys_log_probs=[-0.1] * 8),
    lambda value: value.pop('words'),
])
def test_reazonspeech_parser_rejects_invalid_schema_or_timing(mutate):
    payload = _reazon_payload()
    mutate(payload)

    with pytest.raises(subtitles.ReazonSpeechStructuralError):
        subtitles._parse_reazonspeech_payload(
            payload, subtitles.SpeechIsland(0.0, 2.0), 2.0)


def test_reazonspeech_batch_separates_stdout_from_stderr(
        monkeypatch, tmp_path):
    captured = {}

    class CompleteProcess:
        returncode = 0

        def poll(self):
            return 0

    def fake_popen(args, **kwargs):
        captured['args'] = args
        assert kwargs['stdout'] is not kwargs['stderr']
        kwargs['stdout'].write(
            (json.dumps(_reazon_payload(), ensure_ascii=False) + '\n')
            .encode('utf-8'))
        kwargs['stderr'].write(b'native diagnostic line\n')
        return CompleteProcess()

    monkeypatch.setattr(subtitles.subprocess, 'Popen', fake_popen)
    payloads = subtitles._run_reazonspeech_cli_batch(
        str(tmp_path), str(tmp_path), ['island-00000.wav'],
        0, None, batch_audio_seconds=2.0)

    assert len(payloads) == 1
    assert payloads[0]['text'] == '今日は晴れです'
    assert (tmp_path / 'reazon-batch-00000.stderr.log').read_bytes() == (
        b'native diagnostic line\n')


def test_reazonspeech_jsonl_rejects_duplicate_keys(tmp_path):
    output = tmp_path / 'duplicate.jsonl'
    output.write_text(
        '{"text":"first","text":"second"}\n',
        encoding='utf-8')

    with pytest.raises(subtitles.ReazonSpeechStructuralError):
        subtitles._strict_reazonspeech_json_lines(str(output), 1)


def test_reazonspeech_batch_timeout_is_a_structural_fallback_signal(
        monkeypatch, tmp_path):
    class StalledProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -1

        def wait(self, timeout=None):
            del timeout
            return self.returncode

    monkeypatch.setattr(
        subtitles.subprocess, 'Popen',
        lambda *_args, **_kwargs: StalledProcess())
    monkeypatch.setattr(
        subtitles, '_wait_for_process',
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                subtitles.SubtitleProcessTimeout('stalled'))))

    with pytest.raises(subtitles.ReazonSpeechStructuralError):
        subtitles._run_reazonspeech_cli_batch(
            str(tmp_path), str(tmp_path), ['island-00000.wav'],
            0, None, batch_audio_seconds=2.0)


def test_reazonspeech_structural_failure_lazily_falls_back_to_balanced(
        monkeypatch, tmp_path):
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 2.0)
    whisper = tmp_path / 'whisper-cli.exe'
    vad = tmp_path / 'whisper-vad-speech-segments.exe'
    whisper.write_bytes(b'exe')
    vad.write_bytes(b'vad')
    calls = []
    outcomes = []
    monkeypatch.setattr(
        subtitles, '_run_external_vad',
        lambda *_args, **_kwargs: [
            subtitles.SpeechIsland(0.1, 1.0)])
    monkeypatch.setattr(
        subtitles, '_verify_reazonspeech_install',
        lambda root, **_kwargs: root)
    monkeypatch.setattr(
        subtitles, '_run_reazonspeech_cli_batch',
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                subtitles.ReazonSpeechStructuralError('bad JSON'))))
    monkeypatch.setattr(
        subtitles, '_prepare_whisper_fallback_model',
        lambda *_args: calls.append('download-balanced') or 'balanced.bin')
    monkeypatch.setattr(
        subtitles, '_run_whisper',
        lambda *_args, **_kwargs: calls.append('whisper') or 'fallback.srt')

    result = subtitles._run_reazonspeech(
        str(whisper), str(tmp_path / 'pack'), 'vad.bin', str(wav),
        str(tmp_path / 'out'), str(tmp_path / 'log'), None,
        outcome_callback=lambda profile, fallback: outcomes.append(
            (profile.key, profile.engine, fallback)))

    assert result == 'fallback.srt'
    assert calls == ['download-balanced', 'whisper']
    assert outcomes == [('balanced', 'whisper', True)]


def test_reazonspeech_primary_reports_actual_engine_without_fallback(
        monkeypatch, tmp_path):
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 3.0)
    whisper = tmp_path / 'whisper-cli.exe'
    vad = tmp_path / 'whisper-vad-speech-segments.exe'
    whisper.write_bytes(b'exe')
    vad.write_bytes(b'vad')
    outcomes = []
    monkeypatch.setattr(
        subtitles, '_run_external_vad',
        lambda *_args, **_kwargs: [
            subtitles.SpeechIsland(0.1, 2.5)])
    monkeypatch.setattr(
        subtitles, '_verify_reazonspeech_install',
        lambda root, **_kwargs: root)
    monkeypatch.setattr(
        subtitles, '_run_reazonspeech_cli_batch',
        lambda *_args, **_kwargs: [_reazon_payload()])
    monkeypatch.setattr(
        subtitles, '_prepare_whisper_fallback_model',
        lambda *_args: pytest.fail('primary must not download fallback'))

    result = subtitles._run_reazonspeech(
        str(whisper), str(tmp_path / 'pack'), 'vad.bin', str(wav),
        str(tmp_path / 'out'), str(tmp_path / 'log'), None,
        outcome_callback=lambda profile, fallback: outcomes.append(
            (profile.key, profile.engine, fallback)))

    assert result == str(tmp_path / 'out.srt')
    assert (tmp_path / 'out.srt').is_file()
    assert outcomes == [('auto', 'reazonspeech', False)]


def test_lazy_fallback_shares_the_primary_whisper_interprocess_lock(
        monkeypatch):
    locks = []

    @contextmanager
    def fake_lock(name, _cancel):
        locks.append(name)
        yield

    monkeypatch.setattr(
        subtitles, '_interprocess_cache_lock', fake_lock)
    monkeypatch.setattr(
        subtitles, '_prepare_whisper_model',
        lambda profile, *_args: profile.model_name)

    result = subtitles._prepare_whisper_fallback_model(None, None)

    assert locks == [f'whisper-{subtitles.WHISPER_VERSION}']
    assert result == subtitles.RECOGNITION_PROFILES['balanced'].model_name


def test_reazonspeech_does_not_fallback_for_shared_vad_failure(
        monkeypatch, tmp_path):
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 2.0)
    whisper = tmp_path / 'whisper-cli.exe'
    vad = tmp_path / 'whisper-vad-speech-segments.exe'
    whisper.write_bytes(b'exe')
    vad.write_bytes(b'vad')
    monkeypatch.setattr(
        subtitles, '_run_external_vad',
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(subtitles.SubtitleError('VAD failed'))))
    monkeypatch.setattr(
        subtitles, '_verify_reazonspeech_install',
        lambda root, **_kwargs: root)
    monkeypatch.setattr(
        subtitles, '_prepare_whisper_fallback_model',
        lambda *_args: pytest.fail('must not download fallback'))

    with pytest.raises(subtitles.SubtitleError, match='VAD failed'):
        subtitles._run_reazonspeech(
            str(whisper), str(tmp_path / 'pack'), 'vad.bin', str(wav),
            str(tmp_path / 'out'), str(tmp_path / 'log'), None)


def test_reazonspeech_vad_silence_does_not_download_fallback(
        monkeypatch, tmp_path):
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 2.0)
    whisper = tmp_path / 'whisper-cli.exe'
    vad = tmp_path / 'whisper-vad-speech-segments.exe'
    whisper.write_bytes(b'exe')
    vad.write_bytes(b'vad')
    monkeypatch.setattr(
        subtitles, '_run_external_vad',
        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        subtitles, '_verify_reazonspeech_install',
        lambda root, **_kwargs: root)
    monkeypatch.setattr(
        subtitles, '_prepare_whisper_fallback_model',
        lambda *_args: pytest.fail('silence must not download fallback'))

    assert subtitles._run_reazonspeech(
        str(whisper), str(tmp_path / 'pack'), 'vad.bin', str(wav),
        str(tmp_path / 'out'), str(tmp_path / 'log'), None) is None


def test_recognition_dispatch_keeps_explicit_whisper_profiles(
        monkeypatch):
    calls = []
    monkeypatch.setattr(
        subtitles, '_run_reazonspeech',
        lambda *_args, **_kwargs: calls.append('reazon') or 'auto.srt')
    monkeypatch.setattr(
        subtitles, '_run_whisper',
        lambda *_args, **_kwargs: calls.append('whisper') or 'manual.srt')

    assert subtitles._run_recognition(
        subtitles.recognition_profile('auto'),
        'exe', 'pack', 'vad', 'wav', 'out', 'log', None) == 'auto.srt'
    assert subtitles._run_recognition(
        subtitles.recognition_profile('quality'),
        'exe', 'model', 'vad', 'wav', 'out', 'log', None) == 'manual.srt'
    assert calls == ['reazon', 'whisper']


def test_asr_signature_identifies_reazonspeech_runtime_and_batch_policy():
    automatic = subtitles._asr_signature(
        subtitles.recognition_profile('auto'))
    balanced = subtitles._asr_signature(
        subtitles.recognition_profile('balanced'))

    assert automatic != balanced
    assert automatic == subtitles._asr_signature(
        subtitles.recognition_profile('auto'))


def test_reazonspeech_allows_single_empty_window_without_fallback(
        monkeypatch, tmp_path):
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 14.0)
    whisper = tmp_path / 'whisper-cli.exe'
    vad = tmp_path / 'whisper-vad-speech-segments.exe'
    whisper.write_bytes(b'exe')
    vad.write_bytes(b'vad')
    monkeypatch.setattr(
        subtitles, '_run_external_vad',
        lambda *_args, **_kwargs: [
            subtitles.SpeechIsland(0.1, 1.5),
            subtitles.SpeechIsland(10.0, 12.0),
        ])
    monkeypatch.setattr(
        subtitles, '_verify_reazonspeech_install',
        lambda root, **_kwargs: root)
    monkeypatch.setattr(
        subtitles, '_run_reazonspeech_cli_batch',
        lambda *_args, **_kwargs: [
            _reazon_payload(text=''),
            _reazon_payload(),
        ])
    monkeypatch.setattr(
        subtitles, '_prepare_whisper_fallback_model',
        lambda *_args: pytest.fail('single empty window must not trigger Whisper fallback'))

    result = subtitles._run_reazonspeech(
        str(whisper), str(tmp_path / 'pack'), 'vad.bin', str(wav),
        str(tmp_path / 'out'), str(tmp_path / 'log'), None)

    assert result == str(tmp_path / 'out.srt')
    assert (tmp_path / 'out.srt').is_file()
