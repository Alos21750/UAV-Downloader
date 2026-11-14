import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: calendar
    objectName: "watchCalendarDialog"
    title: i18n.strings.st_calendar_title
    parent: Overlay.overlay; anchors.centerIn: parent
    modal: true; width: 420
    property var targetField: null
    property string stagedDate: ""
    property var monthData: ({title: "", days: []})
    function refresh() { if (year.value >= 1 && month.value >= 1) monthData = watcher.calendarMonth(year.value, month.value) }
    function openFor(field) {
        targetField = field
        const seed = watcher.calendarSeed(field.text)
        stagedDate = seed.selected
        year.value = seed.year; month.value = seed.month
        refresh(); open()
    }
    function shift(delta) {
        let m = month.value + delta
        if (m < 1 && year.value > 1) { year.value--; m = 12 }
        if (m > 12 && year.value < 9999) { year.value++; m = 1 }
        month.value = Math.max(1, Math.min(12, m))
        refresh()
    }
    contentItem: ColumnLayout {
        RowLayout {
            Button { text: "‹"; implicitWidth: 36; Layout.maximumWidth: 36; onClicked: calendar.shift(-1) }
            SpinBox { id: year; objectName: "calendarYear"; from: 1; to: 9999; editable: true; Layout.preferredWidth: 145; textFromValue: function(value, locale) { return String(value) }; onValueModified: calendar.refresh() }
            SpinBox { id: month; objectName: "calendarMonth"; from: 1; to: 12; editable: true; Layout.preferredWidth: 115; onValueModified: calendar.refresh() }
            Button { text: "›"; implicitWidth: 36; Layout.maximumWidth: 36; onClicked: calendar.shift(1) }
        }
        GridLayout {
            columns: 7; Layout.fillWidth: true
            Repeater {
                model: i18n.strings.qt_weekdays.split(",")
                Label { required property string modelData; text: modelData; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
            }
            Repeater {
                model: calendar.monthData.days
                Button {
                    required property var modelData
                    objectName: "calendarDay-" + modelData.day
                    text: modelData.day ? String(modelData.day) : ""
                    enabled: modelData.day > 0; highlighted: modelData.value === calendar.stagedDate
                    Layout.fillWidth: true; implicitWidth: 42
                    onClicked: calendar.stagedDate = modelData.value
                }
            }
        }
        Label { text: calendar.stagedDate }
    }
    footer: DialogButtonBox {
        Button { text: i18n.strings.st_calendar_cancel; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        Button { objectName: "calendarAcceptButton"; text: i18n.strings.qt_ok; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        onAccepted: calendar.accept()
        onRejected: calendar.reject()
    }
    onAccepted: if (targetField) targetField.text = stagedDate
}
