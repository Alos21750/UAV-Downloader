<p align="center">
  <strong>繁體中文</strong> · <a href="./README.zh-CN.md">简体中文</a> · <a href="./README.en.md">English</a> · <a href="./README.ja.md">日本語</a>
</p>

<h1 align="center">UAV Downloader</h1>

<p align="center">
  JableTV、MissAV、SupJav、Hanime1 的桌面下載器與自動監控工具，內建 <strong>AI 生成字幕</strong>。<br />
  <strong>下載完成，自動補上 AI 字幕：</strong>日語音軌在本機辨識，可輸出日文、英文與繁中 SRT。<br />
  想自己瀏覽挑片，用 <strong>UAV Browser</strong>；想依分類持續追新，用 <strong>UAV Watcher</strong>。<br />
  <strong>真正的核心是無人值守：</strong>選好分類與排程後，自動找新片、跨站去重、下載並補上字幕。
</p>

<p align="center">
  <a href="https://github.com/Alos21750/UAV-Downloader/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Alos21750/UAV-Downloader?style=flat-square&label=release&color=ff5263" /></a>
  <a href="https://github.com/Alos21750/UAV-Downloader/releases"><img alt="Total downloads" src="https://img.shields.io/github/downloads/Alos21750/UAV-Downloader/total?style=flat-square&label=downloads&color=2ea44f" /></a>
  <a href="https://github.com/Alos21750/UAV-Downloader"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Alos21750/UAV-Downloader?style=flat-square&logo=github&color=f5b942" /></a>
  <a href="./LICENSE"><img alt="Apache 2.0 license" src="https://img.shields.io/github/license/Alos21750/UAV-Downloader?style=flat-square" /></a>
  <a href="https://github.com/Alos21750/UAV-Downloader/pkgs/container/uav-downloader"><img alt="Docker amd64 and arm64" src="https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ed?style=flat-square&logo=docker&logoColor=white" /></a>
</p>

<p align="center">
  <strong><a href="https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Browser.exe">下載 UAV Browser</a></strong>
  ·
  <strong><a href="https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Watcher.exe">下載 UAV Watcher</a></strong>
  ·
  <a href="https://github.com/Alos21750/UAV-Downloader/releases/latest">UAV Watcher portable ZIP（v3.1.0 起）</a>
  ·
  <a href="https://github.com/Alos21750/UAV-Downloader/releases/latest">查看最新版本</a>
</p>

> [!TIP]
> **預設完全本地，不用 API Key，也不會上傳內容。** UAV Browser 與 UAV Watcher 可在影片下載完成後，自動建立播放器可切換的 `.ja.srt`、`.en.srt`、`.zh-TW.srt`，且不修改 MP4。需要時也可自行接入常見 LLM API；雲端模式只會把辨識後的字幕文字作為影音內容送出，另含必要的 API 驗證與一般連線資訊，絕不傳送影片或音訊。

<p align="center">
  <img src="./docs/assets/uav-browser.png" width="100%" alt="UAV Downloader UAV Browser v3.1.0 English dark interface with JableTV, MissAV and SupJav browse tabs" />
</p>

## 三種工作模式，一個下載核心

| 需求 | 建議 | 操作方式 |
|---|---|---|
| 想瀏覽、搜尋、逐片挑選 | **UAV_Browser.exe** | 看片卡、複選、加入佇列或直接下載 |
| 想讓電腦無人值守自動追新 | **UAV_Watcher.exe** | 選網站、分類、日期、版本優先與排程；之後自動掃描、去重、下載與產生字幕 |
| Defender 對 one-file UAV Watcher 發出偵測 | **UAV_Watcher_portable.zip** | 先核對雜湊與偵測資訊，再評估不需臨時自解壓的備用包；若備用包也被偵測，請停止並回報 |
| 想在 NAS／伺服器無介面執行 | **Docker / CLI** | 傳入一個或多個網址，或掛載 `urls.txt` |

不確定時先下載 **UAV Browser**。兩個 Windows 執行檔都免安裝 Python，Release 版本已包含 ffmpeg。

深入了解：[UAV Browser](./docs/uav-browser.md) · [UAV Watcher 無人值守流程](./docs/uav-watcher.md) · [AI 字幕](./docs/ai-subtitles.md) · [Docker / CLI](./docs/docker-cli.md) · [升級至 UAV](./docs/migration-to-uav.md)

## Windows：30 秒開始

