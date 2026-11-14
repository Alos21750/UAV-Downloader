import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: review
    property string destination: "download"
    property bool startDownload: false
    property var excluded: ({})
    property var rowsToReview: []
    function submit(start) {
        browser.submitReviewedSelection(destination, start,
            {resolution: resolution.currentText, filename_template: template.text},
            Object.keys(excluded))
        close()
    }
    objectName: "selectionReviewDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    width: Math.min(760, parent.width - 32)
    height: Math.min(620, parent.height - 32)
    title: i18n.strings.selection_preview_title
    onOpened: {
        excluded = {}
        rowsToReview = browser.reviewRows()
        resolution.currentIndex = resolution.model.indexOf(settings.values.resolution || "highest")
        template.text = settings.values.filename_template || "{title}"
    }
    contentItem: ColumnLayout {
        Label { text: i18n.strings.selection_preview_desc.replace("{count}", browser.selectedCount); wrapMode: Text.Wrap; Layout.fillWidth: true }
        RowLayout {
            Label { text: i18n.strings.selection_preview_resolution; Layout.preferredWidth: 130 }
            ChoiceBox { id: resolution; model: ["highest", "1080", "720", "480", "360", "lowest"]; Layout.fillWidth: true }
        }
        RowLayout {
            Label { text: i18n.strings.selection_preview_template; Layout.preferredWidth: 130 }
            TextField { id: template; placeholderText: "{title} · {code} · {site}"; Layout.fillWidth: true }
        }
        Label { text: i18n.strings.filename_template_desc; wrapMode: Text.Wrap; Layout.fillWidth: true }
        ListView {
            id: rows; model: review.rowsToReview
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; reuseItems: true
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: reviewRow
                required property var modelData
                width: rows.width - 16
                height: 50; radius: 8
                color: review.palette.button; border.color: review.palette.mid
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                    CheckBox {
                        Accessible.name: reviewRow.modelData.title
                        checked: !review.excluded[reviewRow.modelData.url]
                        onClicked: {
                            const draft = Object.assign({}, review.excluded)
                            if (checked) delete draft[reviewRow.modelData.url]
                            else draft[reviewRow.modelData.url] = true
                            review.excluded = draft
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 2
                        Label { text: reviewRow.modelData.title; font.bold: true; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label { text: reviewRow.modelData.details; font.family: "Consolas"; font.pixelSize: 11; color: review.palette.placeholderText; textFormat: Text.PlainText; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                }
            }
        }
        Label { visible: browser.selectedCount > 200; text: i18n.strings.selection_preview_more.replace("{count}", browser.selectedCount - 200) }
    }
    footer: RowLayout {
        ActionButton { objectName: "reviewCancelButton"; text: i18n.strings.selection_preview_cancel; onClicked: review.close() }
        Item { Layout.fillWidth: true }
        Repeater {
            model: [0, 1]
            ActionButton {
                required property int modelData
                readonly property bool starts: modelData === 1 ? review.startDownload : !review.startDownload
                objectName: starts ? "reviewDownloadButton" : "reviewQueueButton"
                text: starts ? i18n.strings.selection_preview_download : i18n.strings.selection_preview_add
                highlighted: starts === review.startDownload
                enabled: !controller.submittingBatch
                onClicked: review.submit(starts)
            }
        }
    }
}
