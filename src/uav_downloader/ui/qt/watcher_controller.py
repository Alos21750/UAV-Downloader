"""GUI-thread Watcher adapter with bounded log/model updates."""
import queue
from concurrent.futures import Future
from types import SimpleNamespace
from datetime import datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Property, QSortFilterProxyModel, Qt, QTimer, Signal, Slot

from uav_downloader.apps.watch_categories import SITES, group_label, target_label, find_target
from uav_downloader.core.watcher_service import WatcherService
from uav_downloader.core.futures import all_completed
from uav_downloader.i18n.locales import T
from uav_downloader.i18n.qt_strings import translate as QT
from uav_downloader.core.watcher import _normalize_scan_schedule, _plan_next_scan, _valid_baseline_date
from uav_downloader.core.watcher_dates import preset_date, calendar_seed, month_days


class WatchTargetModel(QAbstractListModel):
    fields = ('site', 'targetId', 'group', 'label', 'selected', 'groupId')
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = [dict(site=site, targetId=target['id'], group=group_label(group), groupId=group['id'],
                          label=target_label(target), selected=False)
                     for site, definition in SITES.items() for group in definition['groups']
                     for target in group['targets']]

    def roleNames(self):
        return {Qt.UserRole + i + 1: field.encode() for i, field in enumerate(self.fields)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role):
        field = role - Qt.UserRole - 1
        if index.isValid() and 0 <= index.row() < len(self.rows) and 0 <= field < len(self.fields):
            return self.rows[index.row()][self.fields[field]]

    def restore(self, targets):
        selected = set()
        for row in targets:
            if not isinstance(row, dict) or row.get('site') not in SITES:
                continue
            target = find_target(row['site'], row.get('id'), row.get('category'))
            if target:
                selected.add((row['site'], target['id']))
        for index, row in enumerate(self.rows):
            self.select(index, (row['site'], row['targetId']) in selected)

    def select(self, index, selected):
        if 0 <= index < len(self.rows) and self.rows[index]['selected'] != selected:
            self.rows[index]['selected'] = selected
            self.dataChanged.emit(self.index(index), self.index(index), [Qt.UserRole + 5])

    def select_group(self, site, group, value):
        changed = []
        for index, row in enumerate(self.rows):
            if row['site'] == site and (not group or row['groupId'] == group) and row['selected'] != value:
                row['selected'] = value
                changed.append(index)
        if changed:
            self.dataChanged.emit(self.index(changed[0]), self.index(changed[-1]), [Qt.UserRole + 5])

    def selection(self):
        return [dict(site=row['site'], id=row['targetId']) for row in self.rows if row['selected']]

    def retranslate(self):
        for row, (group, target) in zip(self.rows,
                ((group, target) for definition in SITES.values() for group in definition['groups']
                 for target in group['targets'])):
            row.update(group=group_label(group), label=target_label(target))
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), [Qt.UserRole + 3, Qt.UserRole + 4])


