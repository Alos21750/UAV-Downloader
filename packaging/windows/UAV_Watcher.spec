# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
exec((Path(SPECPATH) / 'qt_runtime.py').read_text(encoding='utf-8'))
datas, binaries, hiddenimports = qt_runtime(SPECPATH)
a = Analysis([str(Path(SPECPATH).parents[1] / 'src/uav_downloader/entrypoints/watch.py')],
    pathex=[str(Path(SPECPATH).parents[1] / 'src')], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[str(Path(SPECPATH) / 'hooks')], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'customtkinter'], noarchive=False, optimize=0)
exclude_external_icu(a)
pyz = PYZ(a.pure)
portable = '--portable' in sys.argv
options = dict(name='UAV_Watcher', debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, upx_exclude=[], runtime_tmpdir=None, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
    version=str(Path(SPECPATH) / 'UAV_Watcher.version'))
if portable:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **options)
    portable_bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='UAV_Watcher_portable')
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **options)
