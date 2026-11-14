import QtQuick
import QtQuick.Controls

Button {
    id: action
    property string helpText: ""
    implicitHeight: 34
    implicitWidth: Math.max(52, contentItem.implicitWidth + leftPadding + rightPadding)
    padding: 9
    font.pixelSize: 13
    contentItem: Text {
        text: action.text
        font: action.font
        color: !action.enabled ? action.palette.placeholderText :
               action.highlighted ? "white" : action.palette.buttonText
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 8
        color: action.highlighted && action.enabled ? action.palette.highlight :
               action.down || action.hovered ? action.palette.alternateBase : "transparent"
        border.width: action.highlighted && action.enabled ? 0 : 1
        border.color: action.activeFocus ? action.palette.highlight : action.palette.mid
    }
    Accessible.description: helpText
    ToolTip {
        id: helpTip
        objectName: action.objectName + "-tooltip"
        text: action.helpText
        visible: text.length > 0 && (action.hovered || (action.activeFocus && action.focusReason !== Qt.MouseFocusReason))
        delay: 500
        width: Math.min(360, implicitWidth)
        contentItem: Label { text: helpTip.text; font: helpTip.font; color: helpTip.palette.toolTipText; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    }
}
