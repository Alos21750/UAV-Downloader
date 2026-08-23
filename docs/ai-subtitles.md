# Local-first AI subtitles

UAV Browser and UAV Watcher can create selectable sidecar subtitles after a
video finishes downloading. The MP4 is not modified.

Available output modes are off, Japanese, English, Traditional Chinese, or all
three. Selected files use the same video stem with `.ja.srt`, `.en.srt`, and
`.zh-TW.srt` suffixes. If translation fails while only English or Traditional
Chinese was requested, an unrequested Japanese sidecar is not left behind.

## Speech recognition

The recommended automatic profile uses a pinned ReazonSpeech K2 v2 model with
sherpa-onnx. It is optimized for ordinary CPUs and does not require a discrete
GPU. The SHA-256-verified recognition pack is downloaded only when subtitles
are first requested.

Explicit whisper.cpp profiles remain available:

- Accurate: large-v3-turbo q5, about 574 MB.
- Balanced: small q5, about 190 MB.
- Fast: base q5, about 60 MB.

Silero VAD is used as a speech gate. Recognition windows preserve source
silence and are mapped back to absolute video time so that subtitles do not
collapse toward the beginning of the video. UAV Downloader records pipeline provenance
and regenerates its own outputs when the selected pipeline changes, while
preserving user-edited or unknown-origin SRT files.

## Translation

The default translator is local and requires no API key. It uses pinned INT8
FuguMT Japanese-to-English and OPUS-MT English-to-Chinese models. The local
translation pack is downloaded only when English, Traditional Chinese, or all
languages are actually requested, then reused offline.

The local pipeline preserves every cue boundary and timestamp. It applies a
versioned exact-match translation memory and curated terminology for adult
dialogue, consent and safety, filming privacy, on-set directions, and common
short phrases. It deliberately avoids fuzzy matching that could reverse
meaning in short commands.

Google and other quota-limited public translation endpoints are not used.

## Optional LLM APIs

Users may instead configure OpenAI, Anthropic, Gemini, or an
OpenAI-compatible endpoint such as DeepSeek, OpenRouter, Groq, Ollama, or
LiteLLM. Only recognized subtitle text is sent as media-derived content; video
and audio remain local. Normal API authentication and connection metadata are
still transmitted to the selected provider.

On Windows, user-supplied API keys are encrypted for the current account with
DPAPI. The repository and release executables contain no API key. Provider
pricing, quotas, retention, and acceptable-use terms remain the user's choice
and responsibility.

## Supply-chain verification

Release model packs are built from pinned inputs, gated by exact size and
SHA-256 values, included in `UAV_SHA256SUMS.txt`, and covered by GitHub artifact
attestations. License details are recorded in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
