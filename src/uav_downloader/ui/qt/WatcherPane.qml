import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ColumnLayout {
    id: pane
    required property var style
    property bool settingsExpanded: false
    property bool categoriesCollapsed: false
    property bool activityVisible: false
    property string siteName: watcher.siteNames[0]
    property var folderTarget: null
    readonly property bool running: !!watcher.state.running
    readonly property int columns: width + 36 >= 1120 ? 3 : 2
    readonly property var categories: watcher.categoryGroups
    readonly property var categoryRows: {
        let result = []
        for (const group of categories) {
            result.push({heading: true, group: group, choices: []})
            for (let index = 0; index < group.choices.length; index += columns)
                result.push({heading: false, group: group, choices: group.choices.slice(index, index + columns),
                             last: index + columns >= group.choices.length})
        }
        return result
    }
    spacing: 10

    component CategoryCheckBox: CheckBox {
        id: choiceControl
        font.pixelSize: 13
        spacing: 7
        indicator: Rectangle {
            x: choiceControl.leftPadding
            y: (choiceControl.height - height) / 2
            width: 17; height: 17; radius: 4
            color: choiceControl.checked ? pane.style.active : pane.style.input
            border.color: choiceControl.activeFocus ? pane.style.active : pane.style.border
            Label { anchors.centerIn: parent; visible: choiceControl.checked; text: "✓"; color: "white"; font.pixelSize: 12 }
        }
    }

    function values() {
        return {output_folder: output.text.trim(), baseline_date: baseline.text.trim()}
    }
    function prepareClose() {
        const patch = values()
        if (!patch.output_folder) delete patch.output_folder
        if (!watcher.validBaseline(patch.baseline_date)) delete patch.baseline_date
        watcher.prepareClose(patch)
    }
    function chooseFolder(target) {
        folderTarget = target
        folderDialog.currentFolder = controller.folderUrl(target.text)
        folderDialog.open()
    }
    function filterCategories() { watcher.setFilter(siteName, query.text) }
    function chooseSite(site) {
        siteName = site
        filterDelay.stop()
        filterCategories()
        targetList.positionViewAtBeginning()
    }
    function syncDraft() {
        if (!output.activeFocus) output.text = watcher.config.output_folder || ""
        if (!baseline.activeFocus) baseline.text = watcher.config.baseline_date || ""
    }
    Connections {
        target: watcher
        function onConfigChanged() { pane.syncDraft() }
        function onLogsAppended(text) {
            logText.append(text)
            if (logText.length > 120000) logText.remove(0, logText.length - 90000)
        }
    }
    Component.onCompleted: syncDraft()

    Timer { id: filterDelay; interval: 120; onTriggered: pane.filterCategories() }
    CalendarDialog { id: calendar }
    TranslationDialog { id: translation }
    FolderDialog {
        id: folderDialog
        onAccepted: if (pane.folderTarget) {
            const path = controller.localFolder(selectedFolder)
            pane.folderTarget.text = path
            if (pane.folderTarget === output) watcher.saveSettings({output_folder: path})
        }
    }
    Dialog {
        id: scheduleDialog
        objectName: "watchScheduleDialog"
        anchors.centerIn: parent; width: Math.min(510, parent.width - 32)
        modal: true; title: i18n.strings.st_schedule
        onOpened: {
            const saved = watcher.config.scan_schedule || {}
            intervalMode.checked = saved.mode !== "daily"
            dailyMode.checked = !intervalMode.checked
            interval.text = String(saved.interval_hours || 24)
            dailyTime.text = saved.daily_time || "18:00"
        }
        contentItem: GridLayout {
            columns: 2
            RadioButton { id: intervalMode; text: i18n.strings.st_schedule_interval; autoExclusive: true }
            TextField { id: interval; enabled: intervalMode.checked; validator: IntValidator { bottom: 1; top: 168 } }
            RadioButton { id: dailyMode; text: i18n.strings.st_schedule_daily; autoExclusive: true }
            TextField { id: dailyTime; enabled: dailyMode.checked; placeholderText: "HH:MM" }
            Label { text: watcher.state.schedule + "  " + (watcher.state.timezone || ""); Layout.columnSpan: 2; Layout.fillWidth: true; wrapMode: Text.Wrap }
        }
        footer: RowLayout {
            ActionButton {
                objectName: "watchScheduleSaveButton"
                text: i18n.strings.st_schedule_save; highlighted: true
                enabled: interval.acceptableInput && !watcher.busy
                onClicked: {
                    watcher.saveSettings({scan_schedule: {mode: dailyMode.checked ? "daily" : "interval",
                        interval_hours: Number(interval.text), daily_time: dailyTime.text}})
                    scheduleDialog.close()
                }
            }
            ActionButton { text: i18n.strings.st_calendar_cancel; onClicked: scheduleDialog.close() }
        }
    }
    Dialog {
        id: overridesDialog
        objectName: "watchSiteOverrides"
        anchors.centerIn: parent; width: Math.min(820, parent.width - 32)
        modal: true; title: i18n.strings.st_site_settings_title
        onOpened: {
            for (let i = 0; i < overrides.count; ++i) {
                const row = overrides.itemAt(i)
                const value = (watcher.config.site_settings || {})[row.site] || {}
                row.folder = value.output_folder || ""
                row.date = value.baseline_date || ""
            }
        }
        contentItem: ColumnLayout {
            Label { text: i18n.strings.st_site_settings_desc; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Repeater {
                id: overrides
                model: watcher.siteNames
                delegate: RowLayout {
                    required property string modelData
                    readonly property string site: modelData
                    property alias folder: overrideFolder.text
                    property alias date: overrideDate.text
                    Label { text: modelData; Layout.preferredWidth: 85 }
                    TextField { id: overrideFolder; placeholderText: i18n.strings.st_site_use_default_folder; Layout.fillWidth: true }
                    ActionButton { text: i18n.strings.browse_folder; onClicked: pane.chooseFolder(overrideFolder) }
                    TextField { id: overrideDate; placeholderText: i18n.strings.st_site_use_default_date; Layout.preferredWidth: 135 }
                    ActionButton { text: i18n.strings.st_calendar; onClicked: calendar.openFor(overrideDate) }
                }
            }
        }
        footer: RowLayout {
            ActionButton {
                text: i18n.strings.st_site_settings_save; highlighted: true; enabled: !watcher.busy
                onClicked: {
                    const all = {}
                    for (let i = 0; i < overrides.count; ++i) {
                        const row = overrides.itemAt(i)
                        const value = {}
                        if (row.folder.trim()) value.output_folder = row.folder.trim()
                        if (row.date.trim()) value.baseline_date = row.date.trim()
                        if (Object.keys(value).length) all[row.site] = value
                    }
                    watcher.saveSettings({site_settings: all})
                    overridesDialog.close()
                }
            }
            ActionButton { text: i18n.strings.st_calendar_cancel; onClicked: overridesDialog.close() }
        }
    }
    Frame {
        Layout.fillWidth: true; Layout.topMargin: 14; padding: 12
        background: Rectangle { radius: 10; color: pane.style.card; border.color: pane.style.cardBorder }
        contentItem: ColumnLayout {
            spacing: 8
            RowLayout {
                Label { text: i18n.strings.save_location; color: pane.style.secondary; Layout.preferredWidth: 100 }
                TextField { id: output; objectName: "watchOutputFolder"; Layout.fillWidth: true; implicitHeight: 38 }
                ActionButton { text: i18n.strings.browse_folder; Layout.preferredWidth: 82; implicitHeight: 38; onClicked: pane.chooseFolder(output) }
                ActionButton {
                    objectName: "watchShowConfig"
                    text: i18n.strings[pane.settingsExpanded ? "st_settings_collapse" : "st_settings_expand"]
                    Layout.preferredWidth: 104; implicitHeight: 38
                    onClicked: pane.settingsExpanded = !pane.settingsExpanded
                }
            }
            ScrollView {
                id: configScroll
                visible: pane.settingsExpanded; clip: true
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(260, Math.max(104, pane.height * .28))
                contentWidth: availableWidth
                ColumnLayout {
                    width: configScroll.availableWidth - 8
                    spacing: 7
                    ProxySettings {}
                    RowLayout {
                        Label { text: i18n.strings.st_baseline_date }
                        TextField { id: baseline; objectName: "watchBaselineInput"; Layout.preferredWidth: 116; placeholderText: "YYYY-MM-DD" }
                        ActionButton { objectName: "watchCalendarButton"; text: i18n.strings.st_calendar; onClicked: calendar.openFor(baseline) }
                        ChoiceBox {
                            objectName: "watchDatePreset"
                            model: [i18n.strings.st_date_yesterday, i18n.strings.st_date_month_1, i18n.strings.st_date_month_2, i18n.strings.st_date_month_3, i18n.strings.st_date_month_6]
                            currentIndex: -1; displayText: currentIndex >= 0 ? currentText : i18n.strings.st_date_quick
                            onActivated: { baseline.text = watcher.datePreset(currentIndex); currentIndex = -1 }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        ActionButton { objectName: "watchOverridesButton"; text: i18n.strings.st_site_settings_button; onClicked: overridesDialog.open() }
                        Label { text: Object.keys(watcher.config.site_settings || {}).length ? i18n.strings.st_site_settings_custom_count.replace("{count}", Object.keys(watcher.config.site_settings).length) : i18n.strings.st_site_settings_default; color: pane.style.muted }
                        Item { Layout.fillWidth: true }
                    }
                    PreferenceRow {
                        label: i18n.strings.resolution_setting
                        ChoiceBox {
                            model: ["highest", "1080", "720", "480", "360", "lowest"]
                            currentIndex: Math.max(0, model.indexOf(watcher.config.resolution || "highest"))
                            onActivated: watcher.saveSettings({resolution: currentText})
                        }
                        PreferenceChoice { setting: "filename_mode"; model: ["full-title", "code-only"] }
                        Item { Layout.fillWidth: true }
                    }
                    PreferenceRow {
                        label: i18n.strings.max_workers_per_video_setting
                        PreferenceEntry { setting: "max_workers_per_video"; numeric: true; Layout.preferredWidth: 90; validator: IntValidator { bottom: 1; top: 16 } }
                        Item { Layout.fillWidth: true }
                    }
                    PreferenceRow {
                        label: i18n.strings.st_version_preference
                        ChoiceBox {
                            id: version; objectName: "watchVersionChoice"
                            model: ["chinese-subtitle", "uncensored", "standard", "english-subtitle", "reducing-mosaic", "all"]
                            labels: Object.assign({}, i18n.options, {all: i18n.strings.st_pref_all})
                            currentIndex: Math.max(0, model.indexOf(watcher.config.version_preference || ""))
                            onActivated: watcher.saveSettings({version_preference: currentText})
                        }
                        Item { Layout.fillWidth: true }
                    }
                    PreferenceRow {
                        label: i18n.strings.subtitle_setting
                        ChoiceBox {
                            model: ["none", "ja", "en", "zh", "all"]
                            currentIndex: Math.max(0, model.indexOf(watcher.config.subtitle_mode || "none"))
                            onActivated: watcher.saveSettings({subtitle_mode: currentText})
                        }
                        PreferenceChoice { setting: "recognition_quality"; model: ["auto", "quality", "balanced", "fast"] }
                        ActionButton { text: i18n.strings.translation_provider_configure; onClicked: translation.open() }
                        Item { Layout.fillWidth: true }
                    }
                }
            }
        }
    }
    Frame {
        visible: !pane.settingsExpanded
        Layout.fillWidth: true; Layout.fillHeight: !pane.categoriesCollapsed
        Layout.minimumHeight: pane.categoriesCollapsed ? 54 : 120
        padding: 12
        background: Rectangle { radius: 10; color: pane.style.card; border.color: pane.style.cardBorder }
        contentItem: ColumnLayout {
            spacing: 8
            RowLayout {
                Label { text: i18n.strings.st_select_hint; font.bold: true }
                Label { text: watcher.selectedCount + " " + i18n.strings.selected; color: pane.style.active; font.bold: true }
                Item { Layout.fillWidth: true }
                TextField {
                    id: query
                    visible: !pane.categoriesCollapsed
                    Layout.preferredWidth: 200; placeholderText: i18n.strings.st_filter_categories
                    onTextEdited: filterDelay.restart()
                }
                ActionButton {
                    text: i18n.strings[pane.categoriesCollapsed ? "st_categories_expand" : "st_categories_collapse"]
                    Layout.preferredWidth: 104
                    onClicked: pane.categoriesCollapsed = !pane.categoriesCollapsed
                }
            }
            RowLayout {
                visible: !pane.categoriesCollapsed
                Layout.alignment: Qt.AlignHCenter
                spacing: 2
                Repeater {
                    model: watcher.siteNames
                    ActionButton {
                        required property string modelData
                        text: modelData + "  " + watcher.siteCounts[modelData]
                        highlighted: pane.siteName === modelData; implicitHeight: 27
                        onClicked: pane.chooseSite(modelData)
                    }
                }
            }
            ListView {
                id: targetList; objectName: "watchTargetList"
                visible: !pane.categoriesCollapsed
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 50
                clip: true; reuseItems: true; cacheBuffer: 120
                model: visible ? pane.categoryRows : []
                ScrollBar.vertical: ScrollBar {}
                delegate: Item {
                    id: categoryRow
                    required property var modelData
                    width: targetList.width - 10
                    height: modelData.heading ? 45 : modelData.last ? 42 : 32
                    Rectangle {
                        anchors.fill: parent; anchors.bottomMargin: categoryRow.modelData.last ? 10 : 0
                        color: pane.style.section
                        radius: categoryRow.modelData.heading || categoryRow.modelData.last ? 8 : 0
                    }
                    Loader {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                        anchors.bottomMargin: categoryRow.modelData.last ? 10 : 0
                        sourceComponent: categoryRow.modelData.heading ? groupHeader : choices
                    }
                    Component {
                        id: groupHeader
                        RowLayout {
                            Label { text: categoryRow.modelData.group.label + "  " + categoryRow.modelData.group.total; font.bold: true; Layout.fillWidth: true }
                            CategoryCheckBox {
                                objectName: "watchGroup-" + categoryRow.modelData.group.id
                                text: i18n.strings.st_select_all
                                checked: watcher.categoryGroupSelection[categoryRow.modelData.group.id] === true
                                onClicked: watcher.selectGroup(categoryRow.modelData.group.id, checked)
                            }
                        }
                    }
                    Component {
                        id: choices
                        RowLayout {
                            spacing: 6
                            Repeater {
                                model: pane.columns
                                CategoryCheckBox {
                                    required property int index
                                    readonly property var choice: categoryRow.modelData.choices[index]
                                    opacity: choice ? 1 : 0; enabled: !!choice
                                    text: choice ? choice.label : ""
                                    checked: !!choice && !!watcher.categorySelection[String(choice.index)]
                                    Layout.fillWidth: true; Layout.preferredWidth: 1
                                    onClicked: if (choice) watcher.selectCategory(choice.index, checked)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    RowLayout {
        spacing: 8
        ActionButton { objectName: "watchStartButton"; text: i18n.strings.st_start; highlighted: true; Layout.preferredWidth: 142; implicitHeight: 40; enabled: !pane.running && !watcher.busy && !controller.closing; onClicked: watcher.apply(pane.values(), "monitor") }
        ActionButton { objectName: "watchStopButton"; text: i18n.strings.st_stop; Layout.preferredWidth: 92; implicitHeight: 40; enabled: pane.running && !watcher.state.stopping && !controller.closing; onClicked: watcher.stop() }
        ActionButton { objectName: "watchCheckButton"; text: i18n.strings.st_check_now; Layout.preferredWidth: 112; implicitHeight: 40; enabled: !watcher.busy && !controller.closing && (!pane.running || watcher.state.monitoring); onClicked: watcher.apply(pane.values(), "check") }
        ActionButton { objectName: "watchScheduleButton"; text: i18n.strings.st_schedule; Layout.preferredWidth: 104; implicitHeight: 40; onClicked: scheduleDialog.open() }
        ActionButton {
            text: i18n.strings[pane.activityVisible && !pane.settingsExpanded ? "st_activity_hide" : "st_activity_show"]
            Layout.preferredWidth: 108; implicitHeight: 40
            onClicked: {
                if (pane.settingsExpanded) { pane.settingsExpanded = false; pane.activityVisible = true }
                else pane.activityVisible = !pane.activityVisible
            }
        }
        Item { Layout.fillWidth: true }
        Rectangle {
            color: pane.style.color("BG_BADGE"); radius: 8; border.color: pane.style.border
            implicitWidth: status.implicitWidth + 24; implicitHeight: 40
            Label { id: status; anchors.centerIn: parent; text: watcher.state.status || ""; color: pane.style.secondary; font.bold: true }
        }
    }
    Frame {
        visible: !!watcher.state.title || !!watcher.state.scan
        Layout.fillWidth: true; padding: 12
        background: Rectangle { radius: 10; color: pane.style.card; border.color: pane.style.cardBorder }
        contentItem: ColumnLayout {
            Label { objectName: "watchActivityTitle"; text: watcher.state.title || watcher.state.scan || i18n.strings.st_progress_idle; Layout.fillWidth: true; elide: Text.ElideRight; textFormat: Text.PlainText }
            RowLayout {
                ProgressBar { objectName: "watchActivityProgress"; Layout.fillWidth: true; value: Math.max(0, watcher.state.progress || 0) / 100; indeterminate: visible && watcher.state.progress < 0 }
                Label { visible: !!watcher.state.title && watcher.state.progress >= 0; text: watcher.state.progress + "%"; color: pane.style.active }
                Label {
                    visible: watcher.state.total > 0
                    text: (watcher.state.done || 0) + " / " + watcher.state.total +
                          (watcher.state.speed > 0 ? " · " + (watcher.state.speed / 1024).toFixed(0) + " KB/s" : "")
                }
            }
        }
    }
    Frame {
        visible: pane.activityVisible && !pane.settingsExpanded
        Layout.fillWidth: true; Layout.fillHeight: pane.categoriesCollapsed; Layout.preferredHeight: 140; padding: 10
        background: Rectangle { radius: 10; color: pane.style.card; border.color: pane.style.cardBorder }
        contentItem: ColumnLayout {
            RowLayout {
                Label { text: i18n.strings.st_activity; font.bold: true; Layout.fillWidth: true }
                Label { text: (watcher.state.schedule || "") + "  " + (watcher.state.nextScan || ""); color: pane.style.secondary }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextArea { id: logText; readOnly: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; font.family: "Consolas" }
            }
        }
    }
    Item { visible: pane.settingsExpanded || (pane.categoriesCollapsed && !pane.activityVisible); Layout.fillHeight: true }
}
