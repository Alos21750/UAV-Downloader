# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata
sys.path.insert(0, str(Path(SPECPATH).parent / 'ffmpeg'))
from bundle import reviewed_bundle, prepare_inputs, finalize_analysis
ffmpeg_bundle = reviewed_bundle()

datas = []
binaries = []
hiddenimports = [
    'cloudscraper', 'Crypto.Cipher.AES', 'm3u8',
    'imageio_ffmpeg', 'imageio_ffmpeg.binaries',
    'curl_cffi', '_cffi_backend', 'certifi', 'faulthandler',
    'uav_downloader.core.crashlog', 'uav_downloader.core.ssl',
    'uav_downloader.subtitles.engine', 'uav_downloader.subtitles.domain',
    'uav_downloader.subtitles.llm', 'uav_downloader.subtitles.settings',
    'uav_downloader.ui.translation_settings',
    'ctranslate2', 'ctranslate2._ext',
    'numpy._core._exceptions',
    'sentencepiece', 'sentencepiece._sentencepiece', 'opencc',
    'socks', 'urllib3.contrib.socks',
]

for pkg in [
        'cloudscraper', 'certifi', 'curl_cffi', 'imageio_ffmpeg',
        'ctranslate2', 'sentencepiece', 'opencc']:
    tmp = collect_all(pkg)
    datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]

datas = [
    entry for entry in datas
    if Path(str(entry[0])).name.lower() != 'cudnn64_9.dll'
]
binaries = [
    entry for entry in binaries
    if Path(str(entry[0])).name.lower() != 'cudnn64_9.dll'
]
datas += copy_metadata('numpy')
datas += copy_metadata('PyYAML')
datas += copy_metadata('imageio-ffmpeg')

datas += [
    ('..\\..\\LICENSE', '.'),
    ('..\\..\\THIRD_PARTY_NOTICES.md', '.'),
    (
        '..\\..\\third_party_licenses\\FuguMT-CC-BY-SA-4.0-NOTICE.txt',
        'third_party_licenses',
    ),
    (
        '..\\..\\third_party_licenses\\Intel-Simplified-Software-License.txt',
        'third_party_licenses',
    ),
]


datas, binaries = prepare_inputs(datas, binaries, ffmpeg_bundle)
a = Analysis(
    ['..\\..\\src\\uav_downloader\\entrypoints\\watch.py'],
    pathex=['..\\..\\src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
finalize_analysis(a, ffmpeg_bundle)
pyz = PYZ(a.pure)

# PyInstaller forwards arguments after ``--`` to the spec.  Building the two
# layouts in separate invocations keeps their PKG/work files isolated while
# preserving the canonical UAV_Watcher.exe name and version metadata.
portable = '--portable' in sys.argv

if portable:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='UAV_Watcher',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        version='UAV_Watcher.version',
    )
    portable_bundle = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='UAV_Watcher_portable',
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='UAV_Watcher',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # Do not allow a runner-local UPX installation to change the release
        # binary or increase the antivirus heuristic surface.
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        version='UAV_Watcher.version',
    )
