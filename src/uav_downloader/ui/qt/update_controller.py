"""GUI-thread update status and safe handoff to the normal close barrier."""
from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from uav_downloader import metadata
from uav_downloader.core import updater
from uav_downloader.core.update_service import UpdateService
from uav_downloader.i18n.locales import T


class UpdateController(QObject):
    changed = Signal()
    errorOccurred = Signal(str)
    installReady = Signal()
    promptRequested = Signal()

    def __init__(self, channel=None, parent=None, service=None):
        super().__init__(parent)
        self.service = service or UpdateService(channel)
        self._future = None
        self._action = ''
        self._status = 'idle'
        self._release = {}
        self.pending_install = ''
        self._closed = False
        self._prompted_versions = set()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    @Property(str, constant=True)
    def currentVersion(self): return metadata.VERSION

    @Property(bool, constant=True)
    def canApply(self): return self.service.can_apply()

    @Property(bool, constant=True)
    def sourceBuild(self): return not updater.is_frozen()

    @Property(bool, notify=changed)
    def busy(self): return self._future is not None

    @Property(bool, notify=changed)
    def available(self): return updater.is_newer(self._release.get('version', ''), metadata.VERSION)

    @Property(str, notify=changed)
    def releaseUrl(self): return str(self._release.get('html_url') or '')

    @Property(str, notify=changed)
    def notes(self): return str(self._release.get('notes') or '')[:4000]

    @Property(str, notify=changed)
    def status(self):
        if self._status == 'available':
            return T('update_available', version=self._release['version'])
        if self._status == 'downloading':
            done, total = self.service.progress()
            return T('update_downloading', pct=int(done * 100 / total) if total else 0)
        return T({'idle': 'update_idle', 'checking': 'update_checking', 'uptodate': 'update_uptodate',
                  'failed': 'update_failed', 'restarting': 'update_restarting'}[self._status])

    @Slot()
    def retranslate(self): self.changed.emit()

    @Slot()
    def check(self):
        if self._closed or self.busy:
            return
        self._future, self._action, self._status = self.service.check(), 'check', 'checking'
        self.changed.emit()

    @Slot()
    def download(self):
        if self._closed or self.busy or not self.available:
            return
        try:
            self._future = self.service.download(dict(self._release))
        except Exception as error:
            self.errorOccurred.emit(str(error))
            return
        self._action, self._status = 'download', 'downloading'
        self.changed.emit()

    @Slot()
    def poll(self):
        if self._closed or self._future is None:
            return
        if not self._future.done():
            if self._action == 'download':
                self.changed.emit()
            return
        future, self._future = self._future, None
        try:
            result = future.result()
            if self._action == 'check':
                if not result:
                    raise RuntimeError(T('update_failed'))
                self._release = result
                self._status = 'available' if self.available else 'uptodate'
                if self.available and result['version'] not in self._prompted_versions:
                    self._prompted_versions.add(result['version'])
                    self.promptRequested.emit()
            else:
                self.pending_install, self._status = result, 'restarting'
                self.installReady.emit()
        except Exception as error:
            self._status = 'failed'
            self.errorOccurred.emit(str(error))
        self.changed.emit()

    @Slot()
    def close(self):
        self._closed = True
        self._future = None
        self.timer.stop()
        self.service.close()
