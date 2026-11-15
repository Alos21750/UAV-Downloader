# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata
sys.path.insert(0, str(Path(SPECPATH).parent / 'ffmpeg'))
from bundle import reviewed_bundle, prepare_inputs, finalize_analysis
ffmpeg_bundle = reviewed_bundle()

datas = []
binaries = []
hiddenimports = [
    'cloudscraper', 'Crypto.Cipher.AES', 'm3u8',
    'imageio_ffmpeg', 'imageio_ffmpeg.binaries',
    'customtkinter', 'curl_cffi', '_cffi_backend',
    'uav_downloader.core.crashlog', 'certifi', 'faulthandler',
    'uav_downloader.core.updater', 'uav_downloader.core.ssl',
    'uav_downloader.subtitles.engine', 'uav_downloader.subtitles.domain',
    'uav_downloader.subtitles.llm', 'uav_downloader.subtitles.settings',
    'uav_downloader.ui.translation_settings',
    'ctranslate2', 'ctranslate2._ext',
    'numpy._core._exceptions',
    'sentencepiece', 'sentencepiece._sentencepiece', 'opencc',
    'socks', 'urllib3.contrib.socks',
]

# Collect package data (cloudscraper browser profiles, certifi certs, customtkinter themes)
for pkg in [
        'cloudscraper', 'certifi', 'customtkinter', 'curl_cffi',
        'imageio_ffmpeg', 'ctranslate2', 'sentencepiece', 'opencc']:
    tmp = collect_all(pkg)
    datas += tmp[0]; binaries += tmp[1]; hiddenimports += tmp[2]

# The Windows CTranslate2 wheel ships an optional cuDNN DLL even though this
# application is CPU-only.  ctranslate2 imports every package DLL on Windows,
# so remove that unused proprietary GPU runtime from both possible TOCs.
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
    ['..\\..\\src\\uav_downloader\\entrypoints\\browse.py'],
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='UAV_Browser',
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
    version='UAV_Browser.version',
)
