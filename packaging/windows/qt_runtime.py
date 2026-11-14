"""Shared pinned-Qt runtime data for Browser, Watcher and portable builds."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata


def qt_runtime(spec_directory):
    root = Path(spec_directory).parents[1]
    datas, binaries = [], []
    hidden = [
        'cloudscraper', 'Crypto.Cipher.AES', 'm3u8', 'imageio_ffmpeg', 'imageio_ffmpeg.binaries',
        'curl_cffi', '_cffi_backend', 'uav_downloader.core.crashlog', 'certifi', 'faulthandler',
        'uav_downloader.core.updater', 'uav_downloader.core.ssl',
        'uav_downloader.subtitles.engine', 'uav_downloader.subtitles.domain',
        'uav_downloader.subtitles.llm', 'uav_downloader.subtitles.settings',
        'ctranslate2', 'ctranslate2._ext', 'numpy._core._exceptions',
        'sentencepiece', 'sentencepiece._sentencepiece', 'opencc', 'socks', 'urllib3.contrib.socks',
    ]
    for package in ['cloudscraper', 'certifi', 'curl_cffi', 'imageio_ffmpeg', 'ctranslate2', 'sentencepiece', 'opencc']:
        package_data, package_binaries, imports = collect_all(package)
        datas.extend(package_data); binaries.extend(package_binaries); hidden.extend(imports)
    datas = [entry for entry in datas if Path(str(entry[0])).name.lower() != 'cudnn64_9.dll']
    binaries = [entry for entry in binaries if Path(str(entry[0])).name.lower() != 'cudnn64_9.dll']
    for package in ['numpy', 'PyYAML', 'PySide6', 'PySide6_Addons', 'PySide6_Essentials', 'shiboken6']:
        datas.extend(copy_metadata(package))
    for name, destination in [
        ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'),
        ('third_party_licenses/Qt-LGPL-3.0.txt', 'third_party_licenses'),
        ('third_party_licenses/Qt-GPL-3.0.txt', 'third_party_licenses'),
        ('third_party_licenses/FuguMT-CC-BY-SA-4.0-NOTICE.txt', 'third_party_licenses'),
        ('third_party_licenses/Intel-Simplified-Software-License.txt', 'third_party_licenses')]:
        datas.append((str(root / name), destination))
    # Spec execution precedes Analysis.pathex. A clean CI checkout has no
    # editable install, so import-based package discovery cannot find these.
    qml_directory = root / 'src/uav_downloader/ui/qt'
    if not (qml_directory / 'Tasks.qml').is_file():
        raise FileNotFoundError(f'Missing Qt workspace resource: {qml_directory / "Tasks.qml"}')
    datas.extend((str(file), 'uav_downloader/ui/qt') for file in sorted(qml_directory.glob('*.qml')))
    datas.extend((str(file), 'uav_downloader/ui/qt/icons') for file in sorted((qml_directory / 'icons').glob('*.svg')))
    return datas, binaries, hidden


def exclude_external_icu(analysis):
    # Qt uses Windows' unversioned ICU API. Poppler/Conda's PATH DLLs may
    # have a different ABI and must not be bundled as system dependencies.
    analysis.binaries = [(name, path, kind) for name, path, kind in analysis.binaries
                         if Path(name).name.lower() not in {'icuuc.dll', 'icuin.dll', 'icudt.dll'}]
