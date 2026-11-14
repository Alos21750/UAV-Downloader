import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    readonly property int watcherWidth: Math.min(1180, Math.max(320, Screen.desktopAvailableWidth - 24))
    readonly property int watcherHeight: Math.min(780, Math.max(320, Screen.desktopAvailableHeight - 40))
    width: workspaceMode === "watcher" ? watcherWidth : 1280
    height: workspaceMode === "watcher" ? watcherHeight : 820
    minimumWidth: workspaceMode === "watcher" ? Math.min(watcherWidth, 760) : 980
    minimumHeight: workspaceMode === "watcher" ? Math.min(watcherHeight, 440) : 680
    visible: true; title: applicationTitle
    font.family: i18n.fontFamily
    property bool mayClose: false
    property bool dark: settings.values.theme === "light" ? false : settings.values.theme === "system" ? systemColors.window.hslLightness < 0.5 : true
    readonly property bool startupReady: controller.ready && !settings.busy && !needsLanguageChoice && !controller.closing
    onStartupReadyChanged: watcher.setStartupReady(startupReady)
    property string workspaceMode: "all"
    property bool needsLanguageChoice: false
    readonly property string applicationTitle: workspaceMode === "watcher" ?
        i18n.strings.st_window_title.replace("{app}", i18n.strings.st_header).replace("{version}", updates.currentVersion) :
        workspaceMode === "browser" ? "UAV Browser — UAV Downloader" : i18n.strings.qt_title
    readonly property int tabCount: workspaceMode === "watcher" ? 2 : workspaceMode === "browser" ? 3 : 4
    readonly property int browseTabIndex: 0
    readonly property int tasksTabIndex: 1
    readonly property int settingsTabIndex: 2
    readonly property int monitorTabIndex: 3
    property string downloadDestination: initialDestination
    property string statusMessage: ""
    FolderDialog {
        id: downloadFolder; objectName: "downloadDirectoryDialog"
        currentFolder: controller.folderUrl(root.downloadDestination)
        onAccepted: settings.saveDownloadDirectory(selectedFolder)
    }
    FileDialog {
        id: importDialog; objectName: "urlImportDialog"
        fileMode: FileDialog.OpenFiles
        nameFilters: ["TXT / CSV / JSON (*.txt *.csv *.json)", "All files (*)"]
        onAccepted: controller.importFiles(selectedFiles, root.downloadDestination)
    }
    FileDialog {
        id: subtitleDialog; objectName: "localSubtitleDialog"
        fileMode: FileDialog.OpenFiles
        title: i18n.strings.subtitle_local_files
        nameFilters: [i18n.strings.subtitle_local_video_filter + " (*.mp4 *.mkv *.ts *.mov *.m4v *.webm *.avi)", i18n.strings.subtitle_local_all_filter + " (*)"]
        onAccepted: controller.subtitleLocalFiles(selectedFiles)
    }
    FileDialog {
        id: exportDialog; objectName: "queueExportDialog"; fileMode: FileDialog.SaveFile
        nameFilters: ["JSON (*.json)", "CSV (*.csv)"]
        onAccepted: controller.exportQueue(selectedFile)
    }
    Dialog {
        id: cancelDialog; anchors.centerIn: parent; title: i18n.strings.cancel_all; modal: true
        width: Math.min(600, root.width - 40)
        standardButtons: Dialog.Yes | Dialog.No
        contentItem: Label { text: i18n.strings.cancel_all_confirm; wrapMode: Text.Wrap }
        onAccepted: controller.cancelAll()
    }
    SystemPalette { id: systemColors }
    OriginalStyle { id: originalStyle; dark: root.dark }
    palette.window: originalStyle.surface
    palette.windowText: originalStyle.color("TEXT_PRI")
    palette.text: palette.windowText
    palette.buttonText: palette.windowText
    palette.button: originalStyle.card
    palette.base: originalStyle.input
    palette.alternateBase: originalStyle.color("BG_CARD_HOVER")
    palette.highlight: originalStyle.active
    palette.highlightedText: "white"
    palette.dark: originalStyle.border
    palette.mid: originalStyle.color("BORDER_HOVER")
    palette.placeholderText: originalStyle.muted
    Connections {
        target: settings
        function onGroupSaved(group) { if (group === "destination") root.downloadDestination = settings.values.download_directory }
        function onSaved() {
            if (root.needsLanguageChoice && settings.values.lang) {
                root.needsLanguageChoice = false
                languageDialog.close()
            }
        }
        function onErrorOccurred(message) { errorText.text = message; errorDialog.open() }
    }
    color: originalStyle.surface
    onClosing: function(event) {
        if (!mayClose) {
            event.accepted = false
            if (!controller.closing) controller.requestClose()
        }
    }
    Connections {
        target: controller
        function onPreparingClose() { if (watchPane.item) watchPane.item.prepareClose() }
        function onErrorOccurred(message) { errorText.text = message; errorDialog.open() }
        function onCloseNeedsDecision(message) { closeText.text = message; closeDialog.open() }
        function onCloseReady() { root.mayClose = true; root.close() }
        function onInputConsumed(text) { if (downloadPane.urlInput.text === text) downloadPane.urlInput.clear() }
        function onBatchCompleted(message) { root.statusMessage = message; controller.completionAlert() }
    }
    Connections {
        target: browser
        function onErrorOccurred(message) { root.statusMessage = message }
    }
    Connections {
        target: watcher
        function onErrorOccurred(message) { errorText.text = message; errorDialog.open() }
    }
    Connections {
        target: updates
        function onErrorOccurred(message) { errorText.text = message; errorDialog.open() }
        function onPromptRequested() { if (!controller.closing && !root.needsLanguageChoice) updateDialog.open(); else root.updatePromptPending = true }
    }
    property bool updatePromptPending: false
    onNeedsLanguageChoiceChanged: if (!needsLanguageChoice && updatePromptPending && !controller.closing) { updatePromptPending = false; updateDialog.open() }
    Dialog {
        id: updateDialog; objectName: "updatePromptDialog"
        title: i18n.strings.update_prompt_title; anchors.centerIn: parent
        width: Math.min(620, root.width - 40); modal: true
        contentItem: ColumnLayout {
            Label { text: updates.status; Layout.fillWidth: true; wrapMode: Text.Wrap }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: 180; clip: true
                TextArea { text: updates.notes; textFormat: TextEdit.PlainText; readOnly: true; wrapMode: TextEdit.Wrap }
            }
            RowLayout {
                Button { text: i18n.strings.update_prompt_later; onClicked: updateDialog.close() }
                Button { text: updates.canApply ? i18n.strings.update_now_btn : i18n.strings.qt_release_page; enabled: !controller.closing; onClicked: { updateDialog.close(); if (updates.canApply) updates.download(); else Qt.openUrlExternally(updates.releaseUrl) } }
            }
        }
    }
    Dialog {
        id: languageDialog; title: i18n.strings.lang_picker_title; anchors.centerIn: parent
        width: 360; modal: true; closePolicy: Popup.NoAutoClose
        contentItem: ColumnLayout {
            Repeater {
                model: [{code: "en", label: "English"}, {code: "zh", label: "繁體中文"},
                        {code: "zh-Hans", label: "简体中文"}, {code: "ja", label: "日本語"}, {code: "th", label: "ไทย"}]
                Button {
                    required property var modelData
                    text: modelData.label; Layout.fillWidth: true
                    enabled: controller.ready && !settings.busy && !controller.closing
                    onClicked: settings.save({lang: modelData.code})
                }
            }
        }
    }
    Component.onCompleted: { if (needsLanguageChoice) languageDialog.open(); watcher.setStartupReady(startupReady) }
    Dialog {
        id: errorDialog; objectName: "taskErrorDialog"; title: i18n.strings.qt_failed; anchors.centerIn: parent
        width: Math.min(600, root.width - 40); modal: true
        contentItem: Label { id: errorText; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        footer: DialogButtonBox {
            Button { text: i18n.strings.qt_ok; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
            onAccepted: errorDialog.accept()
        }
    }
    Dialog {
        id: closeDialog; objectName: "stopCloseDialog"; title: controller.closeIssue === "stopping" ? i18n.strings.qt_stopping_title : i18n.strings.qt_close_pending; anchors.centerIn: parent
        width: 580; modal: true; closePolicy: Popup.NoAutoClose
        contentItem: ColumnLayout {
            Label { id: closeText; objectName: "closeMessage"; Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText }
            Label { objectName: "closeHelp"; text: controller.closeIssue === "saving" ? i18n.strings.qt_close_help : i18n.strings.qt_stop_close_help; Layout.fillWidth: true; wrapMode: Text.Wrap }
            RowLayout {
                Button { objectName: "closeRetryButton"; text: i18n.strings.qt_close_retry; onClicked: { closeDialog.close(); controller.requestClose() } }
                Button { objectName: "closeExitButton"; text: i18n.strings.qt_close_exit; onClicked: { closeDialog.close(); controller.discardAndClose() } }
            }
        }
    }
    Dialog {
        id: clearDialog; property bool savedQueue: false; title: i18n.strings[savedQueue ? "clear_saved_queue" : "clear_list"]; anchors.centerIn: parent
        width: Math.min(600, root.width - 40); modal: true
        contentItem: Label { text: i18n.strings[clearDialog.savedQueue ? "clear_saved_queue_confirm" : "clear_queue_confirm"]; wrapMode: Text.Wrap }
        footer: DialogButtonBox {
            Button { text: i18n.strings.qt_yes; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
            Button { text: i18n.strings.qt_no; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            onAccepted: clearDialog.accept()
            onRejected: clearDialog.reject()
        }
        onAccepted: controller.clearAll()
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 0
        DesktopHeader {
            Layout.fillWidth: true
            style: originalStyle
            monitoring: root.workspaceMode === "watcher"
            onUpdateRequested: tabs.currentIndex = root.settingsTabIndex
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: originalStyle.border }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 48
            color: originalStyle.header
            visible: root.workspaceMode !== "watcher"
            RowLayout {
                id: tabs
                objectName: "workspaceTabs"
                property int currentIndex: root.workspaceMode === "watcher" ? root.monitorTabIndex : root.browseTabIndex
                anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 18
                spacing: 14
                Component.onCompleted: controller.setTaskViewActive(currentIndex === root.tasksTabIndex)
                onCurrentIndexChanged: { controller.setTaskViewActive(currentIndex === root.tasksTabIndex); root.statusMessage = "" }
                Repeater {
                    model: ["tab_browse", "tab_download", "tab_settings", "qt_monitor"]
                    TabButton {
                        id: tabButton
                        required property int index
                        required property string modelData
                        objectName: ["browseTabButton", "tasksTabButton", "settingsTabButton", "monitorTabButton"][index]
                        visible: index < 3 || root.workspaceMode === "all"
                        text: i18n.strings[modelData]
                        checked: tabs.currentIndex === index
                        Layout.fillHeight: true
                        Layout.fillWidth: false
                        Layout.preferredWidth: 200
                        onClicked: tabs.currentIndex = index
                        contentItem: Label {
                            text: tabButton.text
                            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                            color: tabButton.checked ? root.palette.windowText : originalStyle.secondary
                            font.pixelSize: 14
                        }
                        background: Item {
                            Rectangle {
                                anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                                anchors.leftMargin: 4; height: 3; radius: 2
                                color: tabButton.checked || tabButton.activeFocus ? originalStyle.active : "transparent"
                            }
                        }
                    }
                }
                Item { Layout.fillWidth: true }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: originalStyle.border; visible: tabs.visible }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
            BrowsePane {
                id: browsePane
                anchors.fill: parent; visible: tabs.currentIndex === root.browseTabIndex
                style: originalStyle; destination: root.downloadDestination; enabled: !controller.closing
            }
            DownloadPane {
                id: downloadPane
                anchors.fill: parent; visible: tabs.currentIndex === root.tasksTabIndex
                style: originalStyle
                destination: root.downloadDestination
                onDestinationEdited: function(path) { root.downloadDestination = path }
                onChooseDirectoryRequested: downloadFolder.open()
                onImportRequested: importDialog.open()
                onExportRequested: exportDialog.open()
                onSubtitleRequested: {
                    if (!settings.values.subtitle_mode || settings.values.subtitle_mode === "none")
                        root.statusMessage = i18n.strings.subtitle_local_need_mode
                    else subtitleDialog.open()
                }
                onCancelRequested: {
                    if (controller.hasRunningWork) cancelDialog.open()
                    else controller.cancelAll()
                }
                onClearRequested: {
                    if (controller.hasTasks) { clearDialog.savedQueue = false; clearDialog.open() }
                    else controller.clearAll()
                }
            }
            SettingsPane {
                anchors.fill: parent; visible: tabs.currentIndex === root.settingsTabIndex
                style: originalStyle
                destination: root.downloadDestination
                onChooseDirectoryRequested: downloadFolder.open()
                onClearQueueRequested: { clearDialog.savedQueue = true; clearDialog.open() }
            }
            Loader {
                id: watchPane
                anchors.fill: parent
                anchors.leftMargin: 18; anchors.rightMargin: 18
                anchors.bottomMargin: 12
                active: root.workspaceMode !== "browser"; visible: tabs.currentIndex === root.monitorTabIndex
                sourceComponent: Component { WatcherPane { style: originalStyle } }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 30
            visible: root.workspaceMode !== "watcher"
            color: originalStyle.header
            Label {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left; anchors.leftMargin: 16
                anchors.right: parent.right; anchors.rightMargin: 16
                objectName: "desktopStatus"
                text: root.statusMessage || (tabs.currentIndex === root.browseTabIndex ? browsePane.statusText :
                      tabs.currentIndex === root.tasksTabIndex ? controller.statusMessage || controller.queueStatus : "") || i18n.strings.status_ready
                color: originalStyle.secondary; font.family: "Consolas"; font.pixelSize: 13
                elide: Text.ElideRight; textFormat: Text.PlainText
            }
        }
    }
}