1. 下載 [UAV_Browser.exe](https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Browser.exe) 或 [UAV_Watcher.exe](https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Watcher.exe)。
2. 把檔案放在可寫入的資料夾，直接雙擊執行。
3. 首次開啟選擇語言；之後可隨時切換繁體中文、简体中文、English、日本語與明／暗主題。

SmartScreen 信譽提醒與 Defender Antivirus 隔離是不同事件。請先閱讀 [Windows 下載與安全驗證](./WINDOWS_SECURITY.md)：核對 `SHA256SUMS.txt` 與 GitHub provenance；若 Defender 顯示 threat name，請勿直接降低防護設定。

## UAV Browser：瀏覽、挑選、下載

1. 在「瀏覽」選 JableTV、MissAV、SupJav 或 Hanime1，再選分類或輸入關鍵字；Hanime1 另有可組合類型、排序、時間、片長與 240 個標籤的完整篩選器。
2. 勾選多部影片後加入佇列，或直接下載選取項目。
3. 也可在「下載」貼上網址，或從 `.txt` / `.csv` 匯入多個網址。
4. 在「設定」調整儲存位置、畫質、同時下載數、單片執行緒、速度上限、AI 字幕與 Proxy。

| 能力 | 現行行為 |
|---|---|
| 下載佇列 | 每項顯示狀態、進度與速度；佇列會保存；進行中的工作可停止並優先重新排隊，失敗項目可單獨重試 |
| 並行下載 | 預設 2，最多 32 個影片下載；AI 字幕使用獨立背景佇列，不占用下載名額 |
| 單片執行緒 | 每部影片的分段工作執行緒上限為 1–16，預設依 CPU 自動取值（最多 16）；實際來源可使用較少連線，SupJav／Hanime1 直連最多 4 條。使用 Proxy 時可調低以減少負載 |
| 畫質偏好 | 最高、1080p、720p、480p、360p、最低；實際可用畫質依來源而定 |
| AI 字幕 | 不產生、日文、英文、繁中或三語；翻譯預設在本機執行，也可自行設定 LLM API；輸出為播放器可切換的同名 SRT |
| 網址操作 | 剪貼簿偵測、手動貼上、文字／CSV 批次匯入 |
| Proxy | 可用自訂 HTTP、HTTPS、SOCKS4、SOCKS5，或跟隨 Windows 已啟用的手動 ProxyServer；不修改 Windows 全域代理 |
| 更新 | 背景檢查 GitHub Release，有新版時由使用者確認後更新 |

## UAV Watcher：依分類自動追新

<p align="center">
  <img src="./docs/assets/uav-watcher.png" width="100%" alt="UAV Watcher v3.1.0 Traditional Chinese dark interface showing MissAV categories, date, quality, version priority and AI subtitles" />
</p>

1. 選擇儲存位置；若不選，會在執行檔旁自動建立 `tmp`。
2. 按「顯示設定」調整共同基準日期與資料夾，或替四個網站分別指定日期與存放位置；也可調整畫質、單片執行緒、版本優先、AI 字幕與 Proxy。
3. 在四個網站分頁搜尋並勾選分類；支援群組全選。
4. 按「排程」選擇每 1–168 小時，或每天依電腦本地時間在指定時刻檢查。
5. 按「開始監控」。分類會保持可見；掃描或下載時才顯示進度，需要紀錄時按「顯示活動」。

| 網站 | 可選目標數 | 分組內容 |
|---|---:|---|
| JableTV | 129 | 動態／榜單、主分類與標籤群組 |
| MissAV | 102 | 動態／榜單、分類／標籤、片商群組 |
| SupJav | 10 | 動態／榜單與主要分類 |
| Hanime1 | 272 | 上市／上傳／排行、裏番／泡麵番等 9 種類型、6 種時間、8 種片長與完整 240 標籤 |

UAV Watcher 可設為每 1–168 小時自動檢查，或每天依這台電腦的本地時間在指定時刻檢查；舊設定仍預設每 24 小時。按「立即檢查」會立刻執行一次，不會另外建立重複排程。同一番號跨分類或跨網站重複時，會優先保留符合使用者版本偏好的候選；無法可靠辨識番號時只按完全相同網址去重，不猜測合併。

下載記錄優先存於執行檔旁的 `.uav-watcher`；若該位置不可寫，會改用 `%APPDATA%\UAV Downloader\watch`。

## AI 字幕：下載完成，自動補上日／英／繁中 SRT

