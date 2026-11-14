import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: row
    required property string label
    property string description: ""
    default property alias controls: input.data
    Layout.fillWidth: true
    spacing: 4
    RowLayout {
        Layout.fillWidth: true; spacing: 12
        Label { text: row.label; Layout.preferredWidth: 174; wrapMode: Text.Wrap }
        RowLayout { id: input; Layout.fillWidth: true }
    }
    Label {
        visible: !!row.description; text: row.description
        color: palette.placeholderText; font.pixelSize: 12
        Layout.fillWidth: true; wrapMode: Text.Wrap
    }
}
