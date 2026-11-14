import QtQuick
import QtQuick.Controls

TextField {
    id: field
    required property string setting
    property bool numeric: false
    property bool edited: false
    implicitHeight: 34
    text: String(settings.values[setting] ?? "")
    onTextEdited: edited = true
    function commit() {
        if (!edited) return
        edited = false
        if (!acceptableInput || (numeric && !text.trim())) {
            text = String(settings.values[setting] ?? "")
            return
        }
        const value = numeric ? Number(text) : text
        if (value !== settings.values[setting]) settings.save({[setting]: value})
    }
    onEditingFinished: commit()
    onActiveFocusChanged: if (!activeFocus) commit()
    Connections {
        target: settings
        function onValuesChanged() { if (!field.edited) field.text = String(settings.values[field.setting] ?? "") }
        function onErrorOccurred() { if (!field.edited) field.text = String(settings.values[field.setting] ?? "") }
    }
    background: Rectangle { radius: 8; color: field.palette.base; border.color: field.activeFocus ? field.palette.highlight : field.palette.mid }
}
