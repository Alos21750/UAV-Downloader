import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: pane
    spacing: 8
    property var draftBeforeRefresh: null
    Connections {
        target: browser
        function onCatalogChanging() { pane.draftBeforeRefresh = pane.values() }
        function onCatalogChanged() {
            const draft = pane.draftBeforeRefresh
            if (draft) Qt.callLater(function() { pane.restoreChoices(draft) })
        }
    }
    function choose(control, value) {
        control.currentIndex = Math.max(0, control.model.findIndex(row => row.value === value))
    }
    function restore() {
        const value = browser.filters
        query.text = value.query || ""
        restoreChoices(value)
        tagSearch.clear()
    }
    function restoreChoices(value) {
        choose(genre, value.genre); choose(sort, value.sort)
        choose(date, value.date); choose(duration, value.duration)
    }
    function values() {
        return {query: query.text, genre: genre.currentValue || "", sort: sort.currentValue || "",
            date: date.currentValue || "", duration: duration.currentValue || ""}
    }
    function clear() {
        query.clear()
        for (const control of [genre, sort, date, duration]) control.currentIndex = 0
        browser.resetDraftTags()
    }
    Label { text: i18n.strings.hanime_filter_desc; Layout.fillWidth: true; wrapMode: Text.Wrap }
    GridLayout {
        columns: 4; Layout.fillWidth: true
        Label { text: i18n.strings.hanime_filter_query }
        TextField { id: query; Layout.fillWidth: true }
        Label { text: i18n.strings.hanime_filter_genre }
        ComboBox { id: genre; objectName: "hanimeGenre"; model: browser.filterCatalog.genres; textRole: "label"; valueRole: "value"; Layout.preferredWidth: 180 }
        Label { text: i18n.strings.hanime_filter_sort }
        ComboBox { id: sort; model: browser.filterCatalog.sorts; textRole: "label"; valueRole: "value"; Layout.fillWidth: true }
        Label { text: i18n.strings.hanime_filter_date }
        ComboBox { id: date; model: browser.filterCatalog.dates; textRole: "label"; valueRole: "value"; Layout.preferredWidth: 180 }
        Label { text: i18n.strings.hanime_filter_duration }
        ComboBox { id: duration; model: browser.filterCatalog.durations; textRole: "label"; valueRole: "value"; Layout.fillWidth: true }
        Button { text: i18n.strings.hanime_filter_reset; Layout.columnSpan: 2; onClicked: pane.clear() }
    }
    RowLayout {
        Layout.fillWidth: true
        TextField { id: tagSearch; placeholderText: i18n.strings.hanime_filter_tag_search; Layout.fillWidth: true; onTextChanged: browser.filterHanimeTags(text) }
        Label { text: i18n.strings.hanime_filter_selected_tags.replace("{count}", browser.selectedTags) }
        BusyIndicator { running: browser.catalogLoading; visible: running; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
    }
    ListView {
        id: tags; objectName: "hanimeTagList"
        Layout.fillHeight: true; Layout.fillWidth: true; Layout.minimumHeight: 100
        clip: true; reuseItems: true; model: browser.hanimeTags
        ScrollBar.vertical: ScrollBar {}
        delegate: CheckBox {
            objectName: "hanimeTagRow"
            required property int index
            required property string label
            required property string group
            required property bool selected
            width: tags.width - 12
            text: (i18n.strings["st_hanime_tags_" + group] || i18n.strings.hanime_filter_other_tags) + " / " + label
            checked: selected
            onClicked: browser.selectTag(index, checked)
        }
    }
}
