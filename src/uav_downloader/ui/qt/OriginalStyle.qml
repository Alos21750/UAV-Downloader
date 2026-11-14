import QtQuick

QtObject {
    property bool dark: false
    readonly property var colors: i18n.desktopColors
    function color(name) { return colors[name][dark ? 1 : 0] }
    readonly property color surface: color("BG_DARK")
    readonly property color header: color("BG_HEADER")
    readonly property color section: color("BG_SECTION")
    readonly property color sidebar: color("BG_SIDEBAR")
    readonly property color card: color("BG_CARD")
    readonly property color input: color("BG_INPUT")
    readonly property color active: color("ACCENT")
    readonly property color activeFill: color("ACCENT_DIM")
    readonly property color border: color("BORDER")
    readonly property color cardBorder: color("BORDER_CARD")
    readonly property color secondary: color("TEXT_SEC")
    readonly property color muted: color("TEXT_DIM")
}
