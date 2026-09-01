import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from types import SimpleNamespace

import pytest

from uav_downloader.core import config
from uav_downloader.subtitles import engine as subtitles


def _sample_srt(text='こんにちは'):
    return f'1\n00:00:00,000 --> 00:00:01,500\n{text}\n'


def test_normalize_modes_and_language_outputs():
    assert subtitles.normalize_subtitle_mode('zh-TW') == 'zh'
    assert subtitles.normalize_subtitle_mode('multilingual') == 'all'
    assert subtitles.normalize_subtitle_mode('unknown') == 'none'
    assert subtitles.subtitle_languages('all') == ('ja', 'en', 'zh-TW')
    assert subtitles.subtitle_languages('none') == ()


def test_subtitle_media_workspace_uses_the_video_destination_volume(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: ('recognizer.exe', 'model.bin', 'vad.bin'))
    monkeypatch.setattr(
        subtitles, '_extract_audio',
        lambda _video, wav, _log, _cancel:
            open(wav, 'wb').close())

    def fake_recognition(
            _profile, _exe, _model, _vad, _wav, output_base, _log,
            _cancel, **_kwargs):
        output = output_base + '.srt'
        subtitles._atomic_write_text(output, _sample_srt())
        return output

    monkeypatch.setattr(subtitles, '_run_recognition', fake_recognition)
    original_temporary_directory = subtitles.tempfile.TemporaryDirectory
    created = {}

    def tracked_temporary_directory(*args, **kwargs):
        created['dir'] = kwargs.get('dir')
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        subtitles.tempfile, 'TemporaryDirectory',
        tracked_temporary_directory)

    result = subtitles.generate_subtitles(str(video), 'ja')

    assert result.files == (str(tmp_path / 'movie.ja.srt'),)
    assert os.path.abspath(created['dir']) == os.path.abspath(tmp_path)


