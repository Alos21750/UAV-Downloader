"""Searchable tag rows with stable backend values and bounded view delegates."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt


class ChoiceModel(QAbstractListModel):
    fields = ('value', 'label', 'group', 'url', 'selected')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def roleNames(self):
        return {Qt.UserRole + index + 1: field.encode() for index, field in enumerate(self.fields)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role):
        field = role - Qt.UserRole - 1
        if index.isValid() and 0 <= index.row() < len(self.rows) and 0 <= field < len(self.fields):
            return self.rows[index.row()][self.fields[field]]

    def replace(self, rows):
        selected = {row['value'] for row in self.rows if row['selected']}
        self.beginResetModel()
        self.rows = [dict({'value': '', 'label': '', 'group': '', 'url': '',
                          'selected': row['value'] in selected}, **row) for row in rows]
        self.endResetModel()

    def select(self, row, selected):
        if 0 <= row < len(self.rows) and self.rows[row]['selected'] != selected:
            self.rows[row]['selected'] = selected
            self.dataChanged.emit(self.index(row), self.index(row), [Qt.UserRole + 5])

    def restore(self, values):
        values = set(values)
        changed = False
        for row in self.rows:
            selected = row['value'] in values
            changed |= row['selected'] != selected
            row['selected'] = selected
        if changed and self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), [Qt.UserRole + 5])

    def selection(self):
        return [row['value'] for row in self.rows if row['selected']]


class ChoiceFilter(QSortFilterProxyModel):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.text = ''
        self.setSourceModel(source)

    def search(self, text):
        self.beginFilterChange()
        self.text = text.strip().casefold()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row, parent):
        value = self.sourceModel().rows[row]
        return self.text in f'{value["value"]} {value["label"]} {value["group"]}'.casefold()
