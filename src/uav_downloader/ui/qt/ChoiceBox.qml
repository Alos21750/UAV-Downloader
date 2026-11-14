import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    palette.dark: palette.buttonText
    property var labels: i18n.options
    displayText: labels[currentText] || currentText
    implicitWidth: Math.max(140, labelMetrics.width + leftPadding + rightPadding + 12)
    implicitHeight: 36
    background: Rectangle {
        color: control.palette.base
        radius: 8
        border.color: control.activeFocus ? control.palette.highlight : control.palette.mid
    }
    TextMetrics { id: labelMetrics; font: control.font; text: control.displayText }
    contentItem: Text {
        text: control.displayText
        font: control.font
        color: control.enabled ? control.palette.buttonText : control.palette.placeholderText
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    ToolTip.visible: hovered
    ToolTip.text: displayText
    delegate: ItemDelegate {
        required property string modelData
        required property int index
        width: control.width
        text: control.labels[modelData] || modelData
        highlighted: control.highlightedIndex === index
    }
}
