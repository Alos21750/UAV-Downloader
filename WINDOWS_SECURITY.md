# Windows 下載與安全驗證

## 繁體中文

### 官方資產

只從本專案的 [GitHub Releases](https://github.com/Alos21750/UAV-Downloader/releases) 下載。每個目前版本只提供以下 canonical、可驗證資產：

- `UAV_Browser.exe`
- `UAV_Watcher.exe`
- `UAV_Watcher_portable.zip`：不使用 one-file 臨時自解壓流程的 UAV Watcher 備用版本
- `UAV_SHA256SUMS.txt`

AI 元件不會在每個版本重複發布。App 會在第一次實際使用對應功能時，自動從固定且已驗證的 v3.1.0 元件版本取得；正常使用者不必手動下載或解壓模型包。Release 也不再附帶 ALOS／Jable 舊檔名別名。

先用 `UAV_SHA256SUMS.txt` 核對檔案雜湊：

```powershell
Get-FileHash .\UAV_Watcher.exe -Algorithm SHA256
```

若已安裝 GitHub CLI，也可驗證該 Release 資產是否由本專案的 GitHub Actions 產生：

```powershell
gh attestation verify .\UAV_Watcher.exe `
  -R Alos21750/UAV-Downloader

gh attestation verify .\UAV_Watcher_portable.zip `
  -R Alos21750/UAV-Downloader
```

若 SSL／Proxy 攔截阻止 App 自動取得，才需要手動下載固定元件並原樣放在 EXE 旁：

- [`UAV_reazonspeech_asr_v1.zip`](https://github.com/Alos21750/UAV-Downloader/releases/download/v3.1.0/UAV_reazonspeech_asr_v1.zip)：`cf55e5485e14715beee6e0b12ca2b0998ad73ec755513e80138fa5161693c700`
- [`UAV_local_translation_v1.zip`](https://github.com/Alos21750/UAV-Downloader/releases/download/v3.1.0/UAV_local_translation_v1.zip)：`1259a2abeb5026411da39c6f3dcd69ebd70bed654ac9cfaaee0c7373867c0bb4`

兩者也可用 GitHub attestation 驗證：

```powershell

gh attestation verify .\UAV_reazonspeech_asr_v1.zip `
  -R Alos21750/UAV-Downloader

gh attestation verify .\UAV_local_translation_v1.zip `
  -R Alos21750/UAV-Downloader
```

雜湊只能證明檔案內容是否一致；GitHub attestation 只能證明建置來源。兩者都不等於防毒判定，也不能單獨證明程式安全。

### SmartScreen 與 Defender Antivirus 不同

- SmartScreen 的「Windows 已保護您的電腦」通常是下載信譽或發行者信譽提醒。
- Defender Antivirus 若顯示威脅名稱並隔離或刪除檔案，則是防毒內容／行為偵測。

目前公開 EXE 尚未簽章。建置流程已改為自行編譯 PyInstaller bootloader、明確停用 UPX，並提供 portable ZIP，以降低常見 heuristic 誤判面；這些措施不能保證任何防毒產品永不誤判。

### Defender 隔離檔案時

請勿為了執行本工具而關閉防毒、降低全機安全設定，或建立廣泛排除項目。請依序：

1. 更新 Windows Security 的 security intelligence。
2. 在 Protection History 記下完整 threat name、偵測時間與處置。
3. 記下 Windows 版本、Defender platform／security intelligence version，以及被偵測檔案的 SHA-256。
4. 確認下載網址屬於本專案 Release，且 SHA-256 與該版 `UAV_SHA256SUMS.txt` 相同。
5. 將上述資料附在 GitHub issue；維護者才能比對正確的檔案與偵測規則。
6. 不要為了上傳而自行還原隔離檔。可使用 Windows Security 內建回報；維護者則應從官方 Release 取得位元完全相同的資產，以 software developer 身分提交至 [Microsoft Security Intelligence](https://www.microsoft.com/wdsi/filesubmission)，選擇 incorrectly detected 並保留 Submission ID。

在 Microsoft 或維護者完成判定前，不要因為檔名相同就假設遭隔離的檔案一定安全。

### 長期簽章

可信的 Authenticode 簽章能證明發行者與檔案完整性，但仍不保證 Defender 永不攔截。自簽憑證不適合公開散布；本專案後續應評估符合資格的開源專案簽章方案，或 CA 核發的 OV code-signing certificate。

---

## English

### Official assets

Download only from this project's [GitHub Releases](https://github.com/Alos21750/UAV-Downloader/releases). Each current release provides only these canonical, verifiable assets:

- `UAV_Browser.exe`
- `UAV_Watcher.exe`
- `UAV_Watcher_portable.zip`, an onedir fallback without one-file temporary extraction
- `UAV_SHA256SUMS.txt`

AI components are not duplicated in each release. The app automatically fetches them from the fixed, verified v3.1.0 component release when the corresponding feature is first used; normal users do not manually download or extract a model pack. Legacy ALOS and Jable filename aliases are no longer included.

Compare the downloaded file with `UAV_SHA256SUMS.txt`:

```powershell
Get-FileHash .\UAV_Watcher.exe -Algorithm SHA256
```

If GitHub CLI is installed, verify that the release asset was produced by this repository's GitHub Actions workflow:

```powershell
gh attestation verify .\UAV_Watcher.exe `
  -R Alos21750/UAV-Downloader

gh attestation verify .\UAV_Watcher_portable.zip `
  -R Alos21750/UAV-Downloader
```

Only when SSL or proxy interception prevents the automatic fetch, download the fixed component and leave the ZIP unchanged beside the EXE:

- [`UAV_reazonspeech_asr_v1.zip`](https://github.com/Alos21750/UAV-Downloader/releases/download/v3.1.0/UAV_reazonspeech_asr_v1.zip): `cf55e5485e14715beee6e0b12ca2b0998ad73ec755513e80138fa5161693c700`
- [`UAV_local_translation_v1.zip`](https://github.com/Alos21750/UAV-Downloader/releases/download/v3.1.0/UAV_local_translation_v1.zip): `1259a2abeb5026411da39c6f3dcd69ebd70bed654ac9cfaaee0c7373867c0bb4`

GitHub attestations are also available for both fixed components:

```powershell

gh attestation verify .\UAV_reazonspeech_asr_v1.zip `
  -R Alos21750/UAV-Downloader

gh attestation verify .\UAV_local_translation_v1.zip `
  -R Alos21750/UAV-Downloader
```

A checksum proves byte identity. A GitHub attestation proves build provenance. Neither is an antivirus verdict or, by itself, proof that software is safe.

### SmartScreen is not Defender Antivirus

- SmartScreen's “Windows protected your PC” message is generally a download or publisher reputation warning.
- Defender Antivirus quarantine or deletion with a threat name is a content or behavior detection.

The public executables are currently unsigned. The build now uses a source-compiled PyInstaller bootloader, explicitly avoids UPX, and offers an onedir ZIP to reduce common heuristic surfaces. These mitigations cannot guarantee that every antivirus engine will always agree.

### If Defender quarantines the file

Do not weaken system-wide protection or create a broad exclusion just to run this tool. Instead:

1. Update Windows Security intelligence.
2. Record the complete threat name, detection time, and action from Protection History.
3. Record the Windows version, Defender platform and security intelligence versions, and the file's SHA-256.
4. Confirm that the URL is an official project release and that the SHA-256 matches that release's `UAV_SHA256SUMS.txt`.
5. Add those exact details to the GitHub issue so the maintainer can identify the affected sample and rule.
6. Do not restore a quarantined file merely to upload it. Use Windows Security's built-in reporting path when available. The maintainer should obtain the byte-identical asset from the official release, submit it as a software developer through [Microsoft Security Intelligence](https://www.microsoft.com/wdsi/filesubmission), choose the incorrectly detected category, and retain the Submission ID.

Until Microsoft or the maintainer has reviewed the exact sample, do not assume that a quarantined file is safe merely because its filename looks familiar.

### Long-term signing

Trusted Authenticode establishes publisher identity and file integrity, but still does not guarantee that Defender will never block a file. A self-signed certificate is not suitable for public distribution; the long-term path is an eligible open-source signing program or a CA-backed OV code-signing certificate.
