"""Preference editing through the same ordered worker/close barrier as tasks."""
from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from uav_downloader.core.futures import all_completed


class SettingsController(QObject):
    changed = Signal()
    valuesChanged = Signal()
    profileChanged = Signal()
    cloudflareChanged = Signal()
    systemProxyChanged = Signal()
    errorOccurred = Signal(str)
    saved = Signal()
    groupSaved = Signal(str)
    groupError = Signal(str, str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._values, self._profile = {}, {}
        self._cloudflare = {}
        self._system_proxy = {}
        self._pending = None
        self._pending_group = ''
        self._queued_writes = []
        self._closed = False
        self._close_future = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self._submit('get_preferences')

    @Property('QVariantMap', notify=valuesChanged)
    def values(self): return self._values

    @Property('QVariantMap', notify=profileChanged)
    def profile(self): return self._profile

    @Property('QVariantMap', notify=cloudflareChanged)
    def cloudflare(self): return self._cloudflare

    @Property('QVariantMap', notify=systemProxyChanged)
    def systemProxy(self): return self._system_proxy

    @Property(bool, notify=changed)
    def busy(self): return self._pending is not None

    @Property(str, notify=changed)
    def busyGroup(self): return self._pending_group if self.busy else ''

    def _submit(self, action, *args, group=''):
        if self._closed:
            return
        if self._pending is not None:
            if action == 'set_preferences':
                try:
                    self._queued_writes.append((action, self.service.submit(action, *args), group))
                except Exception as error:
                    self.errorOccurred.emit(str(error))
            return
        try:
            self._pending = (action, self.service.submit(action, *args))
            self._pending_group = group
        except Exception as error:
            if group:
                self.groupError.emit(group, str(error))
            self.errorOccurred.emit(str(error))
            if action == 'get_translation':
                self.profileChanged.emit()  # Restore the last successfully loaded profile.
        self.changed.emit()

    @Slot('QVariantMap')
    def save(self, values): self._submit('set_preferences', dict(values))

    @Slot(str, 'QVariantMap')
    def saveGroup(self, group, values):
        self._submit('set_preferences', dict(values), group=group)

    @Slot(QUrl)
    def saveDownloadDirectory(self, folder):
        self.saveGroup('destination', dict(download_directory=folder.toLocalFile()))

    @Slot()
    def refreshSystemProxy(self): self._submit('get_system_proxy', group='proxy-detect')

    @Slot(str, result=str)
    def validateProxyUrl(self, value):
        from uav_downloader.core.config import normalize_proxy_url
        if not value.strip():
            return 'empty'
        try:
            normalize_proxy_url(value)
        except ValueError:
            return 'invalid'
        return 'valid'

    @Slot()
    def cycleTheme(self):
        modes = ['system', 'light', 'dark']
        theme = self._values.get('theme', 'system')
        self.save({'theme': modes[(modes.index(theme) + 1) % len(modes)]})

    @Slot(bool)
    def toggleTheme(self, dark):
        # The effective appearance includes system mode. Save only the theme;
        # unrelated settings-page drafts must never be submitted here.
        self.saveGroup('theme', {'theme': 'light' if dark else 'dark'})

    @Slot(result='QVariantList')
    def compatiblePresets(self):
        from uav_downloader.core.translation_presets import COMPATIBLE_PRESETS
        return [dict(name=name, base_url=url, model=model) for name, (url, model) in COMPATIBLE_PRESETS.items()]

    @Slot(str)
    def loadProfile(self, provider): self._submit('get_translation', provider)

    @Slot('QVariantMap')
    def saveProfile(self, values): self._submit('set_translation', dict(values), group='translation')

    @Slot(str)
    def loadCloudflare(self, host): self._submit('get_cf', host)

    @Slot('QVariantMap')
    def saveCloudflare(self, values): self._submit('set_cf', dict(values), group='cloudflare')

    @Slot()
    def poll(self):
        if self._pending is None:
            if self._closed:
                self.timer.stop()
            return
        action, future = self._pending
        if not future.done():
            return
        group = self._pending_group
        self._pending = None
        self._pending_group = ''
        try:
            result = future.result()
        except Exception as error:
            if not self._closed:
                if group:
                    self.groupError.emit(group, str(error))
                self.errorOccurred.emit(str(error))
            if action == 'get_translation':
                self.profileChanged.emit()
        else:
            if action == 'get_system_proxy':
                self._system_proxy = result
                self.systemProxyChanged.emit()
            elif action.endswith('_cf'):
                self._cloudflare = result
                self.cloudflareChanged.emit()
            elif action.endswith('translation'):
                self._profile = result
                self.profileChanged.emit()
            else:
                self._values = result
                self.valuesChanged.emit()
            if action.startswith('set_'):
                if group:
                    self.groupSaved.emit(group)
                self.saved.emit()
            if action == 'get_preferences':
                self._submit('get_translation')
            elif action == 'get_translation' and not self._cloudflare:
                self._submit('get_cf')
        if self._pending is None and self._queued_writes:
            action, future, self._pending_group = self._queued_writes.pop(0)
            self._pending = action, future
        self.changed.emit()

    @Slot()
    def freeze(self):
        self._closed = True

    def close(self):
        self.freeze()
        # The task worker orders writes before shutdown. Include their result
        # in the window's barrier so a failed last-minute save cannot vanish.
        if self._close_future is not None and not self._close_future.done():
            return self._close_future
        retry = self._close_future is not None and self._close_future.exception() is not None
        pending = ([self._pending[1]] if self._pending else []) + [future for _, future, _ in self._queued_writes]
        self._close_future = all_completed([] if retry else pending)
        return self._close_future

    def abandon(self):
        self.freeze()
        self.timer.stop()
