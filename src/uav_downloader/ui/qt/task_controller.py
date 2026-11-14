"""Qt main-thread adapter over the GUI-independent task command service."""
import queue
import time
import os

from PySide6.QtCore import QDir, QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from uav_downloader.core.task_service import TaskService
from uav_downloader.core.task_queue import MAX_VISIBLE_ROWS, _visible_window
from uav_downloader.core.service_errors import StopTimeoutError
from uav_downloader.ui.qt.task_model import DownloadListModel
from uav_downloader.i18n.locales import T, state_label
from uav_downloader.i18n.qt_strings import translate as QT
from uav_downloader.apps.downloads import filter_items, video_code


class TaskController(QObject):
    errorOccurred = Signal(str)
    closingChanged = Signal()
    closeIssueChanged = Signal()
    preparingClose = Signal()
    readyChanged = Signal()
    closeReady = Signal()
    closeNeedsDecision = Signal(str)
    importsChanged = Signal()
    inputConsumed = Signal(str)
    logsAppended = Signal(str)
    workflowChanged = Signal()
    batchCompleted = Signal(str)
    batchAdded = Signal('QVariantList')
    activityChanged = Signal()

    def __init__(self, path, parent=None, service=None):
        super().__init__(parent)
        self.service = service or TaskService(path)
        self._queue_path = os.path.abspath(path)
        self.model = DownloadListModel(self)
        self._closing, self._ready = False, False
        self._view_active = True
        self._close_future = None
        self._close_deadline = None
        self._close_issue = ''
        self._close_services = []
        self._other_close_futures = []
        self._pending = [self.service.ready]
        self._inputs = {}
        self._folders = {}
        self._batch_futures = {}
        self._filter = 'all'
        self._has_tasks = False
        self._hidden_task_count = 0
        self._running_work = False
        self._status_message = ''
        self._status_futures = {}
        self._summary = ''
        self._batch_busy = False
        self._queue_status = ''
        self.notify_on_complete = True
        self.console = None
        self.stderr_console = None
        self._imports = dict(running=0, stopping=0, paused=0, added=0)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @Property(bool, notify=closingChanged)
    def closing(self):
        return self._closing

    @Property(str, notify=closeIssueChanged)
    def closeIssue(self): return self._close_issue

    def _set_close_issue(self, value):
        if value != self._close_issue:
            self._close_issue = value
            self.closeIssueChanged.emit()

    @Property(bool, notify=readyChanged)
    def ready(self):
        return self._ready

    @Property('QVariantMap', notify=importsChanged)
    def imports(self): return self._imports

    @Property(str, notify=workflowChanged)
    def summary(self): return self._summary

    @Property(bool, notify=activityChanged)
    def hasTasks(self): return self._has_tasks

    @Property(int, notify=activityChanged)
    def hiddenTaskCount(self): return self._hidden_task_count

    @Property(bool, notify=activityChanged)
    def hasRunningWork(self): return self._running_work

    @Property(str, notify=activityChanged)
    def statusMessage(self): return self._status_message

    @Property(str, notify=activityChanged)
    def queueStatus(self): return self._queue_status

    def _set_status(self, text):
        if self._status_message != text:
            self._status_message = text
            self.activityChanged.emit()

    @Property(bool, notify=workflowChanged)
    def submittingBatch(self): return bool(self._batch_futures)

    @Slot(str)
    def setFilter(self, value):
        if value in {'all', 'active', 'queued', 'failed', 'done', 'paused'}:
            self._filter = value
            self.poll()

    def _refresh_task_view(self, rows):
        filtered = filter_items(rows, self._filter)
        visible = _visible_window(filtered, MAX_VISIBLE_ROWS)
        hidden = len(filtered) - len(visible)
        if hidden != self._hidden_task_count:
            self._hidden_task_count = hidden
            self.activityChanged.emit()
        self.model.refresh(visible)

    @Slot('QVariantList', str, bool, 'QVariantMap')
    def addBatch(self, videos, dest, start, options):
        if self._batch_futures:
            return
        future = self._command('add_batch', videos, dest, start, options)
        if future is not None:
            self._batch_futures[future] = ([video.get('url', '') for video in videos], start)
            self.workflowChanged.emit()

    @Slot(QUrl)
    def exportQueue(self, url):
        if url.toLocalFile():
            future = self._command('export', url.toLocalFile())
            if future is not None:
                self._status_futures[future] = 'export_complete'

    @Slot(str, str)
    def downloadLink(self, url, dest):
        if url.strip():
            future = self._command('enqueue', url.strip(), dest.strip() or 'download')
            if future is not None:
                self._inputs[future] = url

    @Slot(str)
    def pasteUrls(self, destination):
        from PySide6.QtGui import QGuiApplication
        future = self._command('paste_urls', QGuiApplication.clipboard().text(), destination.strip() or 'download')
        if future is not None:
            self._status_futures[future] = 'import_complete'

    @Slot(str)
    def copyDetails(self, url):
        import json
        from PySide6.QtGui import QGuiApplication
        row = next((row for row in self.service.manager.snapshot_items() if row.url == url), None)
        if row:
            values = dict(job_id=row.job_id, site=row.site, code=video_code(row.url, row.name),
                          state=state_label(row.state), progress=row.progress, url=row.url,
                          destination=row.dest, resolution=row.resolution,
                          filename_template=row.filename_template, error_code=row.error_code,
                          error=row.error, retry_count=row.retry_count)
            QGuiApplication.clipboard().setText(json.dumps(values, ensure_ascii=False, indent=2))
            self._set_status(T('copy_diagnostics'))

    @Property(str, constant=True)
    def queuePath(self): return self._queue_path

    @Property(str, constant=True)
    def queueFolder(self): return os.path.dirname(self._queue_path)

    @Slot(str, result=QUrl)
    def folderUrl(self, path): return QUrl.fromLocalFile(os.path.abspath(path or 'download'))

    @Slot(QUrl, result=str)
    def localFolder(self, url): return QDir.toNativeSeparators(url.toLocalFile())

    def _command(self, name, *args):
        if self._closing or not self._ready:
            return
        try:
            future = self.service.submit(name, *args)
            self._pending.append(future)
            return future
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    @Slot(str, str)
    def enqueue(self, urls, dest):
        future = self._command('enqueue_urls', urls, dest.strip() or 'download')
        if future is not None:
            self._inputs[future] = urls

    @Slot(str, str)
    def queueUrls(self, urls, dest):
        future = self._command('queue_urls', urls, dest.strip() or 'download')
        if future is not None:
            self._inputs[future] = urls

    @Slot('QVariantList', str)
    def importFiles(self, urls, dest):
        paths = [QUrl(value).toLocalFile() if not isinstance(value, QUrl) else value.toLocalFile() for value in urls]
        if paths and all(paths):
            self._command('import_files', paths, dest.strip() or 'download')

    @Slot('QVariantList')
    def subtitleLocalFiles(self, urls):
        paths = [QUrl(value).toLocalFile() for value in urls]
        if paths and all(paths):
            self._command('local_subtitles', paths)

    def enqueueVideo(self, video, dest):
        self._command('enqueue_video', dict(video), dest.strip() or 'download')

    def queueVideo(self, video, dest):
        self._command('add_video', dict(video), dest.strip() or 'download')

    @Slot(str)
    @Slot(str, str)
    def downloadAll(self, dest, urls=''):
        future = self._command('download_all', dest.strip() or 'download', urls)
        if future is not None and urls:
            self._inputs[future] = urls

    @Slot(str)
    def openFolder(self, path):
        future = self._command('folder_path', path)
        if future is not None:
            self._folders[future] = True

    @Slot(str)
    def retry(self, url):
        item = next((row for row in self.service.manager.snapshot_items() if row.url == url), None)
        self._command('pause' if item and item.action == 'pause' else 'resume' if item and item.action == 'resume' else 'retry', url)

    @Slot()
    def pauseAll(self): self._command('pause_all')

    @Slot()
    def completionAlert(self):
        if os.name == 'nt':
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)

    @Slot(str)
    def remove(self, url):
        self._command('remove', url)

    @Slot()
    def cancelAll(self):
        self._command('cancel_all')

    @Slot()
    def clearAll(self):
        self._command('clear_all')

    @Slot()
    def save(self):
        self._command('save')

    @Slot(bool)
    def setTaskViewActive(self, active):
        self._view_active = active
        if active and self._ready:
            self.poll()

    def addCloseService(self, service):
        self._close_services.append(service)

    @Slot()
    def requestClose(self):
        if self._closing and self._close_deadline is not None:
            return
        if not self._closing:
            self.preparingClose.emit()
            self._closing = True
            self.closingChanged.emit()
        self._set_close_issue('')
        self._close_future = self.service.close()
        self._other_close_futures = [service.close() for service in self._close_services]
        self._close_deadline = time.monotonic() + 10

    @Slot()
    def discardAndClose(self):
        self.service.abandon()
        for service in self._close_services:
            service.abandon()
        self.timer.stop()
        self.closeReady.emit()

    @Slot()
    def poll(self):
        listings = getattr(self.service, 'listings', None)
        if listings is not None:
            imports = listings.snapshot()
            imports.setdefault('stopping', 0)
            imports.setdefault('paused', 0)
            files = getattr(self.service, 'files', None)
            if files is not None:
                file_imports = files.snapshot()
                imports = {key: imports[key] + file_imports.get(key, 0) for key in imports}
            if imports != self._imports:
                self._imports = imports
                self.importsChanged.emit()
        for future in self._pending[:]:
            if future.done():
                self._pending.remove(future)
                consumed = self._inputs.pop(future, None)
                folder = self._folders.pop(future, False)
                status = self._status_futures.pop(future, '')
                try:
                    result = future.result()
                    if future in self._batch_futures:
                        submitted_urls, start = self._batch_futures.pop(future)
                        self._summary = T('queue_started_summary' if start else 'queue_summary', **result)
                        self.workflowChanged.emit()
                        self.batchAdded.emit(submitted_urls)
                    if folder and not QDesktopServices.openUrl(QUrl.fromLocalFile(result)):
                        raise OSError(T('open_folder_failed_title'))
                    if consumed is not None:
                        self.inputConsumed.emit(consumed)
                    if status:
                        self._set_status(T(status, **(result if isinstance(result, dict) else {})))
                    if future is not self.service.ready:
                        self.logsAppended.emit(QT('qt_operation_done'))
                    if future is self.service.ready:
                        self._ready = True
                        self.readyChanged.emit()
                except Exception as exc:
                    if self._batch_futures.pop(future, None) is not None:
                        self.workflowChanged.emit()
                    self.errorOccurred.emit(str(exc))
                    self.logsAppended.emit(str(exc))
        for console in (self.console, self.stderr_console):
            if console is not None:
                text = console.drain()
                if text:
                    self.logsAppended.emit(text)
        while True:
            try:
                self.errorOccurred.emit(self.service.errors.get_nowait())
            except queue.Empty:
                break
        rows = self.service.manager.snapshot_items() if self._view_active or not self._closing else ()
        counts = getattr(self.service.manager, 'activity_counts', lambda: (0, 0, 0, 0, 1))()
        active, pending, subtitle_active, subtitle_pending, limit = counts
        busy = any(counts[:4])
        running = busy or bool(self._imports.get('running'))
        status = []
        if active:
            status.append(f'{state_label("下載中")} {active}/{limit}')
        if pending:
            status.append(f'{state_label("等待中")} {pending}')
        if subtitle_active or subtitle_pending:
            status.append(T('subtitle_queue_status', active=subtitle_active, pending=subtitle_pending))
        done = sum(row.state in {'已下載', '未偵測到日語語音'} for row in rows)
        if done:
            status.append(f'{state_label("已下載")} {done}')
        queue_status = '  |  '.join(status)
        if self._has_tasks != bool(rows) or self._running_work != running or self._queue_status != queue_status:
            self._has_tasks, self._running_work = bool(rows), running
            self._queue_status = queue_status
            self.activityChanged.emit()
        if self._view_active:
            self._refresh_task_view(rows)
        if not self._closing:
            if self._batch_busy and not busy and self.notify_on_complete:
                self.batchCompleted.emit(T('batch_complete'))
            self._batch_busy = busy
        if self._close_future is not None and self._close_deadline is not None:
            futures = [self._close_future, *self._other_close_futures]
            if all(future.done() for future in futures):
                self._close_deadline = None
                errors = []
                for future in futures:
                    try:
                        future.result()
                    except Exception as error:
                        errors.append(error)
                if errors:
                    stopped = next((error for error in errors if isinstance(error, StopTimeoutError)), None)
                    self._set_close_issue('stopping' if stopped is not None else 'saving')
                    self.closeNeedsDecision.emit(QT('qt_stop_wait_timeout') if stopped is not None else str(errors[0]))
                    for error in errors:
                        self.logsAppended.emit(str(error))
                else:
                    self.timer.stop()
                    self.closeReady.emit()
            elif time.monotonic() >= self._close_deadline:
                self._close_deadline = None
                self._set_close_issue('pending')
                self.closeNeedsDecision.emit(QT('qt_close_wait_timeout'))
