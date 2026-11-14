import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    title: i18n.strings.translation_dialog_title
    anchors.centerIn: parent
    width: Math.min(660, parent.width - 32)
    modal: true
    onOpened: sync()
    onClosed: apiKey.clear()
    function sync() {
        provider.currentIndex = Math.max(0, provider.model.indexOf(settings.profile.provider || "local"))
        endpoint.text = settings.profile.base_url || ""
        modelName.text = settings.profile.model || ""
        apiKey.clear()
        clearKey.checked = false
    }
    Connections {
        target: settings
        function onProfileChanged() { if (dialog.visible) dialog.sync() }
        function onGroupSaved(group) { if (group === "translation") dialog.close() }
    }
    contentItem: ColumnLayout {
        spacing: 10
        Label { text: i18n.strings.translation_provider_setting }
        ChoiceBox {
            id: provider; objectName: "translationProvider"
            model: ["local", "openai", "anthropic", "gemini", "openai-compatible"]
            Layout.fillWidth: true; enabled: !settings.busy
            onActivated: settings.loadProfile(currentText)
        }
        ChoiceBox {
            property var presets: settings.compatiblePresets()
            visible: provider.currentText === "openai-compatible"
            model: [i18n.strings.translation_compatible_custom].concat(presets.map(p => p.name))
            Layout.fillWidth: true
            onActivated: if (currentIndex > 0) {
                endpoint.text = presets[currentIndex - 1].base_url
                modelName.text = presets[currentIndex - 1].model
            }
        }
        Label { visible: endpoint.visible; text: i18n.strings.translation_base_url_label }
        TextField { id: endpoint; visible: provider.currentText !== "local"; Layout.fillWidth: true }
        Label { visible: modelName.visible; text: i18n.strings.translation_model_label }
        TextField { id: modelName; visible: endpoint.visible; Layout.fillWidth: true }
        Label { visible: apiKey.visible; text: i18n.strings.translation_api_key_label }
        TextField { id: apiKey; visible: endpoint.visible; echoMode: TextInput.Password; Layout.fillWidth: true; placeholderText: i18n.strings.translation_api_key_placeholder }
        CheckBox { id: clearKey; visible: endpoint.visible; text: i18n.strings.qt_clear_key }
        Label { text: i18n.strings.translation_provider_hint; Layout.fillWidth: true; wrapMode: Text.Wrap }
    }
    footer: RowLayout {
        Item { Layout.fillWidth: true }
        ActionButton { text: i18n.strings.qt_no; onClicked: dialog.close() }
        ActionButton {
            text: i18n.strings.qt_save_translation; highlighted: true; enabled: !settings.busy
            onClicked: settings.saveProfile({provider: provider.currentText, base_url: endpoint.text,
                model: modelName.text, api_key: apiKey.text || null, clear_key: clearKey.checked})
        }
    }
}
