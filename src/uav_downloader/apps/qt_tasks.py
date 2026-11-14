"""Qt Quick desktop workspace shared by Browser and Watcher.

Run python -m uav_downloader.apps.qt_tasks --queue <csv-path>.
An explicit queue path keeps evaluation separate from an open legacy GUI.
"""
import argparse
import sys
from pathlib import Path


def fill_clipboard_input(url_input, text):
    """Retain upstream clipboard hints; queue mutation requires a user action."""
    from uav_downloader.apps.downloads import extract_urls
    urls = extract_urls(str(text)[:65536], max_urls=1)
    if urls:
        url_input.setProperty('text', urls[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', type=Path, required=True)
    parser.add_argument('--dest', default=None)
    parser.add_argument('--watch-state', type=Path, help='Directory containing Watcher config.json and seen.json')
    parser.add_argument('--smoke-report', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--update-channel', choices=['browse', 'watch'], help=argparse.SUPPRESS)
    parser.add_argument('--workspace', choices=['all', 'browser', 'watcher'], default='all')
    parser.add_argument('--initial-url', default='')
    args = parser.parse_args(argv)
    from PySide6.QtCore import QObject, QUrl, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtQuickControls2 import QQuickStyle
    import shiboken6
    from uav_downloader.ui.qt.task_controller import TaskController
    from uav_downloader.ui.qt.browse_controller import BrowseController, ThumbnailProvider
    from uav_downloader.ui.qt.settings_controller import SettingsController
    from uav_downloader.ui.qt.watcher_controller import WatcherController
    from uav_downloader.ui.qt.i18n_controller import I18nController
    from uav_downloader.ui.qt.update_controller import UpdateController
    from uav_downloader.core.task_service import TaskService
    from uav_downloader.core.watcher_service import WatcherService
    from uav_downloader.i18n.locales import set_lang
    from uav_downloader.core import config

    saved_language = config.get_ui_lang()
    set_lang(saved_language)
    config.load_cf_overrides()
    QQuickStyle.setStyle('Basic')
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName('UAV Qt Tasks')
    queue_path = str(args.queue.resolve())
    task_service = TaskService(queue_path, initialize_resolution=args.workspace != 'watcher',
                               migrate_legacy_queue=args.workspace == 'browser')
    controller = TaskController(queue_path, service=task_service)
    browser = BrowseController(controller)
    settings = SettingsController(controller.service)
    settings.valuesChanged.connect(lambda: setattr(
        controller, 'notify_on_complete', settings.values.get('notify_on_complete', True)))
    watch_directory = args.watch_state or args.queue.parent / 'watcher'
    watcher_service = WatcherService(watch_directory, apply_resolution=True) if args.workspace == 'watcher' else None
    watcher = WatcherController(watch_directory, service=watcher_service, enabled=args.workspace != 'browser',
                                auto_start=args.workspace == 'watcher' and not args.smoke_report, startup_ready=False)
    updates = UpdateController(args.update_channel)
    updates.installReady.connect(controller.requestClose)
    i18n = I18nController(settings)
    i18n.changed.connect(watcher.retranslate)
    i18n.changed.connect(browser.retranslate)
    i18n.changed.connect(updates.retranslate)
    controller.addCloseService(watcher)
    controller.addCloseService(settings)
    controller.closingChanged.connect(watcher.freeze)
    controller.closingChanged.connect(browser.close)
    controller.closingChanged.connect(settings.freeze)
    controller.closingChanged.connect(updates.close)
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({'workspaceMode': args.workspace,
                                'needsLanguageChoice': saved_language is None and args.workspace != 'all' and not args.smoke_report})
    warnings = []
    engine.warnings.connect(lambda values: warnings.extend(str(value) for value in values))
    context = engine.rootContext()
    context.setContextProperty('controller', controller)
    context.setContextProperty('tasks', controller.model)
    context.setContextProperty('browser', browser)
    context.setContextProperty('videos', browser.model)
    context.setContextProperty('settings', settings)
    context.setContextProperty('watcher', watcher)
    context.setContextProperty('watchTargets', watcher.targets)
    context.setContextProperty('i18n', i18n)
    context.setContextProperty('updates', updates)
    engine.addImageProvider('thumbnails', ThumbnailProvider())
    context.setContextProperty('initialDestination', str(Path(args.dest or config.get_download_directory() or 'download').resolve()))
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / 'ui' / 'qt' / 'Tasks.qml')))
    if not engine.rootObjects():
        controller.service.abandon()
        watcher.abandon()
        settings.abandon()
        browser.close()
        updates.close()
        shiboken6.delete(engine)
        raise RuntimeError('Qt task workspace could not be loaded\n' + '\n'.join(warnings))
    window = engine.rootObjects()[0]
    tabs = window.findChild(QObject, 'workspaceTabs')
    tabs.setProperty('currentIndex', window.property('monitorTabIndex') if args.workspace == 'watcher'
                     else window.property('tasksTabIndex') if args.initial_url else window.property('browseTabIndex'))
    if args.initial_url:
        window.findChild(QObject, 'urlInput').setProperty('text', args.initial_url)
    if args.workspace == 'browser' and not args.smoke_report:
        QTimer.singleShot(0, lambda: browser.loadSite('JableTV'))
        QTimer.singleShot(0, updates.check)
        # Clipboard changes only suggest an input, as in upstream v3.5.0.
        clipboard = app.clipboard()
        def import_clipboard():
            if not controller.ready or controller.closing:
                return
            fill_clipboard_input(window.findChild(QObject, 'urlInput'), clipboard.text())
        clipboard.dataChanged.connect(import_clipboard)
        controller.readyChanged.connect(import_clipboard)
    smoke = None
    smoke_state = {}
    if args.smoke_report:
        # Packaging smoke: no network or downloads; caller supplies an isolated profile/queue.
        import time
        started = time.monotonic()
        smoke = QTimer()
        def finish_smoke():
            if controller.ready and not settings.busy and not watcher.busy:
                smoke_state.update(workspace=args.workspace, title=window.property('title'),
                                   initial_tab=tabs.property('currentIndex'),
                                   queue_path=queue_path, watch_state=str(watch_directory.resolve()),
                                   language=i18n.language, watcher_enabled=args.workspace != 'browser',
                                   initial_url=window.findChild(QObject, 'urlInput').property('text'),
                                   graphics_api=str(window.rendererInterface().graphicsApi()),
                                   task_rows=len(controller.service.manager.snapshot_items()),
                                   device_pixel_ratio=window.devicePixelRatio())
                smoke.stop()
                controller.requestClose()
            elif time.monotonic() - started > 15:
                warnings.append('Startup timed out')
                smoke.stop()
                controller.discardAndClose()
        controller.errorOccurred.connect(warnings.append)
        settings.errorOccurred.connect(warnings.append)
        watcher.errorOccurred.connect(warnings.append)
        updates.errorOccurred.connect(warnings.append)
        controller.closeNeedsDecision.connect(lambda message: (warnings.append(message), controller.discardAndClose()))
        smoke.timeout.connect(finish_smoke)
        smoke.start(100)
    from uav_downloader.core.console import ConsoleRelay
    original_stdout, original_stderr = sys.stdout, sys.stderr
    controller.console = ConsoleRelay(original_stdout)
    controller.stderr_console = ConsoleRelay(original_stderr)
    sys.stdout, sys.stderr = controller.console, controller.stderr_console
    try:
        result = app.exec()
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
    controller.timer.stop()
    browser.close()
    settings.abandon()
    watcher.timer.stop()
    updates.close()
    controller.service.abandon()
    shiboken6.delete(engine)
    if args.smoke_report:
        import json
        args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
        args.smoke_report.write_text(json.dumps(dict(ready=controller.ready, errors=warnings, **smoke_state), indent=2), encoding='utf-8')
        if warnings:
            return 1
    if updates.pending_install:
        from uav_downloader.core import updater
        if not updater.apply_update_and_restart(updates.pending_install):
            return 1
    return result


if __name__ == '__main__':
    raise SystemExit(main())
