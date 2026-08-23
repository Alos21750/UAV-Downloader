<p align="center">
  <a href="./README.md">繁體中文</a> · <a href="./README.zh-CN.md">简体中文</a> · <a href="./README.en.md">English</a> · <strong>日本語</strong>
</p>

<h1 align="center">UAV Downloader</h1>

<p align="center">
  JableTV、MissAV、SupJav、Hanime1 に対応したデスクトップダウンローダー兼自動監視ツールです。<strong>AI 字幕生成</strong>を内蔵しています。<br />
  <strong>動画のダウンロード後に AI 字幕を自動追加：</strong>日本語音声をローカルで認識し、日本語・英語・繁体字中国語の SRT を出力できます。<br />
  動画を閲覧して選ぶ場合は <strong>UAV Browser</strong>、選択したカテゴリの新着を継続的に追跡する場合は <strong>UAV Watcher</strong> を使用してください。<br />
  <strong>最大の特長は無人自動運転です：</strong>カテゴリとスケジュールを一度設定すれば、新着の検出、重複排除、ダウンロード、字幕生成まで自動化できます。
</p>

<p align="center">
  <a href="https://github.com/Alos21750/UAV-Downloader/releases/latest"><img alt="最新リリース" src="https://img.shields.io/github/v/release/Alos21750/UAV-Downloader?style=flat-square&label=release&color=ff5263" /></a>
  <a href="https://github.com/Alos21750/UAV-Downloader/releases"><img alt="総ダウンロード数" src="https://img.shields.io/github/downloads/Alos21750/UAV-Downloader/total?style=flat-square&label=downloads&color=2ea44f" /></a>
  <a href="https://github.com/Alos21750/UAV-Downloader"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/Alos21750/UAV-Downloader?style=flat-square&logo=github&color=f5b942" /></a>
  <a href="./LICENSE"><img alt="Apache 2.0 ライセンス" src="https://img.shields.io/github/license/Alos21750/UAV-Downloader?style=flat-square" /></a>
  <a href="https://github.com/Alos21750/UAV-Downloader/pkgs/container/uav-downloader"><img alt="Docker amd64 と arm64" src="https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ed?style=flat-square&logo=docker&logoColor=white" /></a>
</p>

<p align="center">
  <strong><a href="https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Browser.exe">UAV Browser をダウンロード</a></strong>
  ·
  <strong><a href="https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Watcher.exe">UAV Watcher をダウンロード</a></strong>
  ·
  <a href="https://github.com/Alos21750/UAV-Downloader/releases/latest">UAV Watcher portable ZIP（v3.1.0 以降）</a>
  ·
  <a href="https://github.com/Alos21750/UAV-Downloader/releases/latest">最新リリースを表示</a>
</p>

> [!TIP]
> **既定では完全ローカルで動作し、API Key もアップロードも不要です。** UAV Browser と UAV Watcher は、動画を変更せずに、プレーヤーで切り替え可能な `.ja.srt`、`.en.srt`、`.zh-TW.srt` をダウンロード後に作成できます。必要に応じて一般的な LLM API も設定できます。クラウドモードでメディア由来の情報として送信されるのは認識済み字幕テキストのみで、ほかに必要な API 認証情報と通常の接続メタデータが送信されます。動画や音声は送信されません。

<p align="center">
  <img src="./docs/assets/uav-browser.png" width="100%" alt="JableTV、MissAV、SupJav の閲覧タブを表示する UAV Downloader UAV Browser のダーク画面" />
</p>

## 3 つのワークフロー、1 つのダウンロードコア

| やりたいこと | 推奨ツール | 操作方法 |
|---|---|---|
| 閲覧・検索して動画を個別に選ぶ | **UAV_Browser.exe** | カードを閲覧し、複数選択してキューに追加、またはすぐにダウンロード |
| 無人で新着を自動追跡する | **UAV_Watcher.exe** | サイト、カテゴリ、基準日、バージョン優先度、スケジュールを設定し、検出・重複排除・ダウンロード・字幕生成を自動実行 |
| Defender が one-file 版 UAV Watcher を検出する | **UAV_Watcher_portable.zip** | ハッシュと検出内容を確認してから、一時自己展開を行わない代替版を評価してください。代替版も検出された場合は使用を中止して報告してください |
| NAS／サーバーで画面なしに実行する | **Docker / CLI** | 1 件以上の URL を渡すか、`urls.txt` をマウント |

