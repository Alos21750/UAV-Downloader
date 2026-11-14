# author: ALOS (Alos21750)
#!/usr/bin/env python
# coding: utf-8

import multiprocessing

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

def main(argv=None):
    run_translation_diagnostic_if_requested()
    from uav_downloader.cli.args import av_recommand, get_parser
    from uav_downloader.core import config
    from uav_downloader import sites as M3U8Sites
    parser = get_parser()
    import sys
    supplied = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args() if argv is None else parser.parse_args(supplied)
    url_arg = args.url or (av_recommand() or '' if args.random else '')
    if args.nogui:
        result = M3U8Sites.consoles_main(
            url_arg, args.output, args.max_workers_per_video)
        return 0 if result is None else int(result)
    from uav_downloader.apps.qt_tasks import main as desktop_main
    options = ['--workspace', 'browser', '--update-channel', 'browse',
               '--queue', args.queue or config.queue_csv_path(),
               '--initial-url', url_arg]
    if any(arg == '--output' or arg.startswith('--output=') or arg.startswith('-o') for arg in supplied):
        options += ['--dest', args.output]
    if args.smoke_report:
        options += ['--smoke-report', args.smoke_report]
    return desktop_main(options)



if __name__ == "__main__":
    raise SystemExit(main())
