import QtQuick
import QtQuick.Controls

ActionButton {
    id: button
    objectName: "themeToggleButton"
    property bool dark: false
    readonly property string mode: settings.values.theme || "system"
    helpText: i18n.strings.qt_theme_toggle + " · " + i18n.options[mode]
    text: mode === "system" ? "◐" : mode === "light" ? "☀" : "☾"
    implicitWidth: 36; implicitHeight: 36
    font.pixelSize: 18
    enabled: controller.ready && !controller.closing && !settings.busy
    Accessible.name: i18n.strings.qt_theme_toggle
    onClicked: settings.cycleTheme()
}
