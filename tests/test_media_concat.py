"""Collection concatenation uses bounded metadata and never clobbers media."""
from concurrent.futures import CancelledError
import os
from pathlib import Path
import struct
import subprocess

import pytest

from uav_downloader.core import media_concat as concat
from uav_downloader.core.storage import StorageWriteError


def box(kind, payload):
    return struct.pack('>I4s', len(payload) + 8, kind) + payload


def desc(kind, payload):
    assert len(payload) < 128
    return bytes([kind, len(payload)]) + payload


def synthetic_mp4(*, duration=1000, scale=1000, width=64, external=False,
                  bitrate=1000, audio=False):
    def header(ticks):
        return b'\0' * 12 + struct.pack('>II', scale, ticks)
    ref = box(b'url ', b'\0\0\0\1' if not external else b'\0\0\0\0file:///secret\0')
    dref = box(b'dinf', box(b'dref', b'\0\0\0\0\0\0\0\1' + ref))
    video = bytearray(78)
    video[6:8] = b'\0\1'
    struct.pack_into('>HH', video, 24, width, 64)
    sample = box(b'avc1', bytes(video) + box(b'avcC', b'\1d\0\x1f\xff') +
                 box(b'btrt', struct.pack('>III', bitrate, bitrate, bitrate)))
    stsd = box(b'stsd', b'\0\0\0\0\0\0\0\1' + sample)
    tkhd = bytearray(84)
    tkhd[3] = 3
    struct.pack_into('>9I', tkhd, 40, 65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824)
    struct.pack_into('>II', tkhd, 76, width << 16, 64 << 16)
    trak = box(b'trak', box(b'tkhd', bytes(tkhd)) + box(b'mdia', box(b'mdhd', header(duration)) +
               box(b'hdlr', b'\0' * 8 + b'vide') +
               box(b'minf', dref + box(b'stbl', stsd))))
    if audio:
        sample = bytearray(28)
        sample[6:8] = b'\0\1'
        struct.pack_into('>HH', sample, 16, 2, 16)
        struct.pack_into('>I', sample, 24, 48000 << 16)
        config = desc(4, b'\x40\x15' + b'\0' * 3 + struct.pack('>II', bitrate, bitrate) + desc(5, b'\x11\x90'))
        esds = box(b'esds', b'\0' * 4 + desc(3, b'\0\1\0' + config + desc(6, b'\2')))
        stsd = box(b'stsd', b'\0\0\0\0\0\0\0\1' + box(b'mp4a', bytes(sample) + esds))
        trak += box(b'trak', box(b'mdia', box(b'mdhd', header(duration)) +
                    box(b'hdlr', b'\0' * 8 + b'soun') +
                    box(b'minf', dref + box(b'stbl', stsd))))
    return box(b'ftyp', b'isom') + box(b'moov', box(b'mvhd', header(duration)) + trak) + box(b'mdat', b'\1')


def test_metadata_allows_different_duration_and_bitrate(tmp_path):
    paths = [tmp_path / 'a.mp4', tmp_path / 'b.mp4']
    paths[0].write_bytes(synthetic_mp4(audio=True))
    paths[1].write_bytes(synthetic_mp4(duration=2000, bitrate=50000, audio=True))
    first, second = map(concat.inspect_mp4, paths)
    assert first.duration == 1 and second.duration == 2
    assert first.streams == second.streams


@pytest.mark.parametrize('data', [
    b'', b'not mp4', struct.pack('>I4s', 0xFFFFFFFF, b'moov'),
    box(b'moof', b'fake'), box(b'mdat', b'fake'),
    synthetic_mp4(external=True), synthetic_mp4(duration=0),
    synthetic_mp4(scale=0), synthetic_mp4()[:-1],
    synthetic_mp4().replace(b'avc1', b'encv'),
])
def test_invalid_external_encrypted_or_fragmented_media_rejected(tmp_path, data):
    path = tmp_path / 'bad.mp4'
    path.write_bytes(data)
    with pytest.raises(concat.MediaConcatError):
        concat.inspect_mp4(path)


def test_metadata_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(concat, '_MAX_MOOV', 10)
    path = tmp_path / 'large.mp4'
    path.write_bytes(synthetic_mp4())
    with pytest.raises(concat.MediaConcatError, match='too large'):
        concat.inspect_mp4(path)


def test_different_rotation_or_display_matrix_is_not_silently_joined(tmp_path):
    first, second = tmp_path / 'a.mp4', tmp_path / 'b.mp4'
    original = synthetic_mp4()
    changed = bytearray(original)
    matrix = changed.index(b'tkhd') + 4 + 40
    struct.pack_into('>I', changed, matrix, 0)
    first.write_bytes(original)
    second.write_bytes(changed)
    with pytest.raises(concat.MediaConcatError, match='differ'):
        concat.concat_mp4([first, second], tmp_path / 'out.mp4', ffmpeg='unused')