def test_trusted_source_chinese_satisfies_zh_before_any_pipeline_or_write(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    forbidden = (
        'recognition_profile',
        '_media_identity',
        '_selected_translation_profile',
        '_prepare_runtime',
        '_extract_audio',
        '_run_recognition',
        'translate_srt_to_zh_tw',
        '_atomic_write_text',
    )
    for name in forbidden:
        monkeypatch.setattr(
            subtitles, name,
            lambda *_args, _name=name, **_kwargs:
                pytest.fail(f'{_name} must not run for source-satisfied zh'))
    progress = []

    result = subtitles.generate_subtitles(
        str(video),
        'zh',
        progress_callback=lambda stage, percent:
            progress.append((stage, percent)),
        source_subtitle_evidence=(
            'missav-url-chinese-subtitle',),
    )

    assert result == subtitles.SubtitleResult(
        (), (), satisfied_by_source=('zh-TW',))
    assert progress == [('done', 100)]
    assert list(tmp_path.iterdir()) == [video]


def test_source_satisfied_zh_reports_existing_valid_sidecar_without_rewriting(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    chinese = tmp_path / 'movie.zh-TW.srt'
    chinese.write_text(_sample_srt('既有字幕'), encoding='utf-8')
    original = chinese.read_bytes()
    forbidden = (
        'recognition_profile',
        '_media_identity',
        '_selected_translation_profile',
        '_prepare_runtime',
        '_extract_audio',
        '_run_recognition',
        'translate_srt_to_zh_tw',
        '_atomic_write_text',
    )
    for name in forbidden:
        monkeypatch.setattr(
            subtitles, name,
            lambda *_args, _name=name, **_kwargs:
                pytest.fail(f'{_name} must not run for source-satisfied zh'))

    result = subtitles.generate_subtitles(
        str(video),
        'zh',
        source_subtitle_evidence=(
            'supjav-category-chinese-subtitle',),
    )

    assert result == subtitles.SubtitleResult(
        (str(chinese),), (), satisfied_by_source=('zh-TW',))
    assert chinese.read_bytes() == original


def test_all_mode_generates_only_ja_and_en_when_source_satisfies_chinese(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    chinese = tmp_path / 'movie.zh-TW.srt'
    chinese.write_text(_sample_srt('既有字幕'), encoding='utf-8')
    original_chinese = chinese.read_bytes()
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: ('recognizer.exe', 'model.bin', 'vad.bin'))
    monkeypatch.setattr(
        subtitles, '_extract_audio',
        lambda _video, wav, _log, _cancel:
            open(wav, 'wb').close())

    def fake_recognition(
            _profile, _exe, _model, _vad, _wav, output_base, _log,
            _cancel, **_kwargs):
        output = output_base + '.srt'
        subtitles._atomic_write_text(output, _sample_srt('日本語'))
        return output

    monkeypatch.setattr(
        subtitles, '_run_recognition', fake_recognition)
    monkeypatch.setattr(
        subtitles, '_selected_translation_profile',
        lambda: SimpleNamespace(uses_api=False))
    monkeypatch.setattr(
        subtitles, '_translation_signature',
        lambda _profile: 'translation-signature')

    def fake_translate(source, destination, target, *_args, **_kwargs):
        assert target == 'en'
        assert os.path.isfile(source)
        subtitles._atomic_write_text(
            destination, _sample_srt('English'))
        return destination

    monkeypatch.setattr(subtitles, 'translate_srt', fake_translate)
    monkeypatch.setattr(
        subtitles, 'translate_srt_to_zh_tw',
        lambda *_args, **_kwargs:
            pytest.fail('Chinese sidecar must not be generated'))

    result = subtitles.generate_subtitles(
        str(video),
        'all',
        source_subtitle_evidence=(
            'jable-category-chinese-subtitle',),
    )

    japanese = tmp_path / 'movie.ja.srt'
    english = tmp_path / 'movie.en.srt'
    assert result.files == (str(japanese), str(english), str(chinese))
    assert result.generated == (str(japanese), str(english))
    assert result.satisfied_by_source == ('zh-TW',)
    assert japanese.is_file()
    assert english.is_file()
    assert chinese.read_bytes() == original_chinese


def test_parse_and_render_srt_round_trip():
    source = (
        '\ufeff1\r\n00:00:00,000 --> 00:00:01,500\r\nこんにちは\r\n\r\n'
        '2\r\n00:00:02,000 --> 00:00:03,000\r\nまたね\r\n'
    )
    cues = subtitles.parse_srt(source)
    assert [cue.text for cue in cues] == ['こんにちは', 'またね']
    rendered = subtitles.render_srt(cues)
    assert '00:00:02,000 --> 00:00:03,000' in rendered
    assert rendered.endswith('\n')


def test_parse_srt_rejects_malformed_content():
    with pytest.raises(subtitles.SubtitleError):
        subtitles.parse_srt('not an srt file')


def test_translation_source_has_no_google_provider():
    source = open(subtitles.__file__, encoding='utf-8').read().lower()
    assert 'translate.googleapis.com' not in source
    assert 'client=gtx' not in source
    assert 'translationratelimited' not in source
    assert '.post(' not in source


def test_verified_file_cache_detects_same_process_mutation(tmp_path):
    component = tmp_path / 'component.bin'
    component.write_bytes(b'original')
    expected_hash = hashlib.sha256(b'original').hexdigest()
    subtitles._verified_paths.clear()

    assert subtitles._is_verified(
        str(component), len(b'original'), expected_hash)
    old_stat = component.stat()
    component.write_bytes(b'tampered')
    os.utime(
        component,
        ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 2_000_000_000))

    assert not subtitles._is_verified(
        str(component), len(b'original'), expected_hash)


def test_whisper_runtime_verifier_checks_every_required_native_file(
        monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    component = runtime / 'Release' / 'whisper-cli.exe'
    component.parent.mkdir(parents=True)
    component.write_bytes(b'known-runtime')
    digest = hashlib.sha256(b'known-runtime').hexdigest()
    (runtime / '.source-sha256').write_text('archive-hash', encoding='ascii')
    monkeypatch.setattr(subtitles, 'WHISPER_ARCHIVE_SHA256', 'archive-hash')
    monkeypatch.setattr(
        subtitles, 'WHISPER_RUNTIME_FILES',
        {'Release/whisper-cli.exe': (len(b'known-runtime'), digest)})
    subtitles._verified_paths.clear()

    assert subtitles._verify_whisper_install(str(runtime)) == str(component)
    component.write_bytes(b'broken-runtim')
    assert subtitles._verify_whisper_install(str(runtime)) is None


def test_interprocess_cache_lock_serializes_two_app_processes(tmp_path):
    log = tmp_path / 'lock-order.txt'
    code = (
        "import os,sys,time\n"
        "os.environ['LOCALAPPDATA']=sys.argv[1]\n"
        "from uav_downloader.subtitles.engine import _interprocess_cache_lock\n"
        "name=sys.argv[2]\n"
        "log=sys.argv[3]\n"
        "def write(value):\n"
        "  with open(log,'a',encoding='utf-8') as handle:\n"
        "    handle.write(value+'\\n'); handle.flush(); os.fsync(handle.fileno())\n"
        "with _interprocess_cache_lock('pytest-shared-cache',None,10):\n"
        "  write(name+'-enter')\n"
        "  if name=='A': time.sleep(0.4)\n"
        "  write(name+'-exit')\n"
    )
    root = os.path.dirname(os.path.dirname(__file__))
    child_env = os.environ.copy()
    source_root = os.path.join(root, 'src')
    child_env['PYTHONPATH'] = os.pathsep.join(filter(None, (
        source_root,
        child_env.get('PYTHONPATH', ''),
    )))
    first = subprocess.Popen(
        [sys.executable, '-c', code, str(tmp_path), 'A', str(log)],
        cwd=root, env=child_env)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if log.is_file() and 'A-enter' in log.read_text(encoding='utf-8'):
            break
        time.sleep(0.02)
    else:
        first.kill()
        pytest.fail('first process did not acquire the cache lock')

    second = subprocess.Popen(
        [sys.executable, '-c', code, str(tmp_path), 'B', str(log)],
        cwd=root, env=child_env)
    assert first.wait(timeout=15) == 0
    assert second.wait(timeout=15) == 0
    assert log.read_text(encoding='utf-8').splitlines() == [
        'A-enter', 'A-exit', 'B-enter', 'B-exit']


def test_translate_cues_uses_exact_domain_rules_and_dedupes_model_work(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    calls = []

    def fake_model(
            model_dir, texts, target_token, _cancel, _progress=None):
        calls.append((model_dir, list(texts), target_token))
        return ['Unknown line.' for _ in texts]

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    result = subtitles.translate_cues(
        ['やめて', '未知の台詞', '  未知の台詞  '],
        'ja', 'en', 'translate_en')

    assert result == ['Stop.', 'Unknown line.', 'Unknown line.']
    assert calls == [('ja-model', ['未知の台詞'], None)]


def test_translate_cues_polishes_lowercase_english_model_output(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    monkeypatch.setattr(
        subtitles, '_run_local_model',
        lambda *_args: ["i'm ready. i will start now."])

    assert subtitles.translate_cues(
        ['人工詞庫にない文章'], 'ja', 'en', 'translate_en') == [
            "I'm ready. I will start now."]


def test_japanese_to_chinese_pivot_keeps_raw_english_model_casing(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    calls = []

    def fake_model(
            model_dir, texts, target_token, _cancel, _progress=None):
        calls.append((model_dir, list(texts), target_token))
        if model_dir == 'ja-model':
            return ["i'm ready to start"]
        return ['準備開始']

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    monkeypatch.setattr(subtitles, '_to_taiwan_chinese', lambda text: text)

    assert subtitles.translate_cues(
        ['人工詞庫にない文章'], 'ja', 'zh-TW', 'translate_zh') == ['準備開始']
    assert calls[1] == (
        'zh-model', ["i'm ready to start"], '>>cmn_Hant<<')


def test_translate_cues_pivots_unknown_japanese_to_taiwan_chinese(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    calls = []

    def fake_model(
            model_dir, texts, target_token, _cancel, _progress=None):
        calls.append((model_dir, list(texts), target_token))
        if model_dir == 'ja-model':
            return ['This is a test.' for _ in texts]
        return ['這是測試。' for _ in texts]

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    monkeypatch.setattr(subtitles, '_to_taiwan_chinese', lambda text: text)
    progress = []

    result = subtitles.translate_cues(
        ['人工詞庫にない長い文章です。', '人工詞庫にない長い文章です。'],
        'ja', 'zh-TW', 'translate_zh',
        lambda stage, percent: progress.append((stage, percent)))

    assert result == ['這是測試。', '這是測試。']
    assert calls == [
        ('ja-model', ['人工詞庫にない長い文章です'], None),
        ('zh-model', ['This is a test'], '>>cmn_Hant<<'),
    ]
    percentages = [
        percent for stage, percent in progress
        if stage == 'translate_zh' and percent is not None
    ]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100


def test_exact_japanese_to_taiwan_translation_honours_immediate_cancel():
    with pytest.raises(subtitles.SubtitleCancelled):
        subtitles.translate_cues(
            ['やめて'], 'ja', 'zh-TW', 'translate_zh',
            cancel_check=lambda: True)


def test_local_model_honours_cancel_before_loading_runtime():
    with pytest.raises(subtitles.SubtitleCancelled):
        subtitles._run_local_model(
            'model-that-must-not-be-opened', ['字幕'], None,
            cancel_check=lambda: True)


def test_local_model_isolates_each_job_in_a_fresh_worker(
        monkeypatch, tmp_path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    (model_dir / 'source.spm').write_bytes(b'source tokenizer')
    (model_dir / 'target.spm').write_bytes(b'target tokenizer')
    processes = []

    class FakeProcess:
        def __init__(self, *, target, args):
            self.target = target
            self.args = args
            self.exitcode = None
            self.alive = False
            self.terminated = False
            self.closed = False
            processes.append(self)

        def start(self):
            self.alive = True
            request_path, result_path, progress_path = self.args
            payload = json.loads(
                open(request_path, encoding='utf-8').read())
            subtitles._atomic_write_text(
                progress_path,
                json.dumps({
                    'schema': 1,
                    'completed': len(payload['texts']),
                    'total': len(payload['texts']),
                }))
            subtitles._atomic_write_text(
                result_path,
                json.dumps({
                    'schema': 1,
                    'ok': True,
                    'translations': list(payload['texts']),
                }))
            self.alive = False
            self.exitcode = 0

        def is_alive(self):
            return self.alive

        def join(self, _timeout=None):
            return None

        def terminate(self):
            self.terminated = True
            self.alive = False
            self.exitcode = -15

        def kill(self):
            self.terminate()

        def close(self):
            self.closed = True

    class FakeContext:
        Process = FakeProcess

    monkeypatch.setattr(
        subtitles.multiprocessing, 'get_context',
        lambda method: FakeContext() if method == 'spawn' else None)

    for job in range(20):
        assert subtitles._run_local_model(
            str(model_dir), [f'job {job}'], None, None) == [f'job {job}']

    assert len(processes) == 20
    assert all(
        process.exitcode == 0
        and not process.terminated
        and process.closed
        for process in processes
    )


def test_local_translation_worker_executes_model_and_reports_progress(
        monkeypatch, tmp_path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    (model_dir / 'source.spm').write_bytes(b'source tokenizer')
    (model_dir / 'target.spm').write_bytes(b'target tokenizer')
    request_path = tmp_path / 'request.json'
    result_path = tmp_path / 'result.json'
    progress_path = tmp_path / 'progress.json'

    class FakeProcessor:
        def __init__(self, *, model_file):
            assert os.path.isfile(model_file)

        def encode(self, text, *, out_type):
            assert out_type is str
            return [str(text)]

        def decode(self, tokens):
            return ' '.join(tokens)

    class FakeTranslator:
        def __init__(self, path, **_kwargs):
            assert path == os.path.abspath(model_dir)

        def translate_batch(self, batches, **_kwargs):
            return [
                SimpleNamespace(hypotheses=[list(tokens)])
                for tokens in batches
            ]

    monkeypatch.setitem(
        sys.modules, 'sentencepiece',
        SimpleNamespace(SentencePieceProcessor=FakeProcessor))
    monkeypatch.setitem(
        sys.modules, 'ctranslate2',
        SimpleNamespace(Translator=FakeTranslator))
    request_path.write_text(json.dumps({
        'schema': subtitles.LOCAL_TRANSLATION_WORKER_SCHEMA,
        'model_dir': os.path.abspath(model_dir),
        'texts': ['first', 'second'],
        'target_token': None,
    }), encoding='utf-8')

    subtitles._local_translation_worker_entry(
        str(request_path), str(result_path), str(progress_path))

    result = json.loads(result_path.read_text(encoding='utf-8'))
    progress = json.loads(progress_path.read_text(encoding='utf-8'))
    assert result == {
        'schema': subtitles.LOCAL_TRANSLATION_WORKER_SCHEMA,
        'ok': True,
        'translations': ['first', 'second'],
    }
    assert progress == {
        'schema': subtitles.LOCAL_TRANSLATION_WORKER_SCHEMA,
        'completed': 2,
        'total': 2,
    }


def test_local_translation_worker_caps_result_before_writing(
        monkeypatch, tmp_path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    request_path = tmp_path / 'request.json'
    result_path = tmp_path / 'result.json'
    progress_path = tmp_path / 'progress.json'
    request_path.write_text(json.dumps({
        'schema': subtitles.LOCAL_TRANSLATION_WORKER_SCHEMA,
        'model_dir': os.path.abspath(model_dir),
        'texts': ['cue'],
        'target_token': None,
    }), encoding='utf-8')
    monkeypatch.setattr(
        subtitles, 'LOCAL_TRANSLATION_WORKER_MAX_BYTES', 512)
    monkeypatch.setattr(
        subtitles, '_run_local_model_in_process',
        lambda *_args, **_kwargs: ['x' * 1024])

    subtitles._local_translation_worker_entry(
        str(request_path), str(result_path), str(progress_path))

    result = json.loads(result_path.read_text(encoding='utf-8'))
    assert result == {
        'schema': subtitles.LOCAL_TRANSLATION_WORKER_SCHEMA,
        'ok': False,
        'error': 'Local translation worker returned too much data',
    }
    assert result_path.stat().st_size <= 512


def test_local_model_cancel_terminates_worker_and_removes_ipc(
        monkeypatch, tmp_path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    created_dirs = []
    processes = []

    class StalledProcess:
        exitcode = None

        def __init__(self, *, target, args):
            del target
            self.args = args
            self.alive = False
            self.terminated = False
            self.closed = False
            processes.append(self)

        def start(self):
            self.alive = True
            created_dirs.append(os.path.dirname(self.args[0]))

        def is_alive(self):
            return self.alive

        def join(self, _timeout=None):
            return None

        def terminate(self):
            self.terminated = True
            self.alive = False
            self.exitcode = -15

        def kill(self):
            self.terminate()

        def close(self):
            self.closed = True

    class FakeContext:
        Process = StalledProcess

    monkeypatch.setattr(
        subtitles.multiprocessing, 'get_context',
        lambda _method: FakeContext())
    checks = iter((False, True))

    with pytest.raises(subtitles.SubtitleCancelled):
        subtitles._run_local_model(
            str(model_dir), ['cue'], None,
            lambda: next(checks, True))

    assert len(processes) == 1
    assert processes[0].terminated
    assert processes[0].closed
    assert created_dirs and not os.path.exists(created_dirs[0])


def test_local_model_timeout_terminates_worker(
        monkeypatch, tmp_path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    processes = []

    class StalledProcess:
        exitcode = None

        def __init__(self, *, target, args):
            del target, args
            self.alive = False
            self.terminated = False
            self.closed = False
            processes.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, _timeout=None):
            return None

        def terminate(self):
            self.terminated = True
            self.alive = False
            self.exitcode = -15

        def kill(self):
            self.terminate()

        def close(self):
            self.closed = True

    class FakeContext:
        Process = StalledProcess

    monkeypatch.setattr(
        subtitles.multiprocessing, 'get_context',
        lambda _method: FakeContext())
    monkeypatch.setattr(
        subtitles, 'LOCAL_TRANSLATION_WORKER_TIMEOUT_SECONDS', -1)

    with pytest.raises(
            subtitles.SubtitleProcessTimeout,
            match='exceeded its time limit'):
        subtitles._run_local_model(
            str(model_dir), ['cue'], None, None)

    assert len(processes) == 1
    assert processes[0].terminated
    assert processes[0].closed


def test_local_model_preserves_ipc_if_worker_cannot_be_stopped(
        monkeypatch, tmp_path):
    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    created_dirs = []

    class UnstoppableProcess:
        exitcode = None

        def __init__(self, *, target, args):
            del target
            self.args = args
            self.alive = False

        def start(self):
            self.alive = True
            created_dirs.append(os.path.dirname(self.args[0]))

        def is_alive(self):
            return self.alive

        def join(self, _timeout=None):
            return None

        def terminate(self):
            return None

        def kill(self):
            return None

        def close(self):
            pytest.fail('a live worker handle must not be closed')

    class FakeContext:
        Process = UnstoppableProcess

    monkeypatch.setattr(
        subtitles.multiprocessing, 'get_context',
        lambda _method: FakeContext())
    monkeypatch.setattr(
        subtitles, 'LOCAL_TRANSLATION_WORKER_TIMEOUT_SECONDS', -1)

    try:
        with pytest.raises(
                subtitles.SubtitleError,
                match='could not be stopped'):
            subtitles._run_local_model(
                str(model_dir), ['cue'], None, None)

        assert len(created_dirs) == 1
        assert os.path.isdir(created_dirs[0])
        assert os.path.isfile(os.path.join(created_dirs[0], 'request.json'))
    finally:
        for path in created_dirs:
            shutil.rmtree(path, ignore_errors=True)


def test_local_translation_memory_is_exact_and_versioned(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    versions = ['engine-v1']
    monkeypatch.setattr(
        subtitles, '_translation_memory_version', lambda: versions[0])
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    model_calls = []

    def fake_model(
            _model_dir, texts, _target_token, _cancel, _progress=None):
        model_calls.append(list(texts))
        return ['A cached translation.' for _ in texts]

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    first = subtitles.translate_cues(
        ['未登録の文章'], 'ja', 'en', 'translate_en')
    assert first == ['A cached translation.']
    assert model_calls == [['未登録の文章']]

    monkeypatch.setattr(
        subtitles, '_run_local_model',
        lambda *_args: pytest.fail('an exact cache hit must skip the model'))
    second = subtitles.translate_cues(
        ['未登録の文章'], 'ja', 'en', 'translate_en')
    assert second == first

    versions[0] = 'engine-v2'
    monkeypatch.setattr(
        subtitles, '_run_local_model',
        lambda *_args: ['A new-version translation.'])
    third = subtitles.translate_cues(
        ['未登録の文章'], 'ja', 'en', 'translate_en')
    assert third == ['A new-version translation.']


def test_translation_memory_version_includes_verified_model_manifest():
    assert subtitles.TRANSLATION_MANIFEST_SHA256
    assert (
        subtitles.TRANSLATION_MANIFEST_SHA256
        in subtitles._translation_memory_version()
    )


def test_invalid_translation_memory_row_falls_back_to_model(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    source = '人工詞庫にない安全な文章'
    subtitles._translation_memory_store(
        {source: '00:00:00,000 --> 00:00:01,000'},
        'ja', 'en')
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    calls = []

    def fake_model(
            _model_dir, texts, _target_token, _cancel, _progress=None):
        calls.append(list(texts))
        return ['Valid replacement.']

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    assert subtitles.translate_cues(
        [source], 'ja', 'en', 'translate_en') == ['Valid replacement.']
    assert calls == [[source]]


def test_obsolete_translation_backup_cleanup_is_best_effort(monkeypatch):
    monkeypatch.setattr(
        subtitles, '_remove_translation_cache_path',
        lambda *_args: (_ for _ in ()).throw(PermissionError('busy')))
    subtitles._remove_translation_cache_path_best_effort(
        r'C:\cache\v1.previous', r'C:\cache')


def test_corrupt_translation_memory_fails_open_without_losing_output(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    path = subtitles._translation_memory_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(b'not a sqlite database')
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    monkeypatch.setattr(
        subtitles, '_run_local_model',
        lambda *_args: ['Translation survives.'])

    assert subtitles.translate_cues(
        ['未登録の長い文章'], 'ja', 'en', 'translate_en') == [
            'Translation survives.']


def test_chinese_stage_reuses_prior_english_model_result(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    calls = []

    def fake_model(
            model_dir, texts, target_token, _cancel, _progress=None):
        calls.append((model_dir, list(texts), target_token))
        if model_dir == 'ja-model':
            return ['Previously translated.' for _ in texts]
        return ['先前已翻譯。' for _ in texts]

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    monkeypatch.setattr(subtitles, '_to_taiwan_chinese', lambda text: text)
    source = ['人工詞庫之外の文章。']
    assert subtitles.translate_cues(
        source, 'ja', 'en', 'translate_en') == ['Previously translated.']
    assert subtitles.translate_cues(
        source, 'ja', 'zh-TW', 'translate_zh') == ['先前已翻譯。']
    assert calls == [
        ('ja-model', ['人工詞庫之外の文章'], None),
        ('zh-model', ['Previously translated'], '>>cmn_Hant<<'),
    ]


def test_model_dedupe_keeps_terminal_punctuation_input_order_independent(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {'ja-en': 'ja-model', 'en-zh': 'zh-model'})
    calls = []

    def fake_model(
            _model_dir, texts, _target_token, _cancel, _progress=None):
        calls.append(list(texts))
        return ['Translated' for _ in texts]

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    plain = '未登録の台詞'
    emphatic = '未登録の台詞！'

    assert subtitles.translate_cues(
        [plain, emphatic], 'ja', 'en', 'translate_en') == [
            'Translated', 'Translated!']
    assert subtitles.translate_cues(
        [emphatic, plain], 'ja', 'en', 'translate_en') == [
            'Translated!', 'Translated']
    assert calls == [[plain]]


def test_taiwan_model_output_uses_fullwidth_terminal_punctuation():
    assert subtitles._restore_terminal_punctuation(
        '痛くないですか？', '會痛嗎?', 'zh-TW') == '會痛嗎？'
    assert subtitles._restore_terminal_punctuation(
        '止めて！', '停下來!', 'zh-TW') == '停下來！'
    assert subtitles._restore_terminal_punctuation(
        '終わりました。', '結束了.', 'zh-TW') == '結束了。'


def test_local_translation_diagnostic_writes_complete_atomic_evidence(
        monkeypatch, tmp_path):
    calls = []

    def fake_translate(texts, source_language, target_language, stage):
        calls.append((list(texts), source_language, target_language, stage))
        return (
            ['English diagnostic.']
            if target_language == 'en'
            else ['繁中診斷。']
        )

    monkeypatch.setattr(subtitles, 'translate_cues', fake_translate)
    monkeypatch.setattr(
        subtitles, '_to_taiwan_chinese',
        lambda _text: '軟體和影片在這裡。')
    output = tmp_path / 'diagnostic.json'

    payload = subtitles.run_local_translation_diagnostic(str(output))

    assert payload['schema'] == 1
    assert payload['english'] == 'English diagnostic.'
    assert payload['taiwan'] == '繁中診斷。'
    assert payload['opencc'] == '軟體和影片在這裡。'
    assert payload['phrase_count'] >= 900
    assert json.loads(output.read_text(encoding='utf-8')) == payload
    assert not list(tmp_path.glob('*.tmp'))
    assert [call[2] for call in calls] == ['en', 'zh-TW']


def test_local_translation_worker_soak_diagnostic_runs_one_parent_cycle(
        monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        subtitles, '_prepare_translation_runtime',
        lambda _progress, _cancel: {
            'ja-en': 'ja-model',
            'en-zh': 'zh-model',
        })

    def fake_model(model_dir, texts, target_token, cancel_check):
        calls.append((
            model_dir, list(texts), target_token, cancel_check))
        return ['translated']

    metric_calls = []

    def fake_metrics():
        metric_calls.append(len(metric_calls) + 1)
        return {
            'working_set_bytes': 40_000_000,
            'private_bytes': 30_000_000,
            'handles': 240,
        }

    monkeypatch.setattr(subtitles, '_run_local_model', fake_model)
    monkeypatch.setattr(
        subtitles, '_translation_soak_process_metrics', fake_metrics)
    output = tmp_path / 'worker-soak.json'

    payload = subtitles.run_local_translation_worker_soak_diagnostic(
        str(output), iterations=3)

    assert payload['kind'] == 'uav_local_translation_worker_soak'
    assert payload['iterations'] == 3
    assert payload['worker_cycles'] == 6
    assert [row['job'] for row in payload['rows']] == [1, 2, 3]
    assert all(
        row['private_bytes'] == 30_000_000
        and row['handles'] == 240
        for row in payload['rows'])
    assert len(calls) == 6
    assert [call[0] for call in calls] == [
        'ja-model', 'zh-model',
        'ja-model', 'zh-model',
        'ja-model', 'zh-model',
    ]
    assert json.loads(output.read_text(encoding='utf-8')) == payload


def test_llm_translation_diagnostic_uses_api_branch_and_redacts_evidence(
        monkeypatch, tmp_path):
    profile = SimpleNamespace(
        provider='openai-compatible',
        model='diagnostic-model',
        base_url='http://127.0.0.1:18080/private-account/v1',
        api_key='must-never-appear',
        uses_api=True,
    )
    monkeypatch.setattr(
        subtitles, '_selected_translation_profile', lambda: profile)
    calls = []
    private_translation = 'Private provider response body.'

    def fake_api(
            texts, source_language, target_language, progress_stage,
            progress_callback, cancel_check, selected_profile):
        calls.append((
            list(texts), source_language, target_language, progress_stage,
            progress_callback, cancel_check, selected_profile,
        ))
        return [private_translation]

    monkeypatch.setattr(subtitles, '_translate_api_direct', fake_api)
    output = tmp_path / 'llm-diagnostic.json'

    payload = subtitles.run_llm_translation_diagnostic(str(output))

    assert len(calls) == 1
    texts, source, target, stage, _progress, _cancel, selected = calls[0]
    assert len(texts) == 1
    assert '確認番号' in texts[0]
    assert source == 'ja'
    assert target == 'en'
    assert stage == 'diagnostic_llm_translate_en'
    assert selected is profile
    assert payload['kind'] == 'llm_translation'
    assert payload['provider'] == 'openai-compatible'
    assert payload['model'] == 'diagnostic-model'
    assert payload['cue_count'] == 1
    assert len(payload['endpoint_sha256']) == 64
    assert len(payload['source_sha256']) == 64
    assert len(payload['translation_sha256']) == 64
    serialized = output.read_text(encoding='utf-8')
    assert json.loads(serialized) == payload
    assert profile.api_key not in serialized
    assert profile.base_url not in serialized
    assert texts[0] not in serialized
    assert private_translation not in serialized
    assert not list(tmp_path.glob('*.tmp'))


def test_llm_translation_diagnostic_requires_api_and_sanitizes_failures(
        monkeypatch, tmp_path):
    local_profile = SimpleNamespace(
        provider='local', model='', base_url='', api_key='', uses_api=False)
    monkeypatch.setattr(
        subtitles, '_selected_translation_profile', lambda: local_profile)
    with pytest.raises(subtitles.SubtitleError, match='external'):
        subtitles.run_llm_translation_diagnostic(
            str(tmp_path / 'local.json'))

    secret = 'never-log-this-key'
    api_profile = SimpleNamespace(
        provider='openai',
        model='diagnostic-model',
        base_url='https://api.example.test/response-body-token',
        api_key=secret,
        uses_api=True,
    )
    monkeypatch.setattr(
        subtitles, '_selected_translation_profile', lambda: api_profile)

    def fail_api(*_args, **_kwargs):
        raise subtitles.SubtitleError(
            f'{secret} https://api.example.test private response body')

    monkeypatch.setattr(subtitles, '_translate_api_direct', fail_api)
    with pytest.raises(subtitles.SubtitleError) as caught:
        subtitles.run_llm_translation_diagnostic(
            str(tmp_path / 'failed.json'))
    message = str(caught.value)
    assert secret not in message
    assert 'api.example.test' not in message
    assert 'response body' not in message
    assert caught.value.__cause__ is None
    assert not (tmp_path / 'failed.json').exists()


def test_prepare_translation_runtime_installs_and_verifies_pack(
        monkeypatch, tmp_path):
    payload = {}
    for model in ('fugumt-ja-en-int8', 'opus-mt-en-zh-int8'):
        for filename in (
                'config.json', 'model.bin', 'shared_vocabulary.json',
                'source.spm', 'target.spm'):
            path = f'models/{model}/{filename}'
            payload[path] = f'{model}:{filename}'.encode()
    payload['models/fugumt-ja-en-int8/vocab.json'] = b'{"<unk>": 0}'
    manifest = {
        'pack_version': 'test',
        'models': {
            'ja-en': {'path': 'models/fugumt-ja-en-int8'},
            'en-zh': {'path': 'models/opus-mt-en-zh-int8'},
        },
        'files': [
            {
                'path': path,
                'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
            }
            for path, data in sorted(payload.items())
        ],
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True) + '\n').encode()
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('manifest.json', manifest_bytes)
        for path, data in payload.items():
            bundle.writestr(path, data)

    archive_bytes = archive.read_bytes()
    monkeypatch.setattr(subtitles, 'TRANSLATION_PACK_VERSION', 'test')
    monkeypatch.setattr(subtitles, 'TRANSLATION_PACK_NAME', 'models.zip')
    monkeypatch.setattr(
        subtitles, 'TRANSLATION_PACK_SIZE', len(archive_bytes))
    monkeypatch.setattr(
        subtitles, 'TRANSLATION_PACK_SHA256',
        hashlib.sha256(archive_bytes).hexdigest())
    monkeypatch.setattr(
        subtitles, 'TRANSLATION_MANIFEST_SHA256',
        hashlib.sha256(manifest_bytes).hexdigest())
    monkeypatch.setattr(subtitles, '_cache_root', lambda: str(tmp_path / 'cache'))
    subtitles._verified_paths.clear()
    downloads = []

    def fake_download(_url, destination, _size, _sha, _stage, _cb, _cancel):
        downloads.append(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(archive, destination)
        return destination

    monkeypatch.setattr(subtitles, '_download_verified', fake_download)
    models = subtitles._prepare_translation_runtime(None, None)
    assert os.path.isfile(os.path.join(models['ja-en'], 'model.bin'))
    assert os.path.isfile(os.path.join(models['en-zh'], 'model.bin'))
    assert not os.path.exists(downloads[0])

    again = subtitles._prepare_translation_runtime(None, None)
    assert again == models
    assert len(downloads) == 1

    runtime_root = os.path.dirname(os.path.dirname(models['ja-en']))
    stale_parent = os.path.dirname(runtime_root)
    stale = os.path.join(
        stale_parent, 'translation-vtest-install-stale')
    os.makedirs(stale)
    with open(os.path.join(stale, 'partial.bin'), 'wb') as handle:
        handle.write(b'partial')
    foreign_install = os.path.join(
        stale_parent, 'translation-vother-install-active')
    os.makedirs(foreign_install)
    with open(os.path.join(foreign_install, 'active.bin'), 'wb') as handle:
        handle.write(b'active')
    previous = runtime_root + '.previous'
    shutil.copytree(runtime_root, previous)
    with open(os.path.join(models['ja-en'], 'model.bin'), 'wb') as handle:
        handle.write(b'tampered')
    subtitles._verified_paths.clear()
    recovered = subtitles._prepare_translation_runtime(None, None)
    assert len(downloads) == 1
    assert not os.path.exists(stale)
    assert os.path.isfile(os.path.join(foreign_install, 'active.bin'))
    assert not os.path.exists(previous)
    assert open(
        os.path.join(recovered['ja-en'], 'model.bin'), 'rb').read() == (
            payload['models/fugumt-ja-en-int8/model.bin'])

    with open(os.path.join(recovered['ja-en'], 'model.bin'), 'wb') as handle:
        handle.write(b'tampered again')
    subtitles._verified_paths.clear()
    repaired = subtitles._prepare_translation_runtime(None, None)
    assert len(downloads) == 2
    assert open(
        os.path.join(repaired['ja-en'], 'model.bin'), 'rb').read() == (
            payload['models/fugumt-ja-en-int8/model.bin'])


def test_prepare_translation_runtime_accepts_verified_zip_beside_frozen_exe(
        monkeypatch, tmp_path):
    payload = {}
    for model in ('fugumt-ja-en-int8', 'opus-mt-en-zh-int8'):
        for filename in (
                'config.json', 'model.bin', 'shared_vocabulary.json',
                'source.spm', 'target.spm'):
            path = f'models/{model}/{filename}'
            payload[path] = f'{model}:{filename}'.encode()
    payload['models/fugumt-ja-en-int8/vocab.json'] = b'{"<unk>": 0}'
    manifest = {
        'pack_version': 'test',
        'models': {
            'ja-en': {'path': 'models/fugumt-ja-en-int8'},
            'en-zh': {'path': 'models/opus-mt-en-zh-int8'},
        },
        'files': [
            {
                'path': path,
                'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
            }
            for path, data in sorted(payload.items())
        ],
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True) + '\n').encode()
    app_dir = tmp_path / 'portable-app'
    app_dir.mkdir()
    executable = app_dir / 'UAV_Browser.exe'
    executable.write_bytes(b'MZ')
    archive = app_dir / 'models.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('manifest.json', manifest_bytes)
        for path, data in payload.items():
            bundle.writestr(path, data)

    archive_bytes = archive.read_bytes()
    monkeypatch.setattr(subtitles, 'TRANSLATION_PACK_VERSION', 'test')
    monkeypatch.setattr(subtitles, 'TRANSLATION_PACK_NAME', 'models.zip')
    monkeypatch.setattr(
        subtitles, 'TRANSLATION_PACK_SIZE', len(archive_bytes))
    monkeypatch.setattr(
        subtitles, 'TRANSLATION_PACK_SHA256',
        hashlib.sha256(archive_bytes).hexdigest())
    monkeypatch.setattr(
        subtitles, 'TRANSLATION_MANIFEST_SHA256',
        hashlib.sha256(manifest_bytes).hexdigest())
    monkeypatch.setattr(
        subtitles, '_cache_root', lambda: str(tmp_path / 'cache'))
    monkeypatch.setattr(subtitles.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(subtitles.sys, 'executable', str(executable))
    monkeypatch.setattr(
        subtitles, '_download_verified',
        lambda *_args, **_kwargs:
            pytest.fail('verified local component must avoid network access'))
    subtitles._verified_paths.clear()

    models = subtitles._prepare_translation_runtime(None, None)

    assert os.path.isfile(os.path.join(models['ja-en'], 'model.bin'))
    assert os.path.isfile(os.path.join(models['en-zh'], 'model.bin'))

    shutil.rmtree(tmp_path / 'cache')
    extracted = app_dir / 'models'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    archive.unlink()
    subtitles._verified_paths.clear()

    models = subtitles._prepare_translation_runtime(None, None)

    assert os.path.commonpath(
        [os.path.abspath(models['ja-en']), os.path.abspath(extracted)]
    ) == os.path.abspath(extracted)
    assert os.path.isfile(os.path.join(models['ja-en'], 'model.bin'))


def test_translate_srt_preserves_timestamps(monkeypatch, tmp_path):
    source = tmp_path / 'video.ja.srt'
    destination = tmp_path / 'video.zh-TW.srt'
    source.write_text(
        _sample_srt('一番目') + '\n2\n00:00:02,000 --> 00:00:03,000\n二番目\n',
        encoding='utf-8')

    monkeypatch.setattr(
        subtitles, 'translate_cues',
        lambda texts, _source, _target, _stage, _progress, _cancel:
            [f'中:{text}' for text in texts])

    subtitles.translate_srt_to_zh_tw(str(source), str(destination))
    result = destination.read_text(encoding='utf-8')
    assert '00:00:00,000 --> 00:00:01,500' in result
    assert '00:00:02,000 --> 00:00:03,000' in result
    assert '中:一番目' in result
    assert '中:二番目' in result
    source_cues = subtitles.parse_srt(source.read_text(encoding='utf-8'))
    result_cues = subtitles.parse_srt(result)
    assert len(result_cues) == len(source_cues)
    assert [cue.index for cue in result_cues] == [
        cue.index for cue in source_cues]
    assert [cue.timing for cue in result_cues] == [
        cue.timing for cue in source_cues]


def test_translate_srt_accepts_bom_marked_utf16_source(
        monkeypatch, tmp_path):
    source = tmp_path / 'video.ja.srt'
    destination = tmp_path / 'video.zh-TW.srt'
    source.write_text(_sample_srt('Japanese source'), encoding='utf-16')
    monkeypatch.setattr(
        subtitles, 'translate_cues',
        lambda texts, *_args, **_kwargs:
            [f'Translated {text}' for text in texts])

    subtitles.translate_srt_to_zh_tw(str(source), str(destination))

    assert subtitles._existing(str(source))
    cues = subtitles.parse_srt(destination.read_text(encoding='utf-8'))
    assert [cue.text for cue in cues] == ['Translated Japanese source']


def test_translate_srt_rejects_empty_source_without_creating_sidecar(tmp_path):
    source = tmp_path / 'video.ja.srt'
    destination = tmp_path / 'video.zh-TW.srt'
    source.write_text('', encoding='utf-8')

    with pytest.raises(
            subtitles.SubtitleError,
            match='does not contain any cues'):
        subtitles.translate_srt_to_zh_tw(str(source), str(destination))

    assert not destination.exists()


def test_generate_all_creates_three_selectable_sidecars(monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    stages = []
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')

    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda _cb, _cancel: ('whisper.exe', 'model.bin', 'vad.bin'))

    def fake_extract(_video, wav, _log, _cancel):
        open(wav, 'wb').close()

    def fake_whisper(
            _exe, _model, _vad, _wav, output_base, _log, _cancel,
            progress_callback=None):
        output = output_base + '.srt'
        with open(output, 'w', encoding='utf-8') as handle:
            handle.write(_sample_srt('こんにちは'))
        return output

    def fake_translate(source, destination, target, stage,
                       progress_callback=None, cancel_check=None,
                       source_language='ja'):
        source_text = open(source, encoding='utf-8').read()
        if source_language == 'ja':
            assert 'こんにちは' in source_text
        else:
            assert source_language == 'en'
            assert 'hello' in source_text
        with open(destination, 'w', encoding='utf-8') as handle:
            handle.write(_sample_srt('hello' if target == 'en' else '你好'))
        if progress_callback:
            progress_callback(stage, 100)
        return destination

    monkeypatch.setattr(subtitles, '_extract_audio', fake_extract)
    monkeypatch.setattr(subtitles, '_run_whisper', fake_whisper)
    monkeypatch.setattr(subtitles, 'translate_srt', fake_translate)
    monkeypatch.setattr(
        subtitles, 'translate_srt_to_zh_tw',
        lambda source, destination, progress_callback=None, cancel_check=None,
        source_language='ja':
            fake_translate(source, destination, 'zh-TW', 'translate_zh',
                           progress_callback, cancel_check, source_language))

    result = subtitles.generate_subtitles(
        str(video), 'all', progress_callback=lambda stage, pct: stages.append((stage, pct)))

    assert [os.path.basename(path) for path in result.files] == [
        'movie.ja.srt', 'movie.en.srt', 'movie.zh-TW.srt']
    assert all(os.path.isfile(path) for path in result.files)
    assert ('transcribe_ja', None) in stages
    assert ('translate_en', None) in stages
    assert ('translate_zh', 100) in stages
    assert stages[-1] == ('done', 100)


def test_chinese_failure_does_not_leave_unrequested_japanese(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(
        config, 'get_recognition_quality', lambda: 'quality')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda _cb, _cancel: ('whisper.exe', 'model.bin', 'vad.bin'))
    monkeypatch.setattr(subtitles, '_extract_audio', lambda _v, wav, _l, _c: open(wav, 'wb').close())

    def fake_whisper(
            _exe, _model, _vad, _wav, output_base, _log, _cancel,
            progress_callback=None):
        output = output_base + '.srt'
        with open(output, 'w', encoding='utf-8') as handle:
            handle.write(_sample_srt())
        return output

    monkeypatch.setattr(subtitles, '_run_whisper', fake_whisper)
    monkeypatch.setattr(
        subtitles, 'translate_srt_to_zh_tw',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subtitles.SubtitleError('translation unavailable')))

    with pytest.raises(subtitles.SubtitleError):
        subtitles.generate_subtitles(str(video), 'zh')
    assert not (tmp_path / 'movie.ja.srt').exists()
    assert not (tmp_path / 'movie.zh-TW.srt').exists()


def test_chinese_reuses_existing_english_without_whisper(
        monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    english = tmp_path / 'movie.en.srt'
    english.write_text(_sample_srt('Existing English.'), encoding='utf-8')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: pytest.fail('English SRT should avoid Whisper'))
    calls = []

    def fake_chinese(source, destination, progress_callback=None,
                     cancel_check=None, source_language='ja'):
        calls.append((source, source_language))
        with open(destination, 'w', encoding='utf-8') as handle:
            handle.write(_sample_srt('既有繁中。'))
        return destination

    monkeypatch.setattr(
        subtitles, 'translate_srt_to_zh_tw', fake_chinese)
    result = subtitles.generate_subtitles(str(video), 'zh')

    assert calls == [(str(english), 'en')]
    assert [os.path.basename(path) for path in result.files] == [
        'movie.zh-TW.srt']
    assert not (tmp_path / 'movie.ja.srt').exists()


def test_existing_outputs_skip_runtime(monkeypatch, tmp_path):
    video = tmp_path / 'movie.mp4'
    video.write_bytes(b'video')
    for language in ('ja', 'en', 'zh-TW'):
        (tmp_path / f'movie.{language}.srt').write_text(_sample_srt(), encoding='utf-8')
    monkeypatch.setattr(
        subtitles, '_prepare_runtime',
        lambda *_args: pytest.fail('runtime must not be prepared'))
    result = subtitles.generate_subtitles(str(video), 'all')
    assert len(result.files) == 3
    assert result.generated == ()


@pytest.mark.parametrize(
    'member',
    ['../outside.exe', '/absolute.exe', 'C:/drive.exe',
     'folder/../outside.exe', 'model.bin:stream'])
def test_safe_zip_extraction_rejects_unsafe_paths(tmp_path, member):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(member, b'bad')
    with pytest.raises(subtitles.SubtitleError):
        subtitles._safe_extract_zip(str(archive), str(tmp_path / 'extract'))


def test_safe_zip_extraction_rejects_duplicates_and_links(tmp_path):
    duplicate = tmp_path / 'duplicate.zip'
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(duplicate, 'w') as bundle:
            bundle.writestr('model.bin', b'first')
            bundle.writestr('model.bin', b'second')
    with pytest.raises(subtitles.SubtitleError):
        subtitles._safe_extract_zip(
            str(duplicate), str(tmp_path / 'duplicate-extract'))

    link = tmp_path / 'link.zip'
    link_info = zipfile.ZipInfo('model-link')
    link_info.create_system = 3
    link_info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(link, 'w') as bundle:
        bundle.writestr(link_info, b'model.bin')
    with pytest.raises(subtitles.SubtitleError):
        subtitles._safe_extract_zip(
            str(link), str(tmp_path / 'link-extract'))


def test_safe_zip_extraction_reports_progress_and_honours_cancel(tmp_path):
    archive = tmp_path / 'safe.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('models/model.bin', b'x' * (2 * 1024 * 1024 + 17))

    progress = []
    destination = tmp_path / 'complete'
    subtitles._safe_extract_zip(
        str(archive), str(destination),
        lambda stage, percent: progress.append((stage, percent)),
        progress_stage='translation_model',
        progress_start=70,
        progress_end=85)
    percentages = [percent for _stage, percent in progress]
    assert percentages == sorted(percentages)
    assert percentages[0] == 70
    assert percentages[-1] == 85
    assert (destination / 'models' / 'model.bin').stat().st_size == (
        2 * 1024 * 1024 + 17)

    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 4

    with pytest.raises(subtitles.SubtitleCancelled):
        subtitles._safe_extract_zip(
            str(archive), str(tmp_path / 'cancelled'),
            cancel_check=cancelled)


def test_existing_subtitle_rejects_empty_and_malformed_sidecars(tmp_path):
    sidecar = tmp_path / 'movie.en.srt'
    assert not subtitles._existing(str(sidecar))

    sidecar.write_bytes(b'')
    assert not subtitles._existing(str(sidecar))

    sidecar.write_text('not an srt', encoding='utf-8')
    assert not subtitles._existing(str(sidecar))

    sidecar.write_text(_sample_srt('Valid subtitle.'), encoding='utf-8')
    assert subtitles._existing(str(sidecar))
