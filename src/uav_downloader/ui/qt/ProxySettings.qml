import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

PreferenceRow {
    label: i18n.strings.proxy_url_label
    description: i18n.strings.proxy_help
    TextField {
        id: proxy
        text: settings.values.proxy_url || ""
        placeholderText: i18n.strings.proxy_url_placeholder; Layout.fillWidth: true
        onAccepted: if (text.trim()) settings.save({proxy_mode: "manual", proxy_url: text})
    }
    ActionButton { text: i18n.strings.proxy_save; highlighted: true; enabled: !!proxy.text.trim() && !settings.busy; onClicked: settings.save({proxy_mode: "manual", proxy_url: proxy.text}) }
    ActionButton { text: i18n.strings.proxy_windows; enabled: !settings.busy; onClicked: settings.save({proxy_mode: "system"}) }
    ActionButton { text: i18n.strings.proxy_clear; enabled: !settings.busy; onClicked: settings.save({proxy_mode: "direct", proxy_url: ""}) }
    Connections { target: settings; function onValuesChanged() { proxy.text = settings.values.proxy_url || "" } }
}