- 兩個 Windows GUI 都可在下載前選擇 **不產生／日文／英文／繁中／三語**。影片完成後只會在旁邊建立所選的 `.ja.srt`、`.en.srt`、`.zh-TW.srt`，不修改原始 MP4。若只要求英文或繁中而翻譯失敗，不會留下未要求的日文 sidecar。字幕翻譯預設使用不需 API Key 的本地模式。
- 日文辨識全程在本機執行。預設的 **自動（建議）** 使用固定 revision 的 [ReazonSpeech K2 v2](https://huggingface.co/reazon-research/reazonspeech-k2-v2) 與 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)，針對一般 CPU 最佳化，不需獨立顯示卡；經 SHA-256 驗證的辨識包約 **178 MB**，只在第一次實際要求字幕時下載。
- 原有的 [whisper.cpp](https://github.com/ggml-org/whisper.cpp) 模式仍可明確選用：**精準 large-v3-turbo q5（約 574 MB）**、**平衡 small q5（約 190 MB）**、**快速 base q5（約 60 MB）**。自動模式只會在執行失敗、輸出損壞或時間軸結構無效時另行下載並改用平衡模式；不會因為猜測文字內容好壞而擅自切換。
- [官方 Silero VAD](https://huggingface.co/ggml-org/whisper-vad/tree/main) 只作為語音閘門；辨識會使用保留原始靜音的、具上下文且互不重疊的視窗，再把結果映射回影片的絕對時間。ReazonSpeech 使用 CPU 高效率的 RNN-T 解碼；三個 Whisper 模式則使用 beam search、best-of 與溫度 fallback。
- App 會記錄自己產生字幕所使用的辨識 profile 與 pipeline 來源；兩者變更時會重新產生由 App 建立的字幕及衍生翻譯。沒有來源記錄的既有 SRT，或產生後由使用者修改過的 SRT，會原樣保留。
- 本地英文與台灣繁中翻譯使用固定版本、SHA-256 驗證的 [FuguMT](https://huggingface.co/staka/fugumt-ja-en) 與 [OPUS-MT](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh) INT8 小模型，不使用 Google 或其他免費網路翻譯端點。本地翻譯模型包約 **147 MB**，只會在實際開始產生英文、繁中或三語字幕時下載；選擇「不產生」或「日文」不會下載，改用 LLM API 時也不需要這個本地翻譯模型包。下載一次後即可離線重複使用。
- 可選的 API 擴充支援 **OpenAI、Anthropic、Gemini** 與 **OpenAI-compatible** API；後者可連接 DeepSeek、OpenRouter、Groq、Ollama、LiteLLM 等相容服務。影音內容中只有辨識後的字幕文字會送到所選服務；API 驗證與一般連線資訊也會正常送出，但影片與音訊始終留在本機。
- 使用者自行提供的 API Key 會透過 Windows DPAPI，以目前登入的 Windows 帳號加密保存；專案與 EXE 不附帶任何 API Key。各服務的費用、額度、資料處理與使用政策由該供應商決定，使用前請自行確認。
- 本地翻譯會逐一保留每個 cue 與原時間軸，套用 900 多組人工撰寫與審核的成人語境、安全／同意、拍攝隱私、現場指示與日常短句，並使用保守的台灣用語校正及版本化 exact-match 翻譯記憶；不做容易反轉「停／不要停」語意的模糊比對。
- UAV Browser 的影片下載與字幕處理使用獨立佇列；字幕逐部在背景產生，不會占用 1–32 個影片下載名額。
- 產生速度取決於 CPU、影片長度、實際語音比例與所選翻譯服務；三語共用同一次本機語音辨識。本地模式也會重用中間翻譯結果，避免重複推論。

## 支援範圍

| 網站 | UAV Browser 瀏覽／搜尋／下載 | UAV Watcher 分類監控 | Docker／CLI 網址下載 |
|---|:---:|:---:|:---:|
| JableTV | ✓ | ✓ | ✓ |
| MissAV | ✓ | ✓ | ✓ |
| SupJav | ✓ | ✓ | ✓ |
| Hanime1 | ✓ | ✓ | ✓ |

網站與 CDN 可能隨時調整；若某站失效，請先確認已使用最新版，再附上可重現資訊開 Issue。

Hanime1 支援官方 `watch?v=` 網址、站內搜尋、分頁，以及可組合的排序、類型、上架時間、片長與完整 240 標籤；裏番／泡麵番的精簡列表版型也能正確解析。下載時會即時解析站方簽名 MP4，依畫質偏好選擇可用來源，並支援最多 4 路分段、斷線續傳與單路備援。

## 從原始碼執行

需要 **Python 3.10+** 與 Tk。舊 README 的 Python 3.8+ 已不符合目前原始碼語法需求。

```bash
git clone https://github.com/Alos21750/UAV-Downloader.git
cd UAV-Downloader
python -m pip install -r requirements.txt
python -m pip install -e .

# 完整 GUI
uav-browser

# 自動監控工具
uav-watcher

# 單一網址、無 GUI，並指定下載位置與每片 3 個下載執行緒
uav-browser --nogui --url "https://jable.tv/videos/example/" --output "/path/to/downloads" --max-workers-per-video 3
```

`-o` 是 `--output` 的縮寫；若省略，預設會儲存在 `./download`。`--max-workers-per-video` 接受 1–16，可在 Proxy 負載較高時調低。

Linux 若未內建 Tk，請先用系統套件管理器安裝 `python3-tk`。macOS／Linux 是原始碼執行方式；Windows Release 才提供免安裝 EXE。

## Docker / NAS

公開映像為 `ghcr.io/alos21750/uav-downloader:latest`，GitHub Actions 會建置 amd64 與 arm64。

```bash
# 下載單一網址；把主機資料夾掛載到 /downloads
docker run --rm -v "/path/to/downloads:/downloads" \
  ghcr.io/alos21750/uav-downloader:latest "https://jable.tv/videos/example/"

# docker compose：直接傳網址
docker compose run --rm uav "https://jable.tv/videos/example/"

# 或把網址逐行放進 ./downloads/urls.txt
docker compose run --rm uav
```

可用環境變數：

| 變數 | 用途 |
|---|---|
| `RESOLUTION` | `highest`、`1080`、`720`、`480`、`360`、`lowest` |
| `MAX_WORKERS_PER_VIDEO` | 每部影片的分段工作執行緒上限，範圍 1–16；調低可減少 Proxy 負載 |
| `URL` / `URLS` | 傳入一個或多個網址 |
| `URLS_FILE` | 網址清單；預設 `/downloads/urls.txt` |
| `DOWNLOAD_DIR` | 容器內儲存位置；預設 `/downloads` |

Docker 是無介面、執行完即結束的下載工作，不包含 UAV Browser 或 UAV Watcher GUI。

## 遇到問題

開 [GitHub Issue](https://github.com/Alos21750/UAV-Downloader/issues/new) 時，請提供：

- App 版本、使用的工具與作業系統。
- 網站與可重現網址，以及預期／實際結果。
- 若程式閃退，附上執行檔旁的 `crash_log.txt` 或 `crash_native.log`。
- 不要上傳 Cookie、Proxy 帳密、Token 或其他私密值。

需要 Proxy 時，可在 UAV Browser「設定」或 UAV Watcher 頂部選擇自訂代理、Windows 系統代理或停用。兩個 GUI 共用設定，且只作用於本程式；Windows 模式目前支援已啟用的手動 ProxyServer。PAC 設定網址會提示但不執行，WPAD 自動偵測尚未支援。

## Stars 與專案活動

<p align="center">
  <img src="./docs/assets/star-history.svg" width="100%" alt="Verified GitHub star history for UAV Downloader" />
</p>

圖表由本 repo 的 GitHub Actions 使用唯讀 repository token 取得目前 stargazers 的 `starredAt` 時間後產生；不請求或輸出帳號名稱。只有資料或圖表格式改變時才更新。曲線反映「目前仍按 Star 的帳號」之加入日期，已取消 Star 的帳號不在資料中。

<details>
<summary>為什麼不再使用舊的 api.star-history.com 圖片？</summary>

GitHub 在 2026 年 7 月限制 stargazer 清單存取，舊的匿名 Star History 圖片端點因而失效。這個專案改用自身 GitHub Actions 權限生成靜態 SVG，避免 README 留下壞圖，也不把 Token 放進 README。參考：[GitHub 公告](https://github.blog/changelog/2026-06-30-upcoming-access-restrictions-to-public-api-endpoints-and-ui-views/) · [Star History 說明](https://www.star-history.com/blog/github-stargazer-api-restriction/)

</details>

## 授權與使用責任

程式碼採 [Apache License 2.0](./LICENSE)。本工具僅供合法的個人與研究用途；請遵守所在地法律、網站條款與內容權利，只下載你有權取得的內容。

版本變更與已修復問題請看 [Releases](https://github.com/Alos21750/UAV-Downloader/releases)。

<p align="center">Built and maintained by <a href="https://github.com/Alos21750">ALOS</a>.</p>
