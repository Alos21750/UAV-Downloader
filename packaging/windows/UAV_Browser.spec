# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
exec((Path(SPECPATH) / 'qt_runtime.py').read_text(encoding='utf-8'))
datas, binaries, hiddenimports = qt_runtime(SPECPATH)
a = Analysis([str(Path(SPECPATH).parents[1] / 'src/uav_downloader/entrypoints/browse.py')],
    pathex=[str(Path(SPECPATH).parents[1] / 'src')], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[str(Path(SPECPATH) / 'hooks')], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'customtkinter'], noarchive=False, optimize=0)
exclude_external_icu(a)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='UAV_Browser', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, upx_exclude=[], runtime_tmpdir=None,
    console=False, disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, version=str(Path(SPECPATH) / 'UAV_Browser.version'))