class WatcherController(QObject):
    changed = Signal()
    configChanged = Signal()
    errorOccurred = Signal(str)
    logsAppended = Signal(str)
    categoryLayoutChanged = Signal()
    categorySelectionChanged = Signal()

    def __init__(self, directory, parent=None, service=None, enabled=True, auto_start=True, startup_ready=True):
        super().__init__(parent)
        if not enabled:
            ready, closed = Future(), Future()
            ready.set_result({})
            closed.set_result(True)
            service = SimpleNamespace(ready=ready, worker=None, events=queue.SimpleQueue(),
                                      close=lambda: closed, abandon=lambda: None)
        self.service = service or WatcherService(directory)
        self.model = WatchTargetModel(self)
        self.targets = WatchTargetFilter(self)
        self.targets.setSourceModel(self.model)
        self.model.dataChanged.connect(self._category_data_changed)
        self._config, self._state = {}, dict(running=False, stopping=False, monitoring=False, scanning=False, progress=0,
                                           title='', scan='', status=T('st_idle'))
        self._pending = [('load', self.service.ready)]
        self._closed = False
        self._close_future = None
        self._status_key = 'st_idle'
        self._stop_requested = False
        self._auto_start = auto_start and enabled
        self._startup_ready = startup_ready
        self._startup_consumed = False
        self._loaded = False
        self._draft = None
        self._draft_queued = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    @Property('QVariantMap', notify=configChanged)
    def config(self): return self._config

    @Property('QStringList', constant=True)
    def siteNames(self): return list(SITES)

    @Property('QVariantMap', constant=True)
    def siteCounts(self):
        return {site: sum(len(group['targets']) for group in definition['groups'])
                for site, definition in SITES.items()}

    def _category_data_changed(self, first, last, roles):
        if Qt.UserRole + 5 in roles:
            self.categorySelectionChanged.emit()
        else:
            self.categoryLayoutChanged.emit()

    @Property('QVariantList', notify=categoryLayoutChanged)
    def categoryGroups(self):
        site = self.targets.site
        groups = {group['id']: dict(id=group['id'], label=group_label(group),
                                   total=len(group['targets']), choices=[])
                  for group in SITES[site]['groups']}
        for index, row in enumerate(self.model.rows):
            if row['site'] == site and self.targets.text in row['label'].casefold():
                groups[row['groupId']]['choices'].append(dict(index=index, label=row['label']))
        return [group for group in groups.values() if group['choices']]

    @Property('QVariantMap', notify=categorySelectionChanged)
    def categorySelection(self):
        return {str(index): row['selected'] for index, row in enumerate(self.model.rows)
                if row['site'] == self.targets.site}

    @Property('QVariantMap', notify=categorySelectionChanged)
    def categoryGroupSelection(self):
        selected = {}
        for row in self.model.rows:
            if row['site'] == self.targets.site:
                key = row['groupId']
                selected[key] = selected.get(key, True) and row['selected']
        return selected

    @Slot(int, bool)
    def selectCategory(self, index, checked):
        self.model.select(index, checked)
        self.changed.emit()

    @Slot('QVariantMap')
    def saveSettings(self, values):
        if not self._closed:
            try:
                self._pending.append(('save', self.service.submit('save', dict(values))))
            except Exception as error:
                self.errorOccurred.emit(str(error))
            self.changed.emit()

    @Property('QVariantMap', notify=changed)
    def state(self): return self._state

    @Property(bool, notify=changed)
    def busy(self): return bool(self._pending)

    @Property(int, notify=changed)
    def selectedCount(self): return len(self.model.selection())

    @Property('QVariantList', notify=changed)
    def groups(self):
        return [dict(id='', label=QT('qt_all_groups'))] + [dict(id=group['id'], label=group_label(group))
                                                        for group in SITES[self.targets.site]['groups']]

    @Property(str, notify=changed)
    def filterGroup(self): return self.targets.group

    @Property('QStringList', notify=changed)
    def collapsedGroups(self): return sorted(group for site, group in self.targets.collapsed if site == self.targets.site)

    @Slot(bool)
    def setStartupReady(self, ready):
        self._startup_ready = ready
        self._try_autostart()

    def _try_autostart(self):
        if self._closed or not self._auto_start or self._startup_consumed or not self._loaded or not self._startup_ready or self.busy:
            return
        self._startup_consumed = True
        worker = self.service.worker
        if (self._config.get('output_folder') and self._config.get('selected_targets') and
                not (worker and worker.is_running())):
            self._pending.append(('monitor', self.service.submit('monitor', {'selected_targets': self.model.selection()})))
            self.changed.emit()

    @Slot(int, result=str)
    def datePreset(self, index): return preset_date(index)

    @Slot(str, result='QVariantMap')
    def calendarSeed(self, value): return calendar_seed(value)

    @Slot(int, int, result='QVariantMap')
    def calendarMonth(self, year, month): return month_days(year, month)

    @Slot(str, result=bool)
    def validBaseline(self, value): return _valid_baseline_date(value)

    @Slot('QVariantMap')
    def prepareClose(self, values):
        if not self._closed:
            self._draft = dict(values)

    @Slot(int, bool)
    def select(self, row, value):
        self.model.select(self.targets.mapToSource(self.targets.index(row, 0)).row(), value)
        self.changed.emit()

    @Slot(str, str)
    def setFilter(self, site, text):
        if site not in SITES:
            return
        self.targets.beginFilterChange()
        if site != self.targets.site:
            self.targets.group = ''
        self.targets.site, self.targets.text = site, text.casefold()
        self.targets.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        self.categoryLayoutChanged.emit()
        self.categorySelectionChanged.emit()
        self.changed.emit()

    @Slot(str)
    def setGroup(self, group):
        self.targets.beginFilterChange()
        self.targets.group = group
        self.targets.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        self.changed.emit()

    @Slot(str, bool)
    def selectGroup(self, group, value):
        self.model.select_group(self.targets.site, group, value)
        self.changed.emit()

    @Slot(str)
    def toggleGroup(self, group):
        if group:
            key = self.targets.site, group
            self.targets.beginFilterChange()
            self.targets.collapsed.remove(key) if key in self.targets.collapsed else self.targets.collapsed.add(key)
            self.targets.endFilterChange(QSortFilterProxyModel.Direction.Rows)
            self.changed.emit()

    @Slot(bool)
    def selectVisible(self, value):
        for row in range(self.targets.rowCount()):
            self.model.select(self.targets.mapToSource(self.targets.index(row, 0)).row(), value)
        self.changed.emit()

    @Slot('QVariantMap', str)
    def apply(self, values, action):
        if self._closed or self.busy:
            return
        self._startup_consumed = True
        values = dict(values, selected_targets=self.model.selection())
        try:
            self._pending.append((action, self.service.submit(action, values)))
        except Exception as error:
            self.errorOccurred.emit(str(error))
        self.changed.emit()

    @Slot()
    def stop(self):
        self._startup_consumed = True
        if not self._closed and not self._stop_requested:
            self._stop_requested = True
            try:
                self._pending.append(('stop', self.service.submit('stop')))
            except Exception as error:
                self._stop_requested = False
                self.errorOccurred.emit(str(error))
            self.changed.emit()

    @Slot()
    def freeze(self): self._closed = True

    @Slot()
    def retranslate(self):
        self.model.retranslate()
        self._state['status'] = T(self._status_key)
        self.changed.emit()

    @Slot()
    def poll(self):
        for action, future in self._pending[:]:
            if not future.done():
                continue
            self._pending.remove((action, future))
            try:
                result = future.result()
                if action in {'load', 'save', 'monitor', 'check'}:
                    self._config = result['config'] if action in {'monitor', 'check'} else result
                    if action == 'load':
                        self.model.restore(self._config.get('selected_targets', []))
                        self._config['selected_targets'] = self.model.selection()
                        self._loaded = True
                    self.configChanged.emit()
            except Exception as error:
                if not self._closed:
                    self.errorOccurred.emit(str(error))
            self.changed.emit()
        worker = self.service.worker
        state = dict(self._state)
        logs = []
        for _ in range(128):
            try:
                kind, value, generation = self.service.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                logs.append(value)
            elif worker is not None and (generation is None or generation == worker.run_generation):
                self._status_key = value
                state['status'] = T(value)
        if logs:
            self.logsAppended.emit('\n'.join(logs))
        if worker is not None:
            progress, scan = worker.get_progress(), worker.get_scan_state()
            running = worker.is_running()
            stopping = running and (self._stop_requested or bool(getattr(worker, 'is_stopping', lambda: False)()))
            if stopping and self._status_key != 'st_storage_failed':
                self._status_key = 'st_stopping'
            elif not running and (self._stop_requested or self._state.get('stopping')):
                self._stop_requested = False
                if self._status_key != 'st_storage_failed':
                    self._status_key = 'st_stopped'
            status = T(self._status_key)
            if stopping and self._status_key == 'st_storage_failed':
                status += ' · ' + T('st_stopping')
            state.update(running=running, stopping=stopping, status=status,
                         monitoring=worker.is_monitoring(), scanning=worker.is_scanning(),
                         progress=int(progress[0] * 100 / progress[1]) if progress and progress[1] > 0 else -1 if progress else 0,
                         title=progress[3] if progress else '', scan=f'{scan[0]} / {scan[1]} / {scan[2]}' if scan else '',
                         speed=progress[2] if progress else 0, done=progress[0] if progress else 0,
                         total=progress[1] if progress else 0, candidates=scan[3] if scan else 0)
        cfg = self.service.schedule_snapshot() if hasattr(self.service, 'schedule_snapshot') else self._config
        schedule = _normalize_scan_schedule(cfg.get('scan_schedule'))
        plan = _plan_next_scan(cfg)
        now = datetime.now().astimezone()
        state.update(schedule=QT('qt_schedule_daily', time=schedule['daily_time']) if schedule['mode'] == 'daily'
                     else QT('qt_schedule_interval', hours=schedule['interval_hours']),
                     timezone=now.strftime('%Z UTC%z'), nextScan=plan.target_local.strftime('%Y-%m-%d %H:%M'))
        if state != self._state:
            self._state = state
            self.changed.emit()
        self._try_autostart()

    def close(self):
        self.freeze()
        if self._close_future is not None:
            if not self._close_future.done() or self._close_future.exception() is None:
                return self._close_future
        retry = self._close_future is not None and self._close_future.exception() is not None
        if self._loaded and self._draft is not None and not self._draft_queued:
            self._draft_queued = True
            values = dict(self._draft, selected_targets=self.model.selection())
            self._pending.append(('save', self.service.submit('save', values)))
        pending = [] if retry else [future for _, future in self._pending]
        self._close_future = all_completed(pending + [self.service.close()])
        return self._close_future

    def abandon(self):
        self.freeze()
        self.service.abandon()


class WatchTargetFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.site, self.text = 'JableTV', ''
        self.group = ''
        self.collapsed = set()

    def filterAcceptsRow(self, row, parent):
        record = self.sourceModel().rows[row]
        return (record['site'] == self.site and
                (not self.group or record['groupId'] == self.group) and
                (record['site'], record['groupId']) not in self.collapsed and
                self.text in (record['label'] + ' ' + record['group'] + ' ' + record['targetId']).casefold())
