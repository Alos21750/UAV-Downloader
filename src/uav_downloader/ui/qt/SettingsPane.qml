import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: pane
    objectName: "settingsScroll"
    required property var style
    property string destination: "download"
    signal clearQueueRequested()
    signal chooseDirectoryRequested()
    clip: true
    contentWidth: availableWidth
    TranslationDialog { id: translation }
    ColumnLayout {
        width: pane.availableWidth - 128
        x: 64; y: 28
        spacing: 13
        enabled: controller.ready && !controller.closing
        Label { text: i18n.strings.settings_title; font.pixelSize: 25; font.bold: true; Layout.topMargin: 4 }
        Label { text: i18n.strings.settings_desc; color: pane.style.secondary; wrapMode: Text.Wrap; Layout.fillWidth: true }
        SettingsCard {
            heading: i18n.strings.update_card_title
            Label { text: i18n.strings.update_current.replace("{version}", updates.currentVersion); Layout.fillWidth: true }
            RowLayout {
                Label { text: updates.status; color: pane.style.secondary; Layout.fillWidth: true; wrapMode: Text.Wrap }
                ActionButton { text: i18n.strings.update_check_btn; enabled: !updates.busy; onClicked: updates.check() }
                ActionButton { text: i18n.strings.update_now_btn; visible: updates.canApply; enabled: updates.available && !updates.busy; onClicked: updates.download() }
                ActionButton { text: i18n.strings.qt_release_page; enabled: !!updates.releaseUrl; onClicked: Qt.openUrlExternally(updates.releaseUrl) }
            }
        }
        SettingsCard {
            heading: i18n.strings.download_settings
            PreferenceRow {
                label: i18n.strings.save_location_setting; description: i18n.strings.save_location_desc
                TextField { text: pane.destination; readOnly: true; Layout.fillWidth: true }
                ActionButton { text: i18n.strings.browse_folder; onClicked: pane.chooseDirectoryRequested() }
            }
            PreferenceRow {
                label: i18n.strings.speed_limit_setting; description: i18n.strings.speed_limit_desc
                ChoiceBox {
                    model: ["0", "1", "2", "5", "10", "15"]
                    labels: ({"0":i18n.strings.unlimited,"1":"1 MB/s","2":"2 MB/s","5":"5 MB/s","10":"10 MB/s","15":"15 MB/s"})
                    currentIndex: model.indexOf(String(settings.values.speed_limit_mbps || 0))
                    enabled: !settings.busy
                    onActivated: settings.save({speed_limit_mbps: Number(currentText)})
                }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.concurrent_setting; description: i18n.strings.concurrent_desc
                PreferenceEntry { setting: "download_concurrency"; numeric: true; Layout.preferredWidth: 90; validator: IntValidator { bottom: 1; top: 32 } }
                Label { text: i18n.strings.max_n.replace("{n}", "32") }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.max_workers_per_video_setting; description: i18n.strings.max_workers_per_video_desc.replace("{n}", "16")
                PreferenceEntry { setting: "max_workers_per_video"; numeric: true; Layout.preferredWidth: 90; validator: IntValidator { bottom: 1; top: 16 } }
                Label { text: i18n.strings.max_n.replace("{n}", "16") }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.slow_requeue_setting; description: i18n.strings.slow_requeue_desc.replace("{grace}", "60").replace("{n}", "3")
                PreferenceEntry { setting: "slow_requeue_kbps"; numeric: true; Layout.preferredWidth: 90; validator: IntValidator { bottom: 0; top: 100000 } }
                Label { text: i18n.strings.slow_requeue_unit }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.resolution_setting; description: i18n.strings.resolution_desc
                PreferenceChoice { setting: "resolution"; model: ["highest", "1080", "720", "480", "360", "lowest"]; Layout.preferredWidth: 180 }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.filename_mode_setting; description: i18n.strings.filename_mode_desc
                PreferenceChoice { setting: "filename_mode"; model: ["full-title", "code-only"]; Layout.preferredWidth: 230 }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.filename_template_setting; description: i18n.strings.filename_template_desc
                PreferenceEntry { setting: "filename_template"; Layout.fillWidth: true; Layout.maximumWidth: 440 }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.download_schedule_setting; description: i18n.strings.download_schedule_desc
                PreferenceEntry { setting: "download_schedule"; placeholderText: "HH:MM"; Layout.preferredWidth: 110 }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.minimum_free_setting; description: i18n.strings.minimum_free_desc
                PreferenceEntry { setting: "minimum_free_gb"; numeric: true; Layout.preferredWidth: 110; validator: DoubleValidator { bottom: 0; top: 1024; locale: "C" } }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.auto_retry_setting; description: i18n.strings.auto_retry_desc
                PreferenceEntry { setting: "auto_retry_count"; numeric: true; Layout.preferredWidth: 110; validator: IntValidator { bottom: 0; top: 10 } }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.notify_complete_setting; description: i18n.strings.notify_complete_desc
                CheckBox { checked: settings.values.notify_on_complete !== false; onClicked: settings.save({notify_on_complete: checked}) }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.subtitle_setting; description: i18n.strings.subtitle_desc
                PreferenceChoice { setting: "subtitle_mode"; model: ["none", "ja", "en", "zh", "all"] }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.recognition_quality_setting; description: i18n.strings.recognition_quality_desc
                PreferenceChoice { setting: "recognition_quality"; model: ["auto", "quality", "balanced", "fast"] }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.translation_provider_setting; description: i18n.strings.translation_provider_hint
                Label { text: i18n.options[settings.profile.provider || "local"]; color: pane.style.secondary }
                Item { Layout.fillWidth: true }
                ActionButton { text: i18n.strings.translation_provider_configure; onClicked: translation.open() }
            }
        }
        SettingsCard {
            heading: i18n.strings.proxy_card_title; description: i18n.strings.proxy_card_desc
            ProxySettings {}
        }
        SettingsCard {
            heading: i18n.strings.cf_card_title; description: i18n.strings.cf_card_desc
            PreferenceRow {
                label: i18n.strings.cf_host_label
                ChoiceBox {
                    id: host
                    model: settings.cloudflare.available || []
                    currentIndex: model.indexOf(settings.cloudflare.host || "")
                    Layout.preferredWidth: 220
                    onActivated: settings.loadCloudflare(currentText)
                }
                Item { Layout.fillWidth: true }
            }
            PreferenceRow {
                label: i18n.strings.cf_cookie_label
                TextField { id: cookie; Layout.fillWidth: true; echoMode: TextInput.Password }
            }
            PreferenceRow {
                label: i18n.strings.cf_ua_label
                TextField { id: agent; Layout.fillWidth: true; text: settings.cloudflare.ua || "" }
            }
            RowLayout {
                Layout.leftMargin: 186
                ActionButton { text: i18n.strings.cf_save; highlighted: true; enabled: !!host.currentText && !settings.busy; onClicked: settings.saveCloudflare({host: host.currentText, cookie: cookie.text, ua: agent.text}) }
                ActionButton { text: i18n.strings.cf_clear; enabled: !!host.currentText && !settings.busy; onClicked: settings.saveCloudflare({host: host.currentText, clear: true}) }
            }
            Connections { target: settings; function onCloudflareChanged() { cookie.clear(); agent.text = settings.cloudflare.ua || "" } }
            Label { text: (settings.cloudflare.hosts || []).length ? i18n.strings.cf_status.replace("{hosts}", settings.cloudflare.hosts.join(", ")) : i18n.strings.cf_status_none; color: pane.style.secondary }
            Label { text: i18n.strings.cf_help; color: pane.style.muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
        SettingsCard {
            heading: i18n.strings.queue_card_title; description: i18n.strings.queue_card_desc
            PreferenceRow {
                label: i18n.strings.queue_path_label
                TextField { text: controller.queuePath; readOnly: true; Layout.fillWidth: true }
            }
            RowLayout {
                Layout.leftMargin: 186
                ActionButton { text: i18n.strings.open_queue_folder; onClicked: controller.openFolder(controller.queueFolder) }
                ActionButton { text: i18n.strings.clear_saved_queue; onClicked: pane.clearQueueRequested() }
            }
        }
        SettingsCard {
            heading: i18n.strings.about
            Label { text: "UAV Downloader  ·  v" + updates.currentVersion }
            Label { text: i18n.strings.disclaimer; Layout.fillWidth: true; wrapMode: Text.Wrap; color: pane.style.secondary }
        }
        Item { Layout.preferredHeight: 12 }
    }
}
