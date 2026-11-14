import QtQuick

ChoiceBox {
    required property string setting
    currentIndex: model.indexOf(String(settings.values[setting] ?? ""))
    onActivated: settings.save({[setting]: currentText})
}
