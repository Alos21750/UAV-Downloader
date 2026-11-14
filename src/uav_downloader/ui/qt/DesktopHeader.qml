import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: header
    required property var style
    property bool monitoring: false
    signal updateRequested()
    color: style.header
    implicitHeight: monitoring ? 72 : 64
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: header.monitoring ? 20 : 24
        anchors.rightMargin: anchors.leftMargin
        spacing: 8
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Label { text: header.monitoring ? i18n.strings.st_header : "UAV Browser"; font.pixelSize: 19; font.bold: true }
            Label { text: header.monitoring ? i18n.strings.st_subtitle : "UAV DOWNLOADER  /  BROWSER"; color: header.style.active; font.family: "Consolas"; font.pixelSize: 10; font.bold: true }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            Layout.fillWidth: false
            spacing: 8
            ChoiceBox {
                objectName: "headerLanguageChoice"
                model: ["en", "zh", "zh-Hans", "ja", "th"]
                labels: ({"en":"English", "zh":"繁體中文", "zh-Hans":"简体中文", "ja":"日本語", "th":"ไทย"})
                currentIndex: model.indexOf(i18n.language)
                Layout.preferredWidth: 126
                Layout.fillWidth: false
                Layout.preferredHeight: 36
                enabled: controller.ready && !settings.busy && !controller.closing
                onActivated: settings.save({lang: currentText})
            }
            ThemeButton { Layout.fillWidth: false; Layout.preferredWidth: 36 }
            Rectangle {
                color: header.style.color("BG_BADGE")
                border.color: header.style.border
                radius: 6
                implicitWidth: versionText.implicitWidth + 20
                implicitHeight: 36
                Layout.fillWidth: false
                Label {
                    id: versionText
                    anchors.centerIn: parent
                    text: "v" + updates.currentVersion + (!header.monitoring && updates.available ? "  " + i18n.strings.update_new_badge : "")
                    color: header.style.secondary
                    font.family: "Consolas"; font.pixelSize: 13; font.bold: true
                }
                TapHandler { enabled: !header.monitoring && updates.available; onTapped: header.updateRequested() }
            }
        }
    }
}
