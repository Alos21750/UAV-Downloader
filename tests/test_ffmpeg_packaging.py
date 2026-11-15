"""Fail-closed payload replacement and corresponding-source contracts."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'uav_ffmpeg_packaging', ROOT / 'packaging/ffmpeg/bundle.py')
bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle)


def test_old_binary_removed_before_and_after_analysis():
    old = r'C:\packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe'
    new = Path('C:/reviewed')
    datas, binaries = bundle.prepare_inputs(
        [(old, 'imageio_ffmpeg/binaries'), ('license.txt', '.')],
        [(old, 'imageio_ffmpeg/binaries'), ('other.dll', '.')], new)
    assert not any(old in entry for entry in datas + binaries)
    assert ('license.txt', '.') in datas
    assert ('other.dll', '.') in binaries
    analysis = SimpleNamespace(
        datas=[('imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe', old, 'DATA')],
        binaries=[('ffmpeg.exe', old, 'BINARY'), ('other.dll', 'other.dll', 'BINARY')])
    bundle.finalize_analysis(analysis, new)
    assert analysis.datas == []
    assert len(analysis.binaries) == 2
    assert analysis.binaries[-1][1] == str(new / bundle.SPEC['binary_name'])


def test_missing_bundle_is_not_silently_replaced_by_upstream(monkeypatch):
    monkeypatch.delenv('UAV_FFMPEG_BUNDLE', raising=False)
    with pytest.raises(RuntimeError, match='UAV_FFMPEG_BUNDLE'):
        bundle.reviewed_bundle()


@pytest.fixture
def fake_bundle(tmp_path, monkeypatch):
    source = b'exact source fixture'
    binary = b'MZ binary fixture'
    spec = dict(bundle.SPEC, sha256=hashlib.sha256(source).hexdigest())
    monkeypatch.setattr(bundle, 'SPEC', spec)
    monkeypatch.setenv('UAV_FFMPEG_BUNDLE', str(tmp_path))
    monkeypatch.setattr(bundle.importlib.metadata, 'version', lambda _name: '0.6.0')
    (tmp_path / spec['binary_name']).write_bytes(binary)
    manifest = {
        'source_sha256': spec['sha256'], 'binary_name': spec['binary_name'],
        'license': 'LGPL-2.1-or-later', 'binary_sha256': hashlib.sha256(binary).hexdigest(),
        'build_recipe_sha256': bundle.sha(bundle.HERE / 'build.py'),
    }
    (tmp_path / 'FFmpeg-provenance.json').write_text(json.dumps(manifest), encoding='utf-8')
    for name in ('FFmpeg-LGPL-2.1.txt', 'FFmpeg-NOTICE.txt'):
        (tmp_path / name).write_text('Fixture notice', encoding='utf-8')
    contents = {'ffmpeg-7.1.tar.xz': source,
                'build.py': (bundle.HERE / 'build.py').read_bytes(),
                'source.json': json.dumps(spec).encode(), 'README.md': b'rebuild',
                'build-packages.txt': b'compiler',
                **{name: (tmp_path / name).read_bytes() for name in bundle.NOTICES}}
    with tarfile.open(tmp_path / spec['source_asset'], 'w:xz') as archive:
        for name, data in contents.items():
            entry = tarfile.TarInfo('UAV_FFmpeg_7.1_Corresponding_Source/' + name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return tmp_path, spec


def test_binary_and_source_map_to_same_recipe(fake_bundle):
    root, _spec = fake_bundle
    assert bundle.reviewed_bundle() == root


@pytest.mark.parametrize('change', ['binary', 'source-missing', 'recipe', 'wrapper'])
def test_mismatched_build_is_rejected(fake_bundle, monkeypatch, change):
    root, spec = fake_bundle
    if change == 'binary':
        (root / spec['binary_name']).write_bytes(b'MZ different binary')
    elif change == 'source-missing':
        (root / spec['source_asset']).unlink()
    elif change == 'wrapper':
        monkeypatch.setattr(bundle.importlib.metadata, 'version', lambda _name: '99.0')
    else:
        path = root / 'FFmpeg-provenance.json'
        value = json.loads(path.read_text())
        value['build_recipe_sha256'] = '0' * 64
        path.write_text(json.dumps(value))
    with pytest.raises((RuntimeError, FileNotFoundError)):
        bundle.reviewed_bundle()


def test_both_specs_and_workflow_require_source_bundle():
    for app in ('Browser', 'Watcher'):
        text = (ROOT / f'packaging/windows/UAV_{app}.spec').read_text()
        assert 'reviewed_bundle()' in text
        assert 'prepare_inputs(datas, binaries, ffmpeg_bundle)' in text
        assert 'finalize_analysis(a, ffmpeg_bundle)' in text
        assert "copy_metadata('imageio-ffmpeg')" in text
    workflow = (ROOT / '.github/workflows/windows-build.yml').read_text()
    assert 'needs: ffmpeg' in workflow
    assert 'packaging/ffmpeg/verify.py' in workflow
    assert workflow.count(bundle.SPEC['source_asset']) >= 4
