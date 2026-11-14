import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Frame {
    id: card
    property string heading: ""
    property string description: ""
    default property alias fields: body.data
    padding: 18
    Layout.fillWidth: true
    Layout.alignment: Qt.AlignTop
    background: Rectangle {
        radius: 10
        color: card.palette.button
        border.color: card.palette.mid
    }
    contentItem: ColumnLayout {
        spacing: 14
        Label { text: card.heading; font.pixelSize: 18; font.bold: true; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Label { text: card.description; visible: text.length > 0; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: card.palette.dark }
        ColumnLayout { id: body; spacing: 14; Layout.fillWidth: true }
    }
}
