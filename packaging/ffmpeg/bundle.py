"""Fail-closed PyInstaller integration for the reviewed FFmpeg build."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / 'source.json').read_text(encoding='utf-8'))
NOTICES = ('FFmpeg-LGPL-2.1.txt', 'FFmpeg-NOTICE.txt', 'FFmpeg-provenance.json')


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def reviewed_bundle():
    raw = os.environ.get('UAV_FFMPEG_BUNDLE', '')
    if not raw:
        raise RuntimeError('Set UAV_FFMPEG_BUNDLE to a verified CI FFmpeg bundle')
    root = Path(raw).resolve(strict=True)
    manifest = json.loads((root / 'FFmpeg-provenance.json').read_text(encoding='utf-8'))
    binary = root / SPEC['binary_name']
    if (manifest.get('source_sha256') != SPEC['sha256']
            or manifest.get('binary_name') != SPEC['binary_name']
            or manifest.get('license') != 'LGPL-2.1-or-later'
            or manifest.get('binary_sha256') != sha(binary)
            or manifest.get('build_recipe_sha256') != sha(HERE / 'build.py')):
        raise RuntimeError('FFmpeg provenance mismatch')
    for name in NOTICES:
        if not (root / name).is_file():
            raise RuntimeError('Missing FFmpeg notice')
    with tarfile.open(root / SPEC['source_asset']) as source:
        prefix = 'UAV_FFmpeg_7.1_Corresponding_Source/'
        for name in ('ffmpeg-7.1.tar.xz', 'build.py', 'source.json', 'README.md',
                     'build-packages.txt', *NOTICES):
            member = source.getmember(prefix + name)
            if not member.isfile():
                raise RuntimeError('Invalid corresponding-source member')
        with source.extractfile(prefix + 'ffmpeg-7.1.tar.xz') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != SPEC['sha256']:
                raise RuntimeError('Corresponding source hash mismatch')
        with source.extractfile(prefix + 'FFmpeg-provenance.json') as handle:
            if json.load(handle) != manifest:
                raise RuntimeError('Source/binary provenance mapping mismatch')
        with source.extractfile(prefix + 'source.json') as handle:
            if json.load(handle) != SPEC:
                raise RuntimeError('Corresponding source specification mismatch')
        with source.extractfile(prefix + 'build.py') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != manifest['build_recipe_sha256']:
                raise RuntimeError('Corresponding build recipe mismatch')
    if importlib.metadata.version('imageio-ffmpeg') != '0.6.0':
        raise RuntimeError('Unreviewed imageio-ffmpeg wrapper version')
    from imageio_ffmpeg._definitions import FNAME_PER_PLATFORM
    if FNAME_PER_PLATFORM.get('windows-x86_64') != SPEC['binary_name']:
        raise RuntimeError('FFmpeg wrapper filename changed')
    return root


def strip_ffmpeg(entries):
    def is_payload(value):
        name = str(value).replace('\\', '/').rsplit('/', 1)[-1].lower()
        return name.startswith('ffmpeg') and name.endswith('.exe')
    return [entry for entry in entries if not any(is_payload(x) for x in entry[:2])]


def prepare_inputs(datas, binaries, root):
    datas = strip_ffmpeg(datas)
    binaries = strip_ffmpeg(binaries)
    datas.extend((str(root / name), 'third_party_licenses') for name in NOTICES)
    binaries.append((str(root / SPEC['binary_name']), 'imageio_ffmpeg/binaries'))
    return datas, binaries


def finalize_analysis(analysis, root):
    # imageio's hook may recollect its original binary during Analysis.
    analysis.datas = strip_ffmpeg(analysis.datas)
    analysis.binaries = strip_ffmpeg(analysis.binaries)
    analysis.binaries.append(('imageio_ffmpeg/binaries/' + SPEC['binary_name'],
                              str(root / SPEC['binary_name']), 'BINARY'))
