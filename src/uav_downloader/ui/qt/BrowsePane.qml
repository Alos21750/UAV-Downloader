import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    required property var style
    property string destination: "download"
    property bool dark: style.dark
    readonly property bool canSubmit: !browser.scanning && !controller.submittingBatch && controller.ready
    readonly property string statusText: browser.loading ? i18n.strings.loading_browse :
        browser.status === "error" ? browser.loadError :
        browser.selectionMessage || controller.summary || i18n.strings.status_ready
    spacing: 0
    function review(start) {
        if (browser.selectedCount > 0) {
            selectionReview.startDownload = start
            selectionReview.open()
        }
    }
    SelectionReview { id: selectionReview; destination: pane.destination }
    Dialog {
        id: filtersDialog; objectName: "hanimeFiltersDialog"
        parent: Overlay.overlay; anchors.centerIn: parent
        title: i18n.strings.hanime_filter_title; modal: true
        width: Math.min(850, parent.width - 40); height: Math.min(650, parent.height - 40)
        onOpened: { browser.openFilters(); filtersForm.restore() }
        onRejected: browser.cancelFilters()
        contentItem: ColumnLayout {
            RowLayout {
                visible: browser.catalogLoading
                BusyIndicator { running: browser.catalogLoading; palette.dark: palette.highlight; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                Label { text: i18n.strings.qt_catalog_loading; Layout.fillWidth: true; wrapMode: Text.Wrap }
            }
            HanimeFilters { id: filtersForm; Layout.fillWidth: true; Layout.fillHeight: true }
        }
        footer: DialogButtonBox {
            Button { text: i18n.strings.hanime_filter_apply; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
            Button { text: i18n.strings.st_calendar_cancel; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            onAccepted: { browser.applyFilters(filtersForm.values()); filtersDialog.close() }
            onRejected: filtersDialog.reject()
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 112
        color: pane.style.section
        ColumnLayout {
            anchors.fill: parent; anchors.leftMargin: 20; anchors.rightMargin: 20
            anchors.topMargin: 10; anchors.bottomMargin: 16; spacing: 6
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                RowLayout {
                    spacing: 1
                    Repeater {
                        model: browser.siteNames
                        ActionButton {
                            required property string modelData
                            objectName: "browseSite-" + modelData
                            text: modelData; highlighted: browser.site === modelData
                            Layout.preferredHeight: 36
                            onClicked: if (browser.site !== modelData) { query.clear(); browser.loadSite(modelData) }
                        }
                    }
                }
                ComboBox {
                    id: category; objectName: "browseCategoryChoice"
                    Layout.preferredWidth: 190; Layout.preferredHeight: 36
                    model: browser.categories; textRole: "name"
                    currentIndex: browser.categoryIndex
                    displayText: currentIndex >= 0 ? currentText : i18n.strings.loading_browse
                    enabled: !browser.loading && count > 0
                    onActivated: { query.clear(); browser.chooseCategory(currentIndex) }
                    background: Rectangle { radius: 8; color: pane.style.input; border.color: pane.style.border }
                }
                ActionButton {
                    text: i18n.strings.hanime_filter_button
                    visible: browser.site === "Hanime1"
                    onClicked: filtersDialog.open()
                }
                TextField {
                    id: query; objectName: "browseSearchInput"
                    Layout.fillWidth: true; Layout.minimumWidth: 40; Layout.preferredHeight: 36
                    placeholderText: i18n.strings.search_placeholder
                    onAccepted: browser.search(text)
                    background: Rectangle { radius: 8; color: pane.style.input; border.color: pane.style.border }
                }
                ActionButton {
                    objectName: "browseSearchButton"; text: i18n.strings.search_btn
                    highlighted: true; Layout.preferredWidth: 76; Layout.preferredHeight: 36
                    onClicked: browser.search(query.text)
                }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Label { objectName: "browseSelectedCount"; text: browser.selectedCount + " " + i18n.strings.selected; color: pane.style.secondary }
                Item { Layout.fillWidth: true }
                ActionButton { objectName: "clearSelectionButton"; text: i18n.strings.clear_selection_btn; onClicked: browser.clearSelection() }
                ActionButton { objectName: "selectPageButton"; text: i18n.strings.select_page_btn; enabled: !browser.loading && !browser.scanning; onClicked: browser.selectAll(true) }
                ActionButton { objectName: "selectAllResultsButton"; text: i18n.strings.select_all_results_btn; enabled: !browser.loading && !browser.scanning; onClicked: browser.selectAllResults() }
                ActionButton { objectName: "browseQueueButton"; text: i18n.strings.add_to_queue; enabled: pane.canSubmit; onClicked: pane.review(false) }
                ActionButton { objectName: "browseDownloadButton"; text: i18n.strings.download_selected; highlighted: true; enabled: pane.canSubmit; onClicked: pane.review(true) }
            }
            Item { Layout.fillHeight: true }
        }
    }
    RowLayout {
        Layout.fillWidth: true; Layout.fillHeight: true
        spacing: 8
        Rectangle {
            Layout.preferredWidth: 192; Layout.fillHeight: true
            color: pane.style.sidebar
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14
                spacing: 12
                Label { text: i18n.strings.sidebar_title; color: pane.style.active; font.bold: true }
                Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: pane.style.border }
                Label { visible: browser.site !== "JableTV"; text: i18n.strings.tags_jable_only; wrapMode: Text.Wrap; Layout.fillWidth: true }
                ListView {
                    id: tags
                    Layout.fillHeight: true; Layout.fillWidth: true
                    model: browser.site === "JableTV" ? browser.sidebarTags : []; clip: true
                    property var expanded: ({})
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Column {
                        id: tagGroup
                        required property var modelData
                        width: tags.width
                        readonly property bool opened: !!tags.expanded[modelData.key]
                        ItemDelegate {
                            width: parent.width; height: 32
                            text: (tagGroup.opened ? "▾ " : "▸ ") +
                                  (i18n.tagGroups[tagGroup.modelData.key] || tagGroup.modelData.key) +
                                  " (" + tagGroup.modelData.tags.length + ")"
                            font.pixelSize: 12
                            onClicked: {
                                const state = Object.assign({}, tags.expanded)
                                state[tagGroup.modelData.key] = !tagGroup.opened
                                tags.expanded = state
                            }
                        }
                        Loader {
                            active: tagGroup.opened
                            width: parent.width
                            sourceComponent: Column {
                                Repeater {
                                    model: tagGroup.modelData.tags
                                    ItemDelegate {
                                        required property var modelData
                                        width: tagGroup.width; height: 28
                                        leftPadding: 14; font.pixelSize: 12
                                        text: modelData.label
                                        onClicked: browser.openSidebarTag(modelData.url)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        GridView {
            id: cards; objectName: "browseCards"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
            Layout.leftMargin: 7; Layout.rightMargin: 31; Layout.topMargin: 7
            clip: true; model: videos; reuseItems: true; cacheBuffer: 160
            cellWidth: width / (pane.Window.width >= 1500 ? 4 : pane.Window.width >= 1080 ? 3 : 2)
            cellHeight: 294
            ScrollBar.vertical: ScrollBar {}
            delegate: Item {
                id: cell
                required property int index
                required property string title
                required property string url
                required property string duration
                required property string imageSource
                required property bool selected
                required property string versionBadge
                width: cards.cellWidth; height: cards.cellHeight
                Rectangle {
                    anchors.fill: parent; anchors.margins: 7
                    radius: 10
                    color: cell.selected ? pane.style.activeFill : pane.style.card
                    border.color: cell.selected ? pane.style.active : pane.style.cardBorder
                    border.width: cell.selected ? 2 : 1
                    TapHandler { onTapped: browser.select(cell.index, !cell.selected) }
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 10
                        spacing: 6
                        Rectangle {
                            Layout.fillWidth: true; Layout.preferredHeight: 169
                            Layout.alignment: Qt.AlignTop
                            color: pane.style.sidebar; radius: 6
                            Image {
                                id: cover
                                anchors.fill: parent
                                source: cell.imageSource; asynchronous: true; fillMode: Image.PreserveAspectFit
                                sourceSize.width: 300; sourceSize.height: 169
                            }
                            Label {
                                anchors.centerIn: parent
                                visible: !cell.imageSource || cover.status === Image.Error
                                text: i18n.strings.no_thumbnail
                                color: pane.style.muted
                            }
                            Rectangle {
                                visible: !!cell.duration
                                anchors.bottom: parent.bottom; anchors.right: parent.right
                                anchors.margins: 5
                                color: "#cc000000"; radius: 4
                                width: durationLabel.implicitWidth + 12; height: 23
                                Label { id: durationLabel; anchors.centerIn: parent; text: cell.duration; color: "white"; font.family: "Consolas"; font.bold: true }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            Label {
                                visible: !!cell.versionBadge
                                text: cell.versionBadge === "uncensored" ? i18n.strings.qt_badge_uncensored : i18n.strings.qt_badge_chinese
                                color: pane.style.active; font.pixelSize: 11; font.bold: true
                            }
                            Label {
                                objectName: "browseCardTitle"
                                text: cell.title; textFormat: Text.PlainText; font.pixelSize: 13
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                                Layout.alignment: Qt.AlignTop
                            }
                        }
                        RowLayout {
                            Item { Layout.fillWidth: true }
                            ActionButton { objectName: "browseOpenCardButton"; text: i18n.strings.preview_browser; Layout.preferredHeight: 30; onClicked: browser.preview(cell.url) }
                            ActionButton { text: (cell.selected ? "✓ " : "") + i18n.strings[cell.selected ? "selected" : "select"]; highlighted: cell.selected; Layout.preferredWidth: 76; Layout.preferredHeight: 30; onClicked: browser.select(cell.index, !cell.selected) }
                        }
                    }
                }
            }
        }
    }
    Rectangle {
        Layout.fillWidth: true; Layout.preferredHeight: 44
        color: pane.style.header
        RowLayout {
            anchors.centerIn: parent; spacing: 7
            ActionButton { text: i18n.strings.first_page; enabled: browser.page > 1 && !browser.loading; onClicked: browser.jumpPage(1) }
            ActionButton { objectName: "previousPageButton"; text: i18n.strings.prev_page; enabled: browser.page > 1 && !browser.loading; onClicked: browser.turnPage(-1) }
            Label { text: i18n.strings.page_n.replace("{n}", browser.page); horizontalAlignment: Text.AlignHCenter; Layout.preferredWidth: 80 }
            ActionButton { objectName: "nextPageButton"; text: i18n.strings.next_page; highlighted: true; enabled: !browser.loading && browser.hasNext; onClicked: browser.turnPage(1) }
            TextField {
                id: jump; objectName: "jumpPageInput"
                Layout.preferredWidth: 50; Layout.preferredHeight: 30
                validator: IntValidator { bottom: 1; top: 1000000 }
                onAccepted: if (acceptableInput) browser.jumpPage(Number(text))
            }
            ActionButton { text: i18n.strings.qt_go_page; enabled: jump.acceptableInput; onClicked: browser.jumpPage(Number(jump.text)) }
        }
    }
}