def test_inputs_validate_before_ffmpeg_and_do_not_overwrite(tmp_path, monkeypatch):
    first, second, output = (tmp_path / name for name in ('a.mp4', 'b.mp4', 'out.mp4'))
    first.write_bytes(synthetic_mp4())
    second.write_bytes(synthetic_mp4(width=32))
    monkeypatch.setattr(concat, '_run_ffmpeg', lambda *_a, **_k: pytest.fail('must not invoke FFmpeg'))
    with pytest.raises(concat.MediaConcatError, match='differ'):
        concat.concat_mp4([first, second], output, ffmpeg='unused')
    output.write_bytes(b'user output')
    with pytest.raises(concat.MediaConcatError, match='already exists'):
        concat.concat_mp4([first], output, ffmpeg='unused')
    assert output.read_bytes() == b'user output'


def test_duplicate_and_empty_inputs_rejected(tmp_path):
    path = tmp_path / 'a.mp4'
    for inputs in ([], [path, path]):
        with pytest.raises(concat.MediaConcatError, match='distinct'):
            concat.concat_mp4(inputs, tmp_path / 'out.mp4', ffmpeg='unused')


def test_cancel_before_io(tmp_path):
    with pytest.raises(CancelledError):
        concat.concat_mp4([tmp_path / 'missing.mp4'], tmp_path / 'out.mp4',
                          ffmpeg='unused', cancel_check=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_missing_input_is_storage_failure(tmp_path):
    with pytest.raises(StorageWriteError):
        concat.inspect_mp4(tmp_path / 'missing.mp4')


def test_failed_ffmpeg_cleans_only_owned_temporary_files(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    original = synthetic_mp4()
    source.write_bytes(original)
    marker = tmp_path / 'keep.txt'
    marker.write_text('user')
    def fail(command, **kwargs):
        Path(command[-1]).write_bytes(b'partial')
        raise concat.MediaConcatError('failed')
    monkeypatch.setattr(concat, '_run_ffmpeg', fail)
    with pytest.raises(concat.MediaConcatError):
        concat.concat_mp4([source], tmp_path / 'out.mp4', ffmpeg='unused')
    assert source.read_bytes() == original
    assert sorted(item.name for item in tmp_path.iterdir()) == ['keep.txt', 'source.mp4']


def test_duration_and_decoder_config_validated_after_merge(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(synthetic_mp4())
    for data in (synthetic_mp4(duration=6000), synthetic_mp4(width=32)):
        monkeypatch.setattr(concat, '_run_ffmpeg', lambda command, **kwargs: Path(command[-1]).write_bytes(data))
        with pytest.raises(concat.MediaConcatError, match='Merged MP4'):
            concat.concat_mp4([source], tmp_path / 'out.mp4', ffmpeg='unused')
        assert not (tmp_path / 'out.mp4').exists()


def test_generated_ascii_names_no_path_injection(tmp_path, monkeypatch):
    source = tmp_path / "中文 ' part.mp4"
    source.write_bytes(synthetic_mp4())
    def fake(command, **kwargs):
        listing = Path(command[command.index('-i') + 1])
        assert listing.read_text() == "ffconcat version 1.0\nfile 'part-0000.mp4'\n"
        assert command[command.index('-protocol_whitelist') + 1] == 'file'
        assert '-n' in command and '-c' in command
        Path(command[-1]).write_bytes(source.read_bytes())
    monkeypatch.setattr(concat, '_run_ffmpeg', fake)
    output = tmp_path / '合集.mp4'
    concat.concat_mp4([source], output, ffmpeg='unused')
    assert output.read_bytes() == source.read_bytes()


def test_cancel_during_ffmpeg_terminates_and_redacts_diagnostics(monkeypatch):
    import io
    from types import SimpleNamespace
    calls = []
    process = SimpleNamespace(
        stderr=io.BytesIO(b'https://example.test/?secret=do-not-log'),
        returncode=None)
    process.poll = lambda: process.returncode
    process.terminate = lambda: (calls.append('terminate'), setattr(process, 'returncode', -15))
    process.wait = lambda timeout: process.returncode
    process.kill = lambda: calls.append('kill')
    monkeypatch.setattr(concat.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(CancelledError, match='Collection cancelled'):
        concat._run_ffmpeg(['ffmpeg'], cancel_check=lambda: True, timeout=10)
    assert calls == ['terminate']


def test_ffmpeg_error_message_does_not_contain_source_metadata(monkeypatch):
    import io
    from types import SimpleNamespace
    process = SimpleNamespace(stderr=io.BytesIO(b'URL https://x/?token=secret'), returncode=1,
                              poll=lambda: 1)
    monkeypatch.setattr(concat.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(concat.MediaConcatError) as error:
        concat._run_ffmpeg(['ffmpeg'], cancel_check=None, timeout=10)
    assert 'secret' not in str(error.value) and 'https' not in str(error.value)


def test_ffmpeg_output_access_failure_is_storage_error(monkeypatch):
    import io
    from types import SimpleNamespace
    process = SimpleNamespace(stderr=io.BytesIO(b'Error opening output file private.mp4: Permission denied'),
                              returncode=1, poll=lambda: 1)
    monkeypatch.setattr(concat.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(StorageWriteError) as error:
        concat._run_ffmpeg(['ffmpeg'], cancel_check=None, timeout=10)
    assert 'private.mp4' not in str(error.value)


def test_copy_fallback_supports_non_hardlink_filesystem(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(synthetic_mp4())
    monkeypatch.setattr(concat.os, 'link', lambda *a: (_ for _ in ()).throw(OSError('no hardlinks')))
    monkeypatch.setattr(concat, '_run_ffmpeg', lambda command, **kwargs: Path(command[-1]).write_bytes(source.read_bytes()))
    output = tmp_path / 'out.mp4'
    concat.concat_mp4([source], output, ffmpeg='unused')
    assert output.read_bytes() == source.read_bytes()
    assert sorted(item.name for item in tmp_path.iterdir()) == ['out.mp4', 'source.mp4']


def test_cancellation_at_publication_removes_only_new_staging_output(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(synthetic_mp4())
    monkeypatch.setattr(concat, '_run_ffmpeg', lambda command, **kwargs: Path(command[-1]).write_bytes(source.read_bytes()))
    output = tmp_path / 'out.mp4'
    with pytest.raises(CancelledError):
        concat.concat_mp4([source], output, ffmpeg='unused', cancel_check=output.exists)
    assert not output.exists() and source.exists()


def test_output_created_by_someone_else_during_merge_is_preserved(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(synthetic_mp4())
    output = tmp_path / 'out.mp4'
    def fake(command, **kwargs):
        Path(command[-1]).write_bytes(source.read_bytes())
        output.write_bytes(b'not ours')
    monkeypatch.setattr(concat, '_run_ffmpeg', fake)
    with pytest.raises(concat.MediaConcatError, match='already exists'):
        concat.concat_mp4([source], output, ffmpeg='unused')
    assert output.read_bytes() == b'not ours'


def test_input_changed_after_inspection_cannot_be_published(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(synthetic_mp4())
    def fake(command, **kwargs):
        Path(command[-1]).write_bytes(source.read_bytes())
        source.write_bytes(synthetic_mp4() + box(b'free', b'changed'))
    monkeypatch.setattr(concat, '_run_ffmpeg', fake)
    with pytest.raises(concat.MediaConcatError, match='changed'):
        concat.concat_mp4([source], tmp_path / 'out.mp4', ffmpeg='unused')
    assert not (tmp_path / 'out.mp4').exists()


@pytest.fixture(scope='module')
def media_tools():
    imageio = pytest.importorskip('imageio_ffmpeg')
    oracle = Path(imageio.get_ffmpeg_exe())
    if not oracle.is_file():
        pytest.skip('Synthetic media encoder unavailable')
    bundle = os.environ.get('UAV_FFMPEG_BUNDLE')
    runtime = Path(bundle) / 'ffmpeg-win-x86_64-v7.1.exe' if bundle else oracle
    return oracle, runtime


def run_media(command):
    result = subprocess.run([str(item) for item in command], capture_output=True,
                            timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr[-1000:]


@pytest.mark.parametrize('codec', ['libx264', 'libx265'])
def test_real_synthetic_ordered_mp4_concat_and_decode(tmp_path, media_tools, codec):
    oracle, runtime = media_tools
    paths = []
    for index, color in enumerate(('red', 'blue')):
        path = tmp_path / f"中文 '{index}.mp4"
        command = [oracle, '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-f', 'lavfi', '-i', f'color=c={color}:s=64x64:r=12',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                   '-t', str(0.8 + index * 0.3), '-c:v', codec, '-threads', '1', '-c:a', 'aac']
        if codec == 'libx265':
            command += ['-x265-params', 'pools=none:frame-threads=1:log-level=error']
        run_media(command + [path])
        paths.append(path)
    output = tmp_path / '合集.mp4'
    concat.concat_mp4(paths, output, ffmpeg=runtime)
    merged = concat.inspect_mp4(output)
    assert merged.duration == pytest.approx(sum(concat.inspect_mp4(p).duration for p in paths), abs=0.1)
    run_media([oracle, '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', output, '-f', 'null', '-'])
    frames = subprocess.run([str(oracle), '-hide_banner', '-loglevel', 'error', '-i', str(output),
                             '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                            capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert frames.returncode == 0
    assert frames.stdout[0] > frames.stdout[2] + 100
    assert frames.stdout[-1] > frames.stdout[-3] + 100
    assert all(path.exists() for path in paths)


def test_real_watcher_collection_cache_append_and_merged_subtitles(tmp_path, media_tools, monkeypatch):
    from types import SimpleNamespace
    from uav_downloader.apps import watch
    from uav_downloader.sites import base
    oracle, runtime = media_tools
    fixture = tmp_path / 'fixture.mp4'
    run_media([oracle, '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=12',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
               '-t', '0.8', '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', fixture])
    rows = [dict(url='https://huangguoai.com/video/123/' + (f'ep-{n}/' if n > 1 else ''),
                 title=f'QA - EP {n}', series_id='123', episode=n) for n in (1, 2)]
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: str(runtime))
    monkeypatch.setattr(watch.SITES['Huangguo']['browser'], 'fetch_episodes',
                        lambda *_args, **_kwargs: list(rows))
    worker = watch.SmallToolWorker(lambda _msg: None)
    downloads, subtitles = [], []
    def create(url, dest, **kwargs):
        assert kwargs['resolution'] == '720'
        path = Path(dest) / 'episode.mp4'
        def download():
            downloads.append(url)
            path.write_bytes(fixture.read_bytes())
            return True
        return SimpleNamespace(is_url_vaildate=lambda: True, start_download=download,
                               _get_video_savename=lambda: str(path))
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', create)
    monkeypatch.setattr(watch, 'generate_subtitles', lambda path, mode, **kwargs:
                        subtitles.append(path) or SimpleNamespace(
                            files=['qa.srt'], no_speech=False, satisfied_by_source=False))
    candidate = watch.group_collection_rows(rows)[0]
    candidate['_collection_resolution'] = '720'
    destination = tmp_path / 'output'
    assert worker._download_one(candidate, str(destination), 'zh') == 'ok'
    output, = destination.glob('*.mp4')
    assert concat.inspect_mp4(output).duration == pytest.approx(2 * concat.inspect_mp4(fixture).duration, abs=0.1)
    assert downloads == [row['url'] for row in rows]
    before = output.stat().st_mtime_ns
    assert worker._download_one(candidate, str(destination), 'none') == 'ok'
    assert output.stat().st_mtime_ns == before and len(downloads) == 2
    rows.append(dict(url='https://huangguoai.com/video/123/ep-3/', title='QA - EP 3',
                     series_id='123', episode=3))
    assert worker._download_one(candidate, str(destination), 'zh') == 'ok'
    assert len(downloads) == 3 and downloads[-1] == rows[-1]['url']
    assert subtitles == [str(output), str(output)]
    assert worker._seen == {}
    assert concat.inspect_mp4(output).duration == pytest.approx(3 * concat.inspect_mp4(fixture).duration, abs=0.15)
    run_media([oracle, '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', output, '-f', 'null', '-'])
    from uav_downloader.subtitles import engine
    sidecar = Path(engine.subtitle_paths(str(output))['zh-TW'])
    def old_subtitle():
        sidecar.write_text('1\n00:00:00,000 --> 00:00:00,500\nQA\n', encoding='utf-8')
        manifest = engine._empty_subtitle_provenance()
        engine._record_asr_track(manifest, 'zh-TW', str(sidecar), 'a' * 64,
                                 engine._media_identity(str(output)))
        engine._save_subtitle_provenance(engine._subtitle_provenance_path(str(output)), manifest)
    old_subtitle()
    rows.append(dict(url='https://huangguoai.com/video/123/ep-4/', title='QA - EP 4', series_id='123', episode=4))
    assert worker._download_one(candidate, str(destination), 'none') == 'ok'
    assert not sidecar.exists()
    old_subtitle()
    rows.append(dict(url='https://huangguoai.com/video/123/ep-5/', title='QA - EP 5', series_id='123', episode=5))
    def failed_subtitles(*_args, **_kwargs):
        raise RuntimeError('synthetic translation failure')
    monkeypatch.setattr(watch, 'generate_subtitles', failed_subtitles)
    assert worker._download_one(candidate, str(destination), 'zh') == 'subtitle_failed'
    assert not sidecar.exists()
    assert len(downloads) == 5 and worker._seen == {}
