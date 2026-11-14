import inspect
import json
import os
import wave

from uav_downloader.core import config
import pytest

from uav_downloader.subtitles import engine as subtitles


def _write_pcm16_wav(path, seconds=1.0):
    frame_count = int(16_000 * seconds)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b'\0\0' * frame_count)


def _cli_payload(*segments):
    return {
        'params': {
            'model': 'model.bin',
            'language': 'ja',
            'translate': False,
        },
        'result': {'language': 'ja'},
        'transcription': list(segments),
    }


def _cli_segment(start_ms, end_ms, text='同じ言葉'):
    def stamp(value):
        seconds, milliseconds = divmod(value, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'

    return {
        'timestamps': {
            'from': stamp(start_ms),
            'to': stamp(end_ms),
        },
        'offsets': {'from': start_ms, 'to': end_ms},
        'text': text,
    }


def test_default_recognition_quality_uses_the_cpu_auto_engine():
    profile = subtitles.recognition_profile(config.get_recognition_quality())

    assert profile.key == 'auto'
    assert profile.engine == 'reazonspeech'
    assert profile.model_name == subtitles.REAZONSPEECH_MODEL_NAME
    assert profile.model_size == subtitles.REAZONSPEECH_PACK_SIZE
    assert profile.model_sha256 == subtitles.REAZONSPEECH_MANIFEST_SHA256


def test_recognition_quality_normalization_keeps_a_safe_default():
    assert subtitles.normalize_recognition_quality(None) == 'auto'
    assert subtitles.normalize_recognition_quality('precise') == 'quality'
    assert subtitles.normalize_recognition_quality('balanced') == 'balanced'
    assert subtitles.normalize_recognition_quality('fast') == 'fast'
    assert subtitles.normalize_recognition_quality('unknown') == 'auto'


def test_recognition_profiles_pin_all_three_lazy_models():
    balanced = subtitles.recognition_profile('balanced')
    fast = subtitles.recognition_profile('fast')

    assert (balanced.model_name, balanced.model_size) == (
        'ggml-small-q5_1.bin', 190_085_487)
    assert balanced.model_sha256 == (
        'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb')
    assert (fast.model_name, fast.model_size) == (
        'ggml-base-q5_1.bin', 59_707_625)
    assert fast.model_sha256 == (
        '422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898')


def test_runtime_downloads_only_the_selected_recognition_model(
        monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime' / 'Release' / 'whisper-cli.exe'
    downloads = []
    monkeypatch.setattr(subtitles, '_cache_root', lambda: str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_verify_whisper_install',
        lambda *_args, **_kwargs: str(runtime))
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'balanced')

    def fake_download(url, destination, size, sha256, *_args):
        downloads.append((url, destination, size, sha256))
        return destination

    monkeypatch.setattr(subtitles, '_download_verified', fake_download)
    _exe, model, _vad = subtitles._prepare_runtime_locked(None, None)

    assert model.endswith('ggml-small-q5_1.bin')
    assert downloads[0][0].endswith('/ggml-small-q5_1.bin')
    assert downloads[0][2:] == (
        190_085_487,
        'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb')
    assert all(
        'ggml-large-v3-turbo-q5_0.bin' not in item[0]
        and 'ggml-base-q5_1.bin' not in item[0]
        for item in downloads)


def test_whisper_runtime_contract_includes_pinned_external_vad_helper():
    required = {path.casefold() for path in subtitles.WHISPER_RUNTIME_FILES}

    assert 'release/whisper-vad-speech-segments.exe' in required
    assert subtitles.WHISPER_RUNTIME_FILES[
        'Release/whisper-vad-speech-segments.exe'] == (
            362_496,
            '69a4dbca1828afc3ffa17e5548ab4b96d866068c84db130980b764b15b15b2eb')


def test_v191_external_vad_output_maps_centiseconds_to_absolute_seconds():
    output = (
        'Detected 3 speech segments:\n'
        'Speech segment 0: start = 38.00, end = 519.00\n'
        'Speech segment 1: start = 703.00, end = 1188.00\n'
        'Speech segment 2: start = 1372.00, end = 1850.00\n'
    )

    islands = subtitles._parse_vad_speech_segments(output)

    assert [(island.start, island.end) for island in islands] == [
        (0.38, 5.19), (7.03, 11.88), (13.72, 18.5)]


@pytest.mark.parametrize('output', [
    'Detected 1 speech segments:\nSpeech segment x: start = 1, end = 2\n',
    'Detected 2 speech segments:\n'
    'Speech segment 0: start = 1, end = 2\n',
    'Detected 1 speech segments:\n'
    'Speech segment 1: start = 1, end = 2\n',
    'Detected 1 speech segments:\n'
    'Speech segment 0: start = 2, end = 1\n',
    'Speech segment 20001: start = 1, end = 2\n',
])
def test_external_vad_parser_rejects_malformed_or_inconsistent_output(output):
    with pytest.raises(subtitles.SubtitleError):
        subtitles._parse_vad_speech_segments(output)


def test_external_vad_uses_empirically_safe_quality_parameters(
        monkeypatch, tmp_path):
    captured = {}

    class CompleteProcess:
        returncode = 0

        def poll(self):
            return 0

    def fake_popen(args, **kwargs):
        captured['args'] = list(args)
        captured['cwd'] = kwargs['cwd']
        kwargs['stdout'].write(
            b'Detected 1 speech segments:\n'
            b'Speech segment 0: start = 20.00, end = 80.00\n')
        return CompleteProcess()

    monkeypatch.setattr(subtitles.subprocess, 'Popen', fake_popen)
    islands = subtitles._run_external_vad(
        str(tmp_path / 'whisper-vad-speech-segments.exe'),
        str(tmp_path / 'vad.bin'),
        str(tmp_path / 'audio.wav'),
        str(tmp_path),
        None,
    )

    args = captured['args']
    assert [(item.start, item.end) for item in islands] == [(0.2, 0.8)]
    assert args[args.index('--vad-threshold') + 1] == '0.35'
    assert args[args.index('--vad-min-speech-duration-ms') + 1] == '100'
    assert args[args.index('--vad-max-speech-duration-s') + 1] == '28'
    assert args[args.index('--vad-speech-pad-ms') + 1] == '0'
    assert args[args.index('--vad-samples-overlap') + 1] == '0.2'


def test_vad_postprocessing_preserves_real_repeated_utterances():
    islands = [
        subtitles.SpeechIsland(0.38, 5.19),
        subtitles.SpeechIsland(5.42, 6.20),
        subtitles.SpeechIsland(7.03, 11.88),
        subtitles.SpeechIsland(13.72, 18.50),
    ]

    merged = subtitles._merge_speech_islands(
        islands, max_gap_seconds=0.45, max_duration_seconds=28.0)

    assert [(item.start, item.end) for item in merged] == [
        (0.38, 6.20), (7.03, 11.88), (13.72, 18.50)]


def test_recognition_windows_retain_real_silence_and_sentence_context():
    windows = subtitles._build_recognition_windows([
        subtitles.SpeechIsland(0.38, 5.19),
        subtitles.SpeechIsland(7.03, 11.88),
        subtitles.SpeechIsland(13.72, 18.50),
    ], audio_duration=20.0)

    assert windows == [subtitles.SpeechIsland(0.0, 19.0)]


def test_recognition_windows_do_not_merge_across_two_second_context_gap():
    windows = subtitles._build_recognition_windows([
        subtitles.SpeechIsland(1.0, 2.0),
        subtitles.SpeechIsland(4.01, 5.0),
    ], audio_duration=6.0)

    assert len(windows) == 2
    assert windows[0].end <= windows[1].start


def test_recognition_window_padding_never_overlaps_or_exceeds_28_seconds():
    windows = subtitles._build_recognition_windows([
        subtitles.SpeechIsland(0.50, 25.50),
        subtitles.SpeechIsland(26.00, 40.00),
    ], audio_duration=42.0)

    assert len(windows) == 2
    assert windows[0].end == windows[1].start
    assert all(
        0 < window.end - window.start <= 28.0
        for window in windows)


def test_long_adjacent_speech_windows_split_without_expanding_context():
    windows = subtitles._build_recognition_windows([
        subtitles.SpeechIsland(1.0, 28.5),
        subtitles.SpeechIsland(29.1, 35.0),
    ], audio_duration=40.0)

    assert len(windows) == 2
    assert windows[0].end == windows[1].start
    assert windows[0].start <= 1.0 <= 28.5 <= windows[0].end
    assert windows[1].start <= 29.1 <= 35.0 <= windows[1].end
    assert all(
        0 < window.end - window.start <= 28.0
        for window in windows)


def test_shipping_whisper_path_does_not_force_greedy_or_disable_fallback():
    source = inspect.getsource(subtitles._run_whisper)

    assert "'-bs', '1'" not in source
    assert "'-bo', '1'" not in source
    assert "'-nf'" not in source


def test_batched_cli_loads_model_once_and_uses_repeated_safe_file_flags():
    args = subtitles._whisper_cli_batch_args(
        'whisper-cli.exe',
        'model.bin',
        ['island-00000.wav', 'island-00001.wav'],
    )

    assert args.count('-m') == 1
    assert args.count('-f') == 2
    assert args[args.index('-bs') + 1] == '5'
    assert args[args.index('-bo') + 1] == '5'
    assert args[args.index('-tp') + 1] == '0'
    assert args[args.index('-tpi') + 1] == '0.2'
    assert '-oj' in args
    assert '-sns' not in args
    assert '-nf' not in args
    assert '--vad' not in args
    assert '.post(' not in open(
        subtitles.__file__, encoding='utf-8').read().lower()


def test_batched_cli_rejects_unsafe_or_oversized_input_lists():
    with pytest.raises(subtitles.SubtitleError):
        subtitles._whisper_cli_batch_args(
            'whisper-cli.exe', 'model.bin', ['../audio.wav'])
    with pytest.raises(subtitles.SubtitleError):
        subtitles._run_whisper_cli_batch(
            'whisper-cli.exe',
            'model.bin',
            '.',
            ['island-00000.wav'] * (subtitles.WHISPER_BATCH_SIZE + 1),
            0,
            None,
        )


def test_cli_payload_maps_and_clamps_offsets_to_absolute_timeline():
    cues = subtitles._parse_whisper_cli_payload(
        _cli_payload(
            _cli_segment(100, 1900, '一回目'),
            _cli_segment(1900, 5200, '二回目'),
        ),
        subtitles.SpeechIsland(10.0, 15.0),
        5.0,
    )

    assert cues == [
        subtitles.RecognizedSegment(10.1, 11.9, '一回目'),
        subtitles.RecognizedSegment(11.9, 15.0, '二回目'),
    ]


@pytest.mark.parametrize('segment', [
    _cli_segment(500, 100, 'backwards'),
    {
        'timestamps': {
            'from': '00:00:00,100',
            'to': '00:00:00,900',
        },
        'offsets': {'from': 200, 'to': 900},
        'text': 'mismatch',
    },
    {
        'timestamps': {
            'from': '00:00:00,100',
            'to': '00:00:00,900',
        },
        'offsets': {'from': True, 'to': 900},
        'text': 'boolean',
    },
])
def test_cli_payload_strictly_rejects_bad_timing(segment):
    with pytest.raises(subtitles.SubtitleError):
        subtitles._parse_whisper_cli_payload(
            _cli_payload(segment),
            subtitles.SpeechIsland(0.0, 2.0),
            2.0,
        )


def test_cli_payload_rejects_overlapping_cues():
    with pytest.raises(subtitles.SubtitleError):
        subtitles._parse_whisper_cli_payload(
            _cli_payload(
                _cli_segment(0, 2000, 'first'),
                _cli_segment(1000, 3000, 'overlap'),
            ),
            subtitles.SpeechIsland(10.0, 14.0),
            4.0,
        )


def test_cli_payload_caps_segment_count():
    segment = _cli_segment(0, 100)
    with pytest.raises(subtitles.SubtitleError):
        subtitles._parse_whisper_cli_payload(
            _cli_payload(
                *([segment] * (
                    subtitles.MAX_WHISPER_SEGMENTS_PER_ISLAND + 1))),
            subtitles.SpeechIsland(0.0, 2.0),
            2.0,
        )


def test_json_reader_rejects_malformed_and_oversized_payloads(tmp_path):
    malformed = tmp_path / 'malformed.json'
    malformed.write_bytes(b'{"transcription":')
    with pytest.raises(subtitles.SubtitleError):
        subtitles._strict_json_file(str(malformed))

    oversized = tmp_path / 'oversized.json'
    oversized.write_bytes(
        b'x' * (subtitles.MAX_WHISPER_RESPONSE_BYTES + 1))
    with pytest.raises(subtitles.SubtitleError):
        subtitles._strict_json_file(str(oversized))


def test_batch_runner_uses_short_cwd_and_reads_every_expected_json(
        monkeypatch, tmp_path):
    calls = []
    names = ['island-00000.wav', 'island-00001.wav']

    def fake_run(
            args, log_path, cancel_check, cwd=None,
            disk_guard_path=None, **kwargs):
        calls.append((
            list(args), log_path, cancel_check, cwd, disk_guard_path,
            kwargs))
        for name in names:
            (tmp_path / f'{name}.json').write_text(
                json.dumps(_cli_payload(_cli_segment(0, 500))),
                encoding='utf-8',
            )

    monkeypatch.setattr(subtitles, '_run_process', fake_run)
    payloads = subtitles._run_whisper_cli_batch(
        'whisper-cli.exe', 'model.bin', str(tmp_path), names, 0, None)

    assert len(payloads) == 2
    assert calls[0][3] == str(tmp_path)
    assert calls[0][4] == str(tmp_path)
    assert calls[0][0].count('-f') == 2
    assert calls[0][5]['stall_timeout_seconds'] > 0
    assert callable(calls[0][5]['heartbeat_callback'])


def test_whisper_batch_reports_real_progress_before_process_returns(
        monkeypatch, tmp_path):
    names = ['island-00000.wav', 'island-00001.wav']
    progress = []

    def fake_run(*_args, **kwargs):
        heartbeat = kwargs['heartbeat_callback']
        heartbeat()
        (tmp_path / f'{names[0]}.json').write_text(
            json.dumps(_cli_payload(_cli_segment(0, 500))),
            encoding='utf-8',
        )
        heartbeat()
        (tmp_path / f'{names[1]}.json').write_text(
            json.dumps(_cli_payload(_cli_segment(0, 500))),
            encoding='utf-8',
        )
        heartbeat()

    monkeypatch.setattr(subtitles, '_run_process', fake_run)
    payloads = subtitles._run_whisper_cli_batch(
        'whisper-cli.exe', 'model.bin', str(tmp_path), names, 0, None,
        total_windows=2,
        batch_audio_seconds=10,
        progress_callback=lambda stage, percent: progress.append(
            (stage, percent)),
    )

    assert len(payloads) == 2
    assert progress[:3] == [
        ('transcribe_ja', 0),
        ('transcribe_ja', 50),
        ('transcribe_ja', 99),
    ]
    assert progress[-1] == ('transcribe_ja', 99)


def test_run_process_cancellation_terminates_child(monkeypatch, tmp_path):
    state = {'terminated': False, 'started': False}

    class RunningProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            state['terminated'] = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def start(*args, **kwargs):
        state['started'] = True
        return RunningProcess()
    monkeypatch.setattr(subtitles.subprocess, 'Popen', start)
    with pytest.raises(subtitles.SubtitleCancelled):
        subtitles._run_process(
            ['whisper-cli.exe'],
            str(tmp_path / 'process.log'),
            lambda: state['started'],
            cwd=str(tmp_path),
        )
    assert state['terminated'] is True


def test_run_process_stall_timeout_terminates_child_and_reports_heartbeat(
        monkeypatch, tmp_path):
    state = {'terminated': False, 'clock': 0.0}
    heartbeats = []

    class StalledProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            state['terminated'] = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def monotonic():
        state['clock'] += 0.6
        return state['clock']

    monkeypatch.setattr(
        subtitles.subprocess, 'Popen',
        lambda *_args, **_kwargs: StalledProcess())
    monkeypatch.setattr(subtitles.time, 'monotonic', monotonic)
    monkeypatch.setattr(subtitles.time, 'sleep', lambda _seconds: None)

    with pytest.raises(
            subtitles.SubtitleProcessTimeout,
            match='stopped responding'):
        subtitles._run_process(
            ['whisper-cli.exe'],
            str(tmp_path / 'process.log'),
            None,
            cwd=str(tmp_path),
            stall_timeout_seconds=1.0,
            progress_probe=lambda: 0,
            heartbeat_callback=lambda: heartbeats.append(state['clock']),
        )

    assert state['terminated'] is True
    assert heartbeats


def test_asr_temp_space_guard_keeps_a_reserve(monkeypatch, tmp_path):
    class Usage:
        free = subtitles.ASR_TEMP_RESERVE_BYTES + 1023

    monkeypatch.setattr(subtitles.shutil, 'disk_usage', lambda _path: Usage())

    with pytest.raises(
            subtitles.SubtitleStorageError,
            match='Not enough free disk space'):
        subtitles._ensure_asr_temp_space(str(tmp_path), 1024)

    subtitles._ensure_asr_temp_space(str(tmp_path), 1023)


def test_zero_vad_windows_returns_no_speech_and_cleans_temp(
        monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    cli = runtime / 'whisper-cli.exe'
    helper = runtime / 'whisper-vad-speech-segments.exe'
    cli.write_bytes(b'exe')
    helper.write_bytes(b'exe')
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav)
    work = tmp_path / 'private-asr-work'

    def fake_mkdtemp(*_args, **_kwargs):
        work.mkdir()
        return str(work)

    monkeypatch.setattr(subtitles.tempfile, 'mkdtemp', fake_mkdtemp)
    monkeypatch.setattr(
        subtitles, '_run_external_vad', lambda *_args, **_kwargs: [])
    result = subtitles._run_whisper(
        str(cli), 'model.bin', 'vad.bin', str(wav),
        str(tmp_path / 'output'), str(tmp_path / 'old.log'), None)

    assert result is None
    assert not work.exists()
    assert not (tmp_path / 'output.srt').exists()


def test_speech_with_no_valid_cues_raises_and_cleans_temp(
        monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    cli = runtime / 'whisper-cli.exe'
    helper = runtime / 'whisper-vad-speech-segments.exe'
    cli.write_bytes(b'exe')
    helper.write_bytes(b'exe')
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 2.0)
    work = tmp_path / 'private-asr-work'

    def fake_mkdtemp(*_args, **_kwargs):
        work.mkdir()
        return str(work)

    monkeypatch.setattr(subtitles.tempfile, 'mkdtemp', fake_mkdtemp)
    monkeypatch.setattr(
        subtitles,
        '_run_external_vad',
        lambda *_args, **_kwargs: [subtitles.SpeechIsland(0.2, 1.0)],
    )
    monkeypatch.setattr(
        subtitles,
        '_run_whisper_cli_batch',
        lambda *_args, **_kwargs: [_cli_payload()],
    )
    with pytest.raises(subtitles.SubtitleError, match='no valid'):
        subtitles._run_whisper(
            str(cli), 'model.bin', 'vad.bin', str(wav),
            str(tmp_path / 'output'), str(tmp_path / 'old.log'), None)
    assert not work.exists()


def test_repeated_utterances_in_separate_windows_are_not_deduplicated(
        monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    cli = runtime / 'whisper-cli.exe'
    helper = runtime / 'whisper-vad-speech-segments.exe'
    cli.write_bytes(b'exe')
    helper.write_bytes(b'exe')
    wav = tmp_path / 'audio.wav'
    _write_pcm16_wav(wav, 35.0)
    monkeypatch.setattr(
        subtitles,
        '_run_external_vad',
        lambda *_args, **_kwargs: [
            subtitles.SpeechIsland(1.0, 2.0),
            subtitles.SpeechIsland(30.0, 31.0),
        ],
    )
    monkeypatch.setattr(
        subtitles,
        '_run_whisper_cli_batch',
        lambda *_args, **_kwargs: [
            _cli_payload(_cli_segment(100, 500, '同じ言葉')),
            _cli_payload(_cli_segment(100, 500, '同じ言葉')),
        ],
    )
    result = subtitles._run_whisper(
        str(cli), 'model.bin', 'vad.bin', str(wav),
        str(tmp_path / 'output'), str(tmp_path / 'old.log'), None)

    cues = subtitles.parse_srt(
        open(result, encoding='utf-8').read())
    assert [cue.text for cue in cues] == ['同じ言葉', '同じ言葉']
    assert cues[0].timing != cues[1].timing


def test_generate_reports_no_speech_without_hallucinated_sidecars(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: ('whisper.exe', 'model.bin', 'vad.bin'))
    monkeypatch.setattr(
        subtitles, '_extract_audio',
        lambda _video, wav, _log, _cancel: open(wav, 'wb').close())
    monkeypatch.setattr(
        subtitles, '_run_whisper',
        lambda *_args, **_kwargs: None)

    result = subtitles.generate_subtitles(str(video), 'ja')

    assert result == subtitles.SubtitleResult((), (), no_speech=True)
    assert not (tmp_path / 'movie.ja.srt').exists()


def test_asr_errors_are_classified_without_paths_text_or_raw_logs(tmp_path):
    log = tmp_path / 'private.log'
    log.write_text(
        'failed to load model C:\\private\\name.bin recognized SECRET WORDS',
        encoding='utf-8',
    )
    error = subtitles._asr_failure('runtime', str(log))

    assert str(error) == 'Speech recognition model could not be loaded'
    assert 'private' not in str(error).lower()
    assert 'secret' not in str(error).lower()


def test_subtitle_result_distinguishes_no_speech_from_success():
    regular = subtitles.SubtitleResult((), ())
    no_speech = subtitles.SubtitleResult((), (), no_speech=True)

    assert regular.no_speech is False
    assert no_speech.no_speech is True


def _valid_srt(text):
    return f'1\n00:00:00,000 --> 00:00:01,000\n{text}\n'


def _write_stale_manifest(video, language, subtitle_path, srt_sha256):
    manifest_path = video.with_suffix('.uav-subtitles.json')
    manifest_path.write_text(
        json.dumps({
            'schema': 1,
            'kind': 'uav_subtitle_provenance',
            'tracks': {
                language: {
                    'generator': 'uav',
                    'srt_sha256': srt_sha256,
                    'asr_signature': 'obsolete-asr-pipeline',
                    'translation_signature': 'obsolete-translation',
                },
            },
        }),
        encoding='utf-8',
    )
    return manifest_path


def test_untracked_existing_japanese_is_preserved_as_user_authored(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    japanese = tmp_path / 'movie.ja.srt'
    video.write_bytes(b'video')
    japanese.write_text(_valid_srt('人工修正版'), encoding='utf-8')
    monkeypatch.setattr(
        subtitles,
        '_prepare_runtime',
        lambda *_args: pytest.fail('manual subtitle must not be replaced'),
    )

    result = subtitles.generate_subtitles(str(video), 'ja')

    assert result.files == (str(japanese),)
    assert result.generated == ()
    assert japanese.read_text(encoding='utf-8') == _valid_srt('人工修正版')


def test_stale_app_generated_japanese_is_retranscribed(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    japanese = tmp_path / 'movie.ja.srt'
    video.write_bytes(b'video')
    japanese.write_text(_valid_srt('舊字幕'), encoding='utf-8')
    stale_sha = subtitles._sha256(str(japanese))
    manifest = _write_stale_manifest(video, 'ja', japanese, stale_sha)
    calls = []
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')

    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: ('whisper.exe', 'model.bin', 'vad.bin'))
    monkeypatch.setattr(
        subtitles, '_extract_audio',
        lambda _video, wav, _log, _cancel: open(wav, 'wb').close())

    def fake_whisper(
            _exe, _model, _vad, _wav, output, _log, _cancel, **_kwargs):
        calls.append('transcribed')
        path = output + '.srt'
        subtitles._atomic_write_text(path, _valid_srt('新字幕'))
        return path

    monkeypatch.setattr(subtitles, '_run_whisper', fake_whisper)

    result = subtitles.generate_subtitles(str(video), 'ja')

    assert calls == ['transcribed']
    assert result.generated == (str(japanese),)
    assert japanese.read_text(encoding='utf-8') == _valid_srt('新字幕')
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['tracks']['ja']['asr_signature'] == (
        subtitles._asr_signature(subtitles.recognition_profile()))
    assert payload['tracks']['ja']['srt_sha256'] == subtitles._sha256(
        str(japanese))


def test_profile_change_invalidates_app_generated_derived_subtitle(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    english = tmp_path / 'movie.en.srt'
    video.write_bytes(b'video')
    english.write_text(_valid_srt('Old English'), encoding='utf-8')
    stale_sha = subtitles._sha256(str(english))
    manifest = _write_stale_manifest(video, 'en', english, stale_sha)
    calls = []
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')

    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: ('whisper.exe', 'model.bin', 'vad.bin'))
    monkeypatch.setattr(
        subtitles, '_extract_audio',
        lambda _video, wav, _log, _cancel: open(wav, 'wb').close())

    def fake_whisper(
            _exe, _model, _vad, _wav, output, _log, _cancel, **_kwargs):
        calls.append('transcribed')
        path = output + '.srt'
        subtitles._atomic_write_text(path, _valid_srt('新しい字幕'))
        return path

    def fake_translate(source, destination, *_args):
        calls.append(('translated', subtitles._sha256(source)))
        subtitles._atomic_write_text(
            destination, _valid_srt('Fresh English'))

    monkeypatch.setattr(subtitles, '_run_whisper', fake_whisper)
    monkeypatch.setattr(subtitles, 'translate_srt', fake_translate)

    result = subtitles.generate_subtitles(str(video), 'en')

    assert calls[0] == 'transcribed'
    assert calls[1][0] == 'translated'
    assert result.generated == (str(english),)
    assert english.read_text(encoding='utf-8') == _valid_srt(
        'Fresh English')
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    assert payload['tracks']['en']['asr_signature'] == (
        subtitles._asr_signature(subtitles.recognition_profile()))
    assert payload['tracks']['en']['source_sha256'] == calls[1][1]


def test_user_edit_after_generation_is_never_overwritten(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    japanese = tmp_path / 'movie.ja.srt'
    video.write_bytes(b'video')
    japanese.write_text(_valid_srt('人工修正後'), encoding='utf-8')
    manifest_path = video.with_suffix('.uav-subtitles.json')
    manifest_path.write_text(
        json.dumps({
            'schema': 1,
            'kind': 'uav_subtitle_provenance',
            'tracks': {
                'ja': {
                    'generator': 'uav',
                    'srt_sha256': '0' * 64,
                    'asr_signature': 'obsolete-asr-pipeline',
                },
            },
        }),
        encoding='utf-8',
    )
    monkeypatch.setattr(
        subtitles,
        '_prepare_runtime',
        lambda *_args: pytest.fail('edited subtitle must not be replaced'),
    )

    result = subtitles.generate_subtitles(str(video), 'ja')

    assert result.files == (str(japanese),)
    assert result.generated == ()
    assert japanese.read_text(encoding='utf-8') == _valid_srt('人工修正後')
