# author: ALOS (Alos21750)
#!/usr/bin/env python
# coding: utf-8

import ctypes
import multiprocessing
import sys

if __name__ == '__main__':
    # PyInstaller replaces this with an early child-process dispatcher.  It
    # must run before SSL, crash logging, Tk, or crawler imports so the local
    # translation worker never starts a second GUI.
    multiprocessing.freeze_support()

from uav_downloader.core.bootstrap import (
    install_runtime_guards,
    run_translation_diagnostic_if_requested,
)

install_runtime_guards()

if __name__ == '__main__':
    run_translation_diagnostic_if_requested()

# Enable DPI awareness BEFORE any Tk/GUI imports
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-monitor V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from uav_downloader.cli.args import av_recommand, get_parser
from uav_downloader import sites as M3U8Sites

# Use modern CustomTkinter GUI by default; fall back to basic tkinter if unavailable
try:
    from uav_downloader.apps.browse import gui_modern_main as _gui_main
    _USE_MODERN = True
except ImportError:
    from uav_downloader.legacy.gui import gui_main as _gui_main
    _USE_MODERN = False

def main():
    url_arg = ""
    parser = get_parser()
    args = parser.parse_args()

    if len(args.url) != 0:
        url_arg = args.url
    elif args.random:
        url_arg = av_recommand() or ""   # None (site changed/blocked) -> empty, not a crash

    if args.nogui:
        result = M3U8Sites.consoles_main(
            url_arg, args.output, args.max_workers_per_video)
        return 0 if result is None else int(result)
    elif _USE_MODERN:
        _gui_main(url_arg, args.output)
    else:
        from uav_downloader.legacy.gui import gui_main
        gui_main(url_arg, args.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
