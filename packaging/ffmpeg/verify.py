"""Windows validation of the exact FFmpeg payload before freezing."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave

from bundle import SPEC, reviewed_bundle


def run(args, timeout=60):
    result = subprocess.run([str(arg) for arg in args], capture_output=True,
                            text=True, encoding='utf-8', errors='replace',
                            timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('FFmpeg validation command failed: ' + result.stderr[-2000:])
    return result.stdout + result.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = reviewed_bundle()
    binary = root / SPEC['binary_name']
    import pefile
    image = pefile.PE(str(binary))
    try:
        assert image.FILE_HEADER.Machine == 0x8664, 'Expected x64 PE'
        imports = sorted(item.dll.decode('ascii').lower()
                         for item in getattr(image, 'DIRECTORY_ENTRY_IMPORT', []))
    finally:
        image.close()
    allowed = {'kernel32.dll', 'msvcrt.dll', 'user32.dll', 'advapi32.dll',
               'bcrypt.dll', 'ole32.dll', 'shell32.dll', 'secur32.dll',
               'ws2_32.dll', 'psapi.dll', 'ntdll.dll', 'gdi32.dll',
               'crypt32.dll', 'winmm.dll', 'shlwapi.dll'}
    assert all(name in allowed or name.startswith(('api-ms-win-', 'ext-ms-win-'))
               for name in imports), 'Unexpected non-system DLL dependency'
    provenance = json.loads((root / 'FFmpeg-provenance.json').read_text(encoding='utf-8'))
    assert imports == provenance['dll_imports'], 'Import table provenance mismatch'
    license_text = run([binary, '-L'])
    assert 'GNU Lesser General Public' in license_text
    assert 'GNU General Public License' not in license_text
    config = run([binary, '-buildconf'])
    assert 'uav-lgpl1' in config
    for flag in ('--enable-gpl', '--enable-version3', '--enable-nonfree'):
        assert flag not in config
    for flag in ('--disable-autodetect', '--disable-network', '--extra-ldflags=-static'):
        assert flag in config
    # This is only an independent synthetic-fixture generator. Its binary is
    # never copied into a release by the specs.
    import imageio_ffmpeg
    oracle = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    assert oracle != binary.resolve()
    cases = []
    with tempfile.TemporaryDirectory(prefix='uav-ffmpeg-') as temporary:
        folder = Path(temporary) / '測試 media'
        folder.mkdir()
        for codec, audio, suffix in [('libx264', 'aac', '.ts'),
                                     ('libx265', 'aac', '.mkv'),
                                     ('libvpx-vp9', 'libopus', '.webm')]:
            source = folder / (codec + suffix)
            command = [oracle, '-hide_banner', '-loglevel', 'error', '-y',
                       '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=12',
                       '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                       '-t', '1', '-c:v', codec, '-threads', '1', '-c:a', audio]
            if codec == 'libx265':
                command += ['-x265-params', 'pools=none:frame-threads=1:log-level=error']
            run(command + [source])
            if suffix != '.webm':
                remux = source.with_suffix('.mp4')
                run([binary, '-y', '-hide_banner', '-loglevel', 'error',
                     '-fflags', '+genpts', '-i', source, '-c', 'copy',
                     '-movflags', '+faststart', '-avoid_negative_ts', 'make_zero', remux])
                data = remux.read_bytes()
                assert 0 < data.index(b'moov') < data.index(b'mdat')
                run([oracle, '-hide_banner', '-loglevel', 'error', '-i', remux,
                     '-f', 'null', '-'])
                cases.append(codec + '-remux-faststart-decode')
            wav = source.with_suffix('.wav')
            run([binary, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                 '-i', source, '-vn', '-ac', '1', '-ar', '16000',
                 '-c:a', 'pcm_s16le', wav])
            with wave.open(str(wav), 'rb') as stream:
                assert (stream.getnchannels(), stream.getframerate(),
                        stream.getsampwidth()) == (1, 16000, 2)
                assert stream.getnframes() >= 14000
            cases.append(codec + '-' + audio + '-pcm16k-unicode')
    report = {'passed': True, 'binary_sha256': provenance['binary_sha256'],
              'source_sha256': SPEC['sha256'], 'dll_imports': imports,
              'checks': ['license', 'configuration', 'source-provenance', *cases]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
