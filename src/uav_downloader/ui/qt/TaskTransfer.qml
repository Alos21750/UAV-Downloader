import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: metrics
    required property var task
    readonly property bool showProgress: !!task && task.progress > 0 &&
        ["下載中", "字幕準備中", "字幕模型準備中", "字幕辨識中", "字幕翻譯中"].indexOf(task.state) >= 0
    spacing: 4
    ProgressBar {
        id: bar
        objectName: "taskProgressBar"
        visible: metrics.showProgress
        Layout.preferredWidth: 150; Layout.preferredHeight: 8
        value: metrics.task ? metrics.task.progress / 100 : 0
        background: Rectangle { radius: 4; color: bar.palette.base }
        contentItem: Item {
            Rectangle {
                width: parent.width * bar.visualPosition
                height: parent.height
                radius: 4
                color: bar.palette.highlight
            }
        }
    }
    Label {
        visible: metrics.showProgress
        text: metrics.task ? metrics.task.progress + "%" : ""
        color: palette.placeholderText; font.family: "Consolas"; Layout.preferredWidth: 46
    }
    Label {
        text: metrics.task ? metrics.task.speed || "" : ""
        visible: !!text
        color: palette.placeholderText; font.family: "Consolas"; Layout.preferredWidth: 76
        elide: Text.ElideRight
    }
}
