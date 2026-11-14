"""Collect the QML modules used by UAV, using PyInstaller's pinned Qt hook.

The upstream hook collects every installed QML plugin, including WebEngine,
Charts and 3D components that this application never imports.
"""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()
qml_root = Path(pyside6_library_info.location['QmlImportsPath']).resolve()
subtrees = ('QtQml/Models', 'QtQml/WorkerScript', 'QtCore', 'QtQuick/Controls', 'QtQuick/Templates',
            'QtQuick/Layouts', 'QtQuick/Dialogs', 'QtQuick/Window',
            'Qt/labs/folderlistmodel', 'Qt/labs/platform', 'Qt/labs/settings')


def used_qml(entry):
    relative = Path(entry[0]).resolve().relative_to(qml_root).as_posix()
    return (Path(relative).parent.as_posix() in {'QtQuick', 'QtQml'}
            or any(relative == root or relative.startswith(root + '/') for root in subtrees))


binaries += [entry for entry in qml_binaries if used_qml(entry)]
datas += [entry for entry in qml_datas if used_qml(entry)]