迷った場合は **UAV Browser** から始めてください。どちらの Windows 実行ファイルも Python のインストールは不要で、Release 版には ffmpeg が含まれています。

詳細：[UAV Browser](./docs/uav-browser.md) · [UAV Watcher の無人自動運転](./docs/uav-watcher.md) · [AI 字幕](./docs/ai-subtitles.md) · [Docker / CLI](./docs/docker-cli.md) · [UAV への移行](./docs/migration-to-uav.md)

## Windows：30 秒で開始

1. [UAV_Browser.exe](https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Browser.exe) または [UAV_Watcher.exe](https://github.com/Alos21750/UAV-Downloader/releases/latest/download/UAV_Watcher.exe) をダウンロードします。
2. 書き込み可能なフォルダーへ置き、ダブルクリックして実行します。
3. 初回起動時に言語を選択します。以後は日本語、English、繁體中文、简体中文、およびライト／ダークテーマをいつでも切り替えられます。

SmartScreen の評価警告と Defender Antivirus の隔離は別の事象です。まず [Windows のダウンロードとセキュリティ検証](./WINDOWS_SECURITY.md)を読み、`SHA256SUMS.txt` と GitHub provenance を確認してください。Defender が脅威名を表示した場合、保護設定を安易に弱めないでください。

## UAV Browser：閲覧・選択・ダウンロード

1. 「閲覧」で JableTV、MissAV、SupJav、Hanime1 のいずれかを選び、カテゴリを開くか検索します。Hanime1 ではジャンル、並び順、公開日、再生時間、240 タグを組み合わせる詳細フィルターも利用できます。
2. 複数の動画を選択してキューに追加するか、そのままダウンロードします。
3. 「ダウンロード」タブへ URL を貼り付けるほか、`.txt` / `.csv` から複数 URL を取り込めます。
4. 「設定」で保存先、画質、同時ダウンロード数、動画ごとのワーカー数、速度制限、AI 字幕、Proxy を調整できます。

| 機能 | 現在の動作 |
|---|---|
| ダウンロードキュー | 項目ごとに状態、進捗、速度を表示し、キューを保存します。実行中の項目は停止して最優先で再キューでき、失敗した項目は個別に再試行できます |
| 同時ダウンロード | 既定は 2 本、最大 32 本です。AI 字幕は別のバックグラウンドキューで処理され、動画のダウンロード枠を使用しません |
| 動画ごとのワーカー | セグメントワーカーの上限は 1–16 で、既定値は CPU に応じて自動決定されます（最大 16）。配信元によっては少ない接続数を使用し、SupJav／Hanime1 の直接ダウンロードは最大 4 接続です。Proxy の負荷を下げる場合は小さい値にしてください |
| 画質設定 | 最高、1080p、720p、480p、360p、最低。実際に利用できる画質は配信元によって異なります |
| AI 字幕 | オフ、日本語、英語、繁体字中国語、3 言語。翻訳は既定でローカル実行され、任意で LLM API を設定できます。同名の切り替え可能な SRT として出力されます |
| URL 入力 | クリップボード検出、手動貼り付け、テキスト／CSV 一括取り込み |
| Proxy | 独自の HTTP、HTTPS、SOCKS4、SOCKS5、または Windows で有効な手動 ProxyServer を使用できます。Windows のグローバル設定は変更しません |
| 更新 | バックグラウンドで GitHub Release を確認し、新版のインストール前にユーザーへ確認します |

## UAV Watcher：カテゴリの新着を自動監視

<p align="center">
  <img src="./docs/assets/uav-watcher.png" width="100%" alt="MissAV のカテゴリ、日付、画質、バージョン優先度、AI 字幕を表示する UAV Watcher のダーク画面" />
</p>

1. 保存先を選択します。未指定の場合、実行ファイルの隣に `tmp` が自動作成されます。
2. 「設定を表示」で共通の基準日と保存先、または 4 サイトそれぞれの日付とフォルダーを指定できます。画質、動画ごとのワーカー数、バージョン優先度、AI 字幕、Proxy も同じ画面で変更できます。
3. 4 サイトのタブでカテゴリを検索して選択します。グループ単位の全選択にも対応しています。
4. 「スケジュール」で 1–168 時間ごとの確認、またはコンピューターのローカル時刻による毎日の確認時刻を設定します。
5. 「監視開始」を押します。カテゴリは常に表示され、スキャンまたはダウンロード中のみ進捗が表示されます。ログが必要な場合は「アクティビティを表示」を押します。

| サイト | 選択可能な対象数 | グループ内容 |
|---|---:|---|
| JableTV | 129 | フィード／ランキング、主要カテゴリ、タググループ |
| MissAV | 102 | フィード／ランキング、カテゴリ／タグ、メーカーグループ |
| SupJav | 10 | フィード／ランキング、主要カテゴリ |
| Hanime1 | 272 | 新着／ランキング、裏番／ショートアニメを含む 9 ジャンル、6 期間、8 再生時間、全 240 タグ |

UAV Watcher は 1–168 時間ごと、またはこのコンピューターのローカル時刻で毎日指定時刻に確認できます。既存設定の既定値は引き続き 24 時間です。「今すぐ確認」は重複スケジュールを作成せずに 1 回だけ直ちに実行します。同じ識別番号がカテゴリやサイトをまたいで重複した場合、設定したバージョン優先度に合う候補を残します。番号を確実に識別できない場合は完全に同一の URL だけを重複排除し、推測による統合は行いません。

ダウンロード履歴は、書き込み可能な場合は実行ファイルの隣の `.uav-watcher` に保存されます。書き込めない場合は `%APPDATA%\UAV Downloader\watch` を使用します。

## AI 字幕：ダウンロード後に日／英／繁体字中国語 SRT を自動追加

- どちらの Windows GUI でも、ダウンロード前に **オフ／日本語／英語／繁体字中国語／3 言語**を選択できます。動画の完了後、選択した `.ja.srt`、`.en.srt`、`.zh-TW.srt` だけを隣に作成し、元の MP4 は変更しません。英語または中国語だけを要求して翻訳に失敗した場合、要求していない日本語 sidecar は残りません。字幕翻訳の既定は API Key 不要のローカルモードです。
- 日本語音声認識は常にローカルで実行されます。既定の **自動（推奨）** プロファイルは、固定 revision の [ReazonSpeech K2 v2](https://huggingface.co/reazon-research/reazonspeech-k2-v2) と [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) を使用します。一般的な CPU 向けに最適化され、専用 GPU は不要です。SHA-256 検証済みの認識パックは約 **178 MB** で、実際に字幕を初めて要求したときだけダウンロードされます。
- 従来の [whisper.cpp](https://github.com/ggml-org/whisper.cpp) プロファイルも明示的に選択できます：**高精度 large-v3-turbo q5（約 574 MB）**、**バランス small q5（約 190 MB）**、**高速 base q5（約 60 MB）**。自動モードがバランス版を追加ダウンロードして切り替えるのは、実行失敗、出力破損、またはタイムライン構造が無効な場合だけです。正常な文字列の品質を推測して勝手に切り替えることはありません。
- [公式 Silero VAD](https://huggingface.co/ggml-org/whisper-vad/tree/main) は発話ゲートとしてのみ使用されます。認識は元の無音を保持した、文脈を含む重複しないウィンドウで実行し、結果を動画の絶対時刻へ戻します。ReazonSpeech は CPU 効率の高い RNN-T デコードを使用し、3 種類の Whisper プロファイルは beam search、best-of 候補、temperature fallback を使用します。
- App は生成した字幕の認識プロファイルとパイプラインの provenance を記録します。いずれかが変更された場合は App 生成字幕と派生翻訳を再生成しますが、provenance のない既存 SRT やユーザーが編集した SRT は変更せずに保持します。
- ローカルの英語および台湾繁体字中国語翻訳には、固定版かつ SHA-256 検証済みの [FuguMT](https://huggingface.co/staka/fugumt-ja-en) と [OPUS-MT](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh) INT8 小型モデルを使用します。Google やその他の無料オンライン翻訳エンドポイントは使用しません。約 **147 MB** のローカル翻訳パックは、英語、繁体字中国語、または 3 言語の字幕処理を実際に開始したときだけダウンロードされます。オフと日本語のみではダウンロードせず、LLM API 使用時にも不要です。一度取得すればオフラインで再利用できます。
- オプションの API 拡張は **OpenAI、Anthropic、Gemini**、および **OpenAI-compatible** API に対応しています。互換モードでは DeepSeek、OpenRouter、Groq、Ollama、LiteLLM などへ接続できます。選択したサービスへ送信されるメディア由来の情報は認識済み字幕テキストだけです。必要な API 認証情報と通常の接続メタデータも送信されますが、動画と音声は常にローカルに残ります。
- ユーザーが入力した API Key は、現在サインインしている Windows アカウント用の Windows DPAPI で暗号化して保存されます。プロジェクトや EXE に API Key は含まれません。料金、使用量上限、データ処理、利用規約は選択したプロバイダーによって異なるため、使用前に最新条件を確認してください。
- ローカル翻訳では各 cue を元のタイムスタンプに固定し、メンテナーが作成・確認した 900 以上の成人向け文脈、安全／同意、撮影時のプライバシー、現場指示、日常表現に加え、保守的な台湾表現の補正とバージョン管理された exact-match 翻訳メモリを使用します。「止めて」と「止めないで」のような意味を反転させる恐れがある曖昧一致は使用しません。
- UAV Browser は動画ダウンロードと字幕処理を別々のキューで管理するため、バックグラウンド字幕処理は 1–32 個の動画ダウンロード枠を占有しません。
- 所要時間は CPU、動画の長さ、実際の発話量、選択した翻訳サービスによって異なります。3 言語モードは 1 回のローカル音声認識を共有し、ローカルモードでは中間翻訳結果も再利用して重複推論を避けます。

## 対応範囲

| サイト | UAV Browser の閲覧／検索／ダウンロード | UAV Watcher のカテゴリ監視 | Docker／CLI の URL ダウンロード |
|---|:---:|:---:|:---:|
| JableTV | ✓ | ✓ | ✓ |
| MissAV | ✓ | ✓ | ✓ |
| SupJav | ✓ | ✓ | ✓ |
| Hanime1 | ✓ | ✓ | ✓ |

サイトや CDN は予告なく変更される場合があります。動作しなくなった場合はまず最新版へ更新し、再現情報を添えて Issue を作成してください。

Hanime1 は公式 `watch?v=` URL、サイト内検索、ページ送りに加え、並べ替え、ジャンル、公開日、再生時間、全 240 タグを組み合わせた絞り込みに対応します。裏番／ショートアニメのコンパクト表示も解析できます。ダウンロード時に最新の署名付き MP4 URL を解決し、画質設定に従ってソースを選択します。最大 4 分割接続、レジューム、単一接続フォールバックも利用できます。

## ソースコードから実行

現在のコードには **Python 3.10+** と Tk が必要です。以前の README にあった Python 3.8+ という記載は、現在使用している構文には対応しません。

```bash
git clone https://github.com/Alos21750/UAV-Downloader.git
cd UAV-Downloader
python -m pip install -r requirements.txt
python -m pip install -e .

# フル GUI
uav-browser

# カテゴリ自動監視ツール
uav-watcher

# 1 件の URL を GUI なしで、保存先と動画ごとの 3 ワーカーを指定して実行
uav-browser --nogui --url "https://jable.tv/videos/example/" --output "/path/to/downloads" --max-workers-per-video 3
```

`-o` は `--output` の短縮形です。省略すると `./download` に保存されます。`--max-workers-per-video` は 1–16 を受け付け、Proxy の負荷を下げる場合は小さくできます。

Linux に Tk がない場合は、システムのパッケージマネージャーで `python3-tk` をインストールしてください。macOS／Linux はソースコードから実行し、インストール不要の EXE は Windows Release のみで提供されます。

## Docker / NAS

公開イメージは `ghcr.io/alos21750/uav-downloader:latest` です。GitHub Actions が amd64 と arm64 の両方をビルドします。

```bash
# 1 件の URL をダウンロードし、ホストのフォルダーを /downloads にマウント
docker run --rm -v "/path/to/downloads:/downloads" \
  ghcr.io/alos21750/uav-downloader:latest "https://jable.tv/videos/example/"

# docker compose：URL を直接渡す
docker compose run --rm uav "https://jable.tv/videos/example/"

# または ./downloads/urls.txt に 1 行 1 URL で記載
docker compose run --rm uav
```

利用可能な環境変数：

| 変数 | 用途 |
|---|---|
| `RESOLUTION` | `highest`、`1080`、`720`、`480`、`360`、`lowest` |
| `MAX_WORKERS_PER_VIDEO` | 動画ごとのセグメントワーカー上限（1–16）。小さくすると Proxy の負荷を軽減できます |
| `URL` / `URLS` | 1 件以上の URL を渡す |
| `URLS_FILE` | URL リスト。既定は `/downloads/urls.txt` |
| `DOWNLOAD_DIR` | コンテナ内の保存先。既定は `/downloads` |

Docker は GUI なしで処理完了後に終了するダウンロードジョブです。UAV Browser または UAV Watcher の GUI は含まれません。

## トラブルシューティング

[GitHub Issue](https://github.com/Alos21750/UAV-Downloader/issues/new) を作成するときは、次の情報を含めてください：

- App のバージョン、使用したツール、OS。
- サイトと再現可能な URL、期待した結果と実際の結果。
- クラッシュした場合は、実行ファイルの隣にある `crash_log.txt` または `crash_native.log`。
- Cookie、Proxy の認証情報、Token、その他の秘密情報はアップロードしないでください。

Proxy が必要な場合は、UAV Browser の「設定」または UAV Watcher 上部で、独自 Proxy、Windows システム Proxy、または無効を選択できます。2 つの GUI はこの設定を共有し、本アプリだけに適用します。Windows モードは現在、有効な手動 ProxyServer に対応しています。PAC 設定 URL は表示しますが実行せず、WPAD 自動検出にはまだ対応していません。

## Stars とプロジェクト活動

<p align="center">
  <img src="./docs/assets/star-history.svg" width="100%" alt="UAV Downloader の検証済み GitHub Star 履歴" />
</p>

グラフは本リポジトリの GitHub Actions によって生成されます。読み取り専用の repository token を使用し、現在の各 stargazer の `starredAt` 時刻だけを取得します。ユーザー名は要求も出力もしません。データまたはグラフ形式が変化した場合だけファイルを更新します。そのため、この曲線は「現在もリポジトリに Star を付けているアカウント」の追加日を示し、Star を解除したアカウントは含まれません。

<details>
<summary>以前の api.star-history.com 画像を使用しない理由</summary>

GitHub は 2026 年 7 月に stargazer リストへのアクセスを制限し、従来の匿名 Star History 画像エンドポイントが動作しなくなりました。本プロジェクトでは、README に Token を置くことなく壊れた画像を避けるため、独自の GitHub Actions 権限で静的 SVG を生成しています。参考：[GitHub の告知](https://github.blog/changelog/2026-06-30-upcoming-access-restrictions-to-public-api-endpoints-and-ui-views/) · [Star History の説明](https://www.star-history.com/blog/github-stargazer-api-restriction/)

</details>

## ライセンスと適切な利用

コードは [Apache License 2.0](./LICENSE) で提供されます。本ツールは合法的な個人利用または研究目的に限って使用してください。所在地の法律、サイトの規約、コンテンツの権利を守り、アクセス権限のあるコンテンツだけをダウンロードしてください。

バージョン変更と解決済みの問題は [Releases](https://github.com/Alos21750/UAV-Downloader/releases) を参照してください。

<p align="center"><a href="https://github.com/Alos21750">ALOS</a> が開発・保守しています。</p>
