"""UAV Watcher entry point.

Runtime guards and machine-consumed diagnostics run before importing Qt or the
site resolver, which is required by the frozen Windows verification workflow.
"""

from __future__ import annotations

import multiprocessing

from uav_downloader.core.bootstrap import (
    install_runtime_guards,
    run_translation_diagnostic_if_requested,
)


if __name__ == "__main__":
    multiprocessing.freeze_support()

install_runtime_guards()


def main(argv=None):
    run_translation_diagnostic_if_requested()
    import argparse
    from pathlib import Path
    from uav_downloader.core.watcher import STATE_DIR
    from uav_downloader.apps.qt_tasks import main as desktop_main
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch-state', type=Path, default=Path(STATE_DIR))
    parser.add_argument('--queue', type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument('--smoke-report', type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    # Watcher's settings commands must not overwrite Browser's download queue.
    options = ['--workspace', 'watcher', '--update-channel', 'watch',
               '--watch-state', str(args.watch_state),
               '--queue', str(args.queue or args.watch_state / 'qt-settings-queue.csv')]
    if args.smoke_report:
        options += ['--smoke-report', str(args.smoke_report)]
    return desktop_main(options)


if __name__ == "__main__":
    raise SystemExit(main())
