"""Exercise the production QML binding in an isolated Qt process."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_download_progress_uses_original_visibility_without_indeterminate_animation():
    pytest.importorskip('PySide6')
    source = Path(__file__).resolve().parents[1] / 'src'
    code = '''
import sys
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from pathlib import Path

QQuickStyle.setStyle('Basic')
app = QGuiApplication([])
engine = QQmlEngine()
component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(sys.argv[1]) / 'uav_downloader/ui/qt/TaskTransfer.qml')))
assert not component.isError(), component.errors()
root = component.createWithInitialProperties({'task': {'state': '等待中', 'progress': 0, 'speed': ''}})
assert root is not None, component.errors()
bar = root.findChild(QObject, 'taskProgressBar')
for state, progress, expected in [('下載中', 0, False), ('下載中', 50, True),
                                   ('已下載', 100, False), ('字幕辨識中', 60, True)]:
    root.setProperty('task', dict(state=state, progress=progress, speed=''))
    app.processEvents()
    assert bar.property('visible') == expected, (state, progress)
    assert not bar.property('indeterminate')
    assert abs(bar.property('value') - progress / 100) < .001
del root
del component
del engine
'''
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', code, str(source)], env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
