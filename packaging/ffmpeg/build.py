"""Build a separate LGPL FFmpeg executable and its corresponding source bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / 'source.json').read_text(encoding='utf-8'))
FLAGS = [
    '--enable-cross-compile', '--target-os=mingw32', '--arch=x86_64',
    '--cross-prefix=x86_64-w64-mingw32-', '--cc=x86_64-w64-mingw32-gcc-win32',
    '--extra-ldflags=-static', '--disable-autodetect', '--disable-gpl',
    '--disable-version3', '--disable-nonfree', '--disable-shared', '--enable-static',
    '--disable-pthreads', '--enable-w32threads', '--disable-x86asm',
    '--disable-ffplay', '--disable-ffprobe', '--disable-doc', '--disable-debug',
    '--disable-avdevice', '--disable-devices', '--disable-network',
    '--disable-hwaccels', '--disable-encoders', '--enable-encoder=pcm_s16le',
    '--extra-version=uav-lgpl1',
]


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work-dir', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--source-archive', type=Path)
    args = parser.parse_args()
    work, output = args.work_dir.resolve(), args.out_dir.resolve()
    # Never recursively clean a user-specified directory.
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()) or any(output.iterdir()):
        raise RuntimeError('Build and output directories must be empty')
    archive = work / 'ffmpeg-7.1.tar.xz'
    if args.source_archive:
        shutil.copyfile(args.source_archive, archive)
    else:
        with urllib.request.urlopen(SPEC['url'], timeout=60) as response, archive.open('wb') as target:
            shutil.copyfileobj(response, target)
    if sha(archive) != SPEC['sha256']:
        raise RuntimeError('Official FFmpeg source hash mismatch')
    with tarfile.open(archive) as source:
        source.extractall(work, filter='data')
    tree = work / 'ffmpeg-7.1'
    subprocess.run(['sh', './configure', *FLAGS], cwd=tree, check=True)
    subprocess.run(['make', '-j', str(min(os.cpu_count() or 2, 8)), 'ffmpeg.exe'],
                   cwd=tree, check=True)
    binary = output / SPEC['binary_name']
    shutil.copyfile(tree / 'ffmpeg.exe', binary)
    compiler = subprocess.check_output(
        ['x86_64-w64-mingw32-gcc-win32', '--version'], text=True)
    imports = subprocess.check_output(
        ['x86_64-w64-mingw32-objdump', '-p', str(binary)], text=True)
    dlls = sorted(line.split('DLL Name:', 1)[1].strip().lower()
                  for line in imports.splitlines() if 'DLL Name:' in line)
    if any(name.startswith(('libgcc', 'libstdc++', 'libwinpthread')) for name in dlls):
        raise RuntimeError('Unexpected non-system MinGW runtime dependency')
    packages = subprocess.check_output(['dpkg-query', '-W'], text=True)
    provenance = {
        'schema': 1, 'ffmpeg_version': SPEC['version'], 'license': 'LGPL-2.1-or-later',
        'source_url': SPEC['url'], 'source_sha256': SPEC['sha256'],
        'source_asset': SPEC['source_asset'], 'binary_name': SPEC['binary_name'],
        'binary_sha256': sha(binary), 'configure': FLAGS, 'compiler': compiler,
        'dll_imports': dlls, 'build_recipe_sha256': sha(HERE / 'build.py'),
    }
    (output / 'FFmpeg-provenance.json').write_text(
        json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(tree / 'COPYING.LGPLv2.1', output / 'FFmpeg-LGPL-2.1.txt')
    notice = (
        'FFmpeg 7.1, Copyright (c) the FFmpeg developers.\n'
        'Separately licensed under LGPL-2.1-or-later; not under UAV project terms.\n'
        'Built without GPL, version3, nonfree, or external auto-detected libraries.\n'
        'UAV uses this executable as a separate process; users may replace it.\n'
        'Complete corresponding source and build recipe: ' + SPEC['source_asset'] + '\n'
        'Available beside the application in the same release at\n'
        'https://github.com/Alos21750/UAV-Downloader/releases\n'
        'See FFmpeg-provenance.json for exact source and binary hashes.\n'
    )
    (output / 'FFmpeg-NOTICE.txt').write_text(notice, encoding='utf-8')
    corresponding = work / 'UAV_FFmpeg_7.1_Corresponding_Source'
    corresponding.mkdir()
    shutil.copyfile(archive, corresponding / archive.name)
    for name in ('build.py', 'source.json', 'README.md'):
        shutil.copyfile(HERE / name, corresponding / name)
    for name in ('FFmpeg-LGPL-2.1.txt', 'FFmpeg-NOTICE.txt', 'FFmpeg-provenance.json'):
        shutil.copyfile(output / name, corresponding / name)
    (corresponding / 'build-packages.txt').write_text(packages, encoding='utf-8')
    # No source patching occurs: the exact original tarball plus this recipe
    # contains all FFmpeg sources/build inputs; no external codec libraries.
    with tarfile.open(output / SPEC['source_asset'], 'w:xz') as package:
        package.add(corresponding, arcname=corresponding.name)
    print(json.dumps({'binary_sha256': sha(binary),
                      'source_asset_sha256': sha(output / SPEC['source_asset']),
                      'dll_imports': dlls}))


if __name__ == '__main__':
    main()
