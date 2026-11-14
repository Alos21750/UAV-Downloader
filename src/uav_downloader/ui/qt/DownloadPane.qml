import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var style
    property string destination: "download"
    property alias urlInput: urls
    signal destinationEdited(string path)
    signal chooseDirectoryRequested()
    signal importRequested()
    signal exportRequested()
    signal subtitleRequested()
    signal cancelRequested()
    signal clearRequested()
    spacing: 0
    enabled: controller.ready && !controller.closing

    component Entry: TextField {
        Layout.fillWidth: true
        Layout.preferredHeight: 38
        selectByMouse: true
        background: Rectangle { radius: 8; color: pane.style.input; border.color: pane.style.border }
    }
    Rectangle {
        Layout.fillWidth: true; Layout.preferredHeight: 105
        color: pane.style.section
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 14; anchors.leftMargin: 20; anchors.rightMargin: 20; spacing: 5
            RowLayout {
                Label { text: i18n.strings.save_location; color: pane.style.secondary; Layout.preferredWidth: 86 }
                Entry { id: folder; objectName: "destinationInput"; text: pane.destination; onTextEdited: pane.destinationEdited(text) }
                ActionButton { text: i18n.strings.browse_folder; Layout.preferredWidth: 72; Layout.preferredHeight: 38; onClicked: pane.chooseDirectoryRequested() }
                ActionButton { objectName: "openDownloadDirectoryButton"; text: i18n.strings.open_btn; Layout.preferredWidth: 60; Layout.preferredHeight: 38; onClicked: controller.openFolder(folder.text) }
            }
            RowLayout {
                Label { text: i18n.strings.url_label; color: pane.style.secondary; Layout.preferredWidth: 86 }
                Entry { id: urls; objectName: "urlInput"; font.family: "Consolas" }
            }
        }
    }
    Rectangle {
        Layout.fillWidth: true; Layout.preferredHeight: 106
        color: pane.style.header
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; anchors.leftMargin: 16; anchors.rightMargin: 16
            spacing: 0
            RowLayout {
                spacing: 8
                ActionButton { objectName: "downloadInputButton"; text: i18n.strings.download_btn; highlighted: true; Layout.preferredWidth: 112; Layout.preferredHeight: 38; onClicked: controller.downloadLink(urls.text, folder.text) }
                ActionButton { objectName: "downloadAllButton"; text: i18n.strings.download_all_btn; Layout.preferredWidth: 126; Layout.preferredHeight: 38; onClicked: controller.downloadAll(folder.text, urls.text) }
                Item { Layout.fillWidth: true }
                Label { text: i18n.strings.speed_limit; color: pane.style.muted }
                ChoiceBox {
                    model: ["0", "1", "2", "5", "10", "15"]
                    labels: ({ "0": i18n.strings.unlimited, "1": "1 MB/s", "2": "2 MB/s", "5": "5 MB/s", "10": "10 MB/s", "15": "15 MB/s" })
                    currentIndex: model.indexOf(String(settings.values.speed_limit_mbps || 0))
                    Layout.preferredWidth: 118; Layout.preferredHeight: 38
                    enabled: !settings.busy
                    onActivated: settings.save({speed_limit_mbps: Number(currentText)})
                }
                Item { Layout.fillWidth: true }
                ActionButton { text: i18n.strings.pause_all; Layout.preferredHeight: 38; onClicked: controller.pauseAll() }
                ActionButton { text: i18n.strings.cancel_all; Layout.preferredHeight: 38; onClicked: pane.cancelRequested() }
                ActionButton { text: i18n.strings.clear_list; Layout.preferredHeight: 38; onClicked: pane.clearRequested() }
            }
            RowLayout {
                spacing: 8
                ActionButton { text: i18n.strings.subtitle_local_files; Layout.preferredHeight: 38; onClicked: pane.subtitleRequested() }
                ActionButton { text: i18n.strings.import_urls; Layout.preferredHeight: 38; onClicked: pane.importRequested() }
                ActionButton { text: i18n.strings.paste_urls; Layout.preferredHeight: 38; onClicked: controller.pasteUrls(folder.text) }
                ActionButton { text: i18n.strings.export_queue; Layout.preferredHeight: 38; onClicked: pane.exportRequested() }
                Item { Layout.fillWidth: true }
            }
        }
    }
    Rectangle {
        Layout.fillWidth: true; Layout.preferredHeight: 48
        color: pane.style.section
        RowLayout {
            anchors.left: parent.left; anchors.leftMargin: 16; anchors.verticalCenter: parent.verticalCenter
            spacing: 1
            Repeater {
                model: ["all", "active", "queued", "failed", "done", "paused"]
                ActionButton {
                    required property string modelData
                    text: i18n.strings["queue_filter_" + modelData]
                    highlighted: taskList.filter === modelData
                    implicitHeight: 32
                    onClicked: { taskList.filter = modelData; controller.setFilter(modelData); taskList.positionViewAtBeginning() }
                }
            }
        }
    }
    ListView {
        id: taskList; objectName: "taskList"
        property string filter: "all"
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
        clip: true; model: tasks; reuseItems: true; cacheBuffer: 180
        ScrollBar.vertical: ScrollBar {}
        delegate: Item {
            id: row
            required property var model
            width: taskList.width; height: 90
            Rectangle {
                anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                anchors.topMargin: 7; anchors.bottomMargin: 7
                color: pane.style.card; border.color: pane.style.cardBorder; radius: 10
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 8
                    Rectangle {
                        readonly property bool failing: ["未完成", "封鎖/解析失敗", "網址錯誤", "已取消"].indexOf(row.model.state) >= 0
                        readonly property bool finished: ["已下載", "未偵測到日語語音"].indexOf(row.model.state) >= 0
                        readonly property bool waiting: ["准备中", "準備中", "等待中", "等待重試", "已排程", "已暫停"].indexOf(row.model.state) >= 0
                        Layout.preferredWidth: row.model.state === "未偵測到日語語音" ? 210 : 92
                        Layout.preferredHeight: 32; radius: 6
                        color: pane.style.color(failing ? "ERROR_DIM" : finished ? "SUCCESS_DIM" : waiting ? "WARNING_DIM" : "ACCENT_DIM")
                        Label {
                            anchors.fill: parent; anchors.margins: 3
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                            text: i18n.states[row.model.state] || row.model.state || "—"
                            elide: Text.ElideRight; font.pixelSize: 13; font.bold: true
                            color: pane.style.color(parent.failing ? "ERROR_C" : parent.finished ? "SUCCESS" : parent.waiting ? "WARNING" : "ACCENT")
                        }
                    }
                    Rectangle {
                        Layout.preferredWidth: 88; Layout.preferredHeight: 50
                        color: pane.style.sidebar; radius: 6
                        Image {
                            anchors.fill: parent; asynchronous: true; fillMode: Image.PreserveAspectFit
                            source: browser.thumbnailSource(row.model.site, row.model.thumbnail)
                            sourceSize.width: 88; sourceSize.height: 50
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
                        Label { text: row.model.name || row.model.url; font.pixelSize: 14; font.bold: true; Layout.fillWidth: true; elide: Text.ElideRight; textFormat: Text.PlainText }
                        Label {
                            text: row.model.error || i18n.strings.job_details.replace("{site}", row.model.site).replace("{code}", row.model.code).replace("{resolution}", row.model.resolution)
                            color: pane.style.muted; font.family: "Consolas"; font.pixelSize: 12
                            Layout.fillWidth: true; elide: Text.ElideRight; textFormat: Text.PlainText
                            TapHandler { onTapped: controller.copyDetails(row.model.url) }
                        }
                    }
                    TaskTransfer { task: row.model }
                    ActionButton {
                        objectName: "taskActionButton-" + row.model.url
                        visible: !!row.model.action
                        text: "↻ " + i18n.strings[row.model.action === "pause" ? "pause_short" : row.model.action === "resume" ? "resume_short" : "retry_short"]
                        Layout.preferredWidth: 82; Layout.preferredHeight: 32
                        onClicked: controller.retry(row.model.url)
                    }
                    ActionButton { text: "▣"; Accessible.name: i18n.strings.open_btn; Layout.preferredWidth: 32; Layout.preferredHeight: 32; onClicked: controller.openFolder(row.model.dest || pane.destination) }
                    ActionButton { objectName: "removeTaskButton-" + row.model.url; text: "✕"; Accessible.name: i18n.strings.qt_remove; Layout.preferredWidth: 32; Layout.preferredHeight: 32; onClicked: controller.remove(row.model.url) }
                }
            }
        }
        footer: Label {
            width: taskList.width; topPadding: 7; bottomPadding: 16
            visible: controller.hiddenTaskCount > 0
            text: i18n.strings.dl_list_more_not_shown.replace("{n}", controller.hiddenTaskCount)
            color: pane.style.muted; horizontalAlignment: Text.AlignHCenter
        }
        Label {
            anchors.horizontalCenter: parent.horizontalCenter; y: 40
            visible: taskList.count === 0
            text: i18n.strings[controller.hasTasks ? "queue_filter_empty" : "dl_list_empty"]
            color: pane.style.muted
        }
    }
}
