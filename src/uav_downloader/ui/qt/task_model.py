"""GUI-thread model for immutable snapshots from the shared download queue."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from uav_downloader.apps.downloads import video_code

class DownloadListModel(QAbstractListModel):
    selectionChanged = Signal()
    FIELDS = ('url', 'name', 'state', 'progress', 'speed', 'error', 'dest', 'action', 'selected', 'local', 'auto_requeues',
              'downloaded_bytes', 'total_bytes', 'file_bytes', 'size_kind', 'segments_done', 'segments_total', 'size_recorded', 'job_id', 'site', 'resolution', 'filename_template', 'thumbnail', 'eta', 'error_code', 'retry_count', 'next_retry_at', 'not_before', 'created_at', 'updated_at', 'source_kind', 'source_fallback', 'resume_hint', 'resume_requested', 'can_cancel', 'source_height', 'video_width', 'video_height')
    ROLES = {Qt.UserRole + index + 1: field.encode() for index, field in enumerate((*FIELDS, 'code'))}
    ROLE_FIELDS = tuple((role, field.decode()) for role, field in ROLES.items() if field not in {b'selected', b'code'})

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = ()
        self.selected = set()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        return self.ROLES

    def data(self, index, role):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        field = self.ROLES.get(role)
        if field is None:
            return None
        if field == b'selected':
            return self._rows[index.row()].url in self.selected
        if field == b'code':
            row = self._rows[index.row()]
            return video_code(row.url, row.name)
        value = getattr(self._rows[index.row()], field.decode())
        # QML translates stable state codes reactively. Translating this role
        # here would leave unchanged rows in the old language after a switch.
        return value

    def refresh(self, snapshots):
        """Preserve delegates for progress changes; notify only changed roles."""
        snapshots = tuple(snapshots)
        selected = self.selected & {row.url for row in snapshots}
        if selected != self.selected:
            self.selected = selected
            self.selectionChanged.emit()
        old = self._rows
        old_urls = {row.url for row in old}
        new_urls = {row.url for row in snapshots}
        common = [row.url for row in snapshots if row.url in old_urls]
        if common != [row.url for row in old if row.url in new_urls]:
            self.beginResetModel()
            self._rows = snapshots
            self.endResetModel()
            return
        # Apply contiguous edits without resetting surviving rows or scroll position.
        end = len(old)
        while end:
            end -= 1
            if old[end].url in new_urls:
                continue
            start = end
            while start and old[start - 1].url not in new_urls:
                start -= 1
            self.beginRemoveRows(QModelIndex(), start, end)
            old = old[:start] + old[end + 1:]
            self._rows = old
            self.endRemoveRows()
            end = start
        index = 0
        while index < len(snapshots):
            if snapshots[index].url in old_urls:
                index += 1
                continue
            start = index
            while index < len(snapshots) and snapshots[index].url not in old_urls:
                index += 1
            self.beginInsertRows(QModelIndex(), start, index - 1)
            old = old[:start] + snapshots[start:index] + old[start:]
            self._rows = old
            self.endInsertRows()
        self._rows = snapshots
        pending_start, pending_end, pending_roles = None, None, None
        for row, (before, after) in enumerate(zip(old, snapshots)):
            roles = [role for role, field in self.ROLE_FIELDS
                     if getattr(before, field) != getattr(after, field)] if before != after else []
            if before.name != after.name:
                roles.append(Qt.UserRole + len(self.FIELDS) + 1)
            if pending_start is not None and (roles != pending_roles or row != pending_end + 1):
                self.dataChanged.emit(self.index(pending_start), self.index(pending_end), pending_roles)
                pending_start = None
            if roles:
                if pending_start is None:
                    pending_start, pending_roles = row, roles
                pending_end = row
        if pending_start is not None:
            self.dataChanged.emit(self.index(pending_start), self.index(pending_end), pending_roles)

    def select(self, url, value):
        for row, item in enumerate(self._rows):
            if item.url == url and (url in self.selected) != value:
                self.selected.add(url) if value else self.selected.discard(url)
                self.dataChanged.emit(self.index(row), self.index(row), [Qt.UserRole + 9])
                self.selectionChanged.emit()
                break

    def select_all(self, value):
        selected = {item.url for item in self._rows} if value else set()
        if self.selected != selected:
            self.selected = selected
            if self._rows:
                self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1), [Qt.UserRole + 9])
            self.selectionChanged.emit()
