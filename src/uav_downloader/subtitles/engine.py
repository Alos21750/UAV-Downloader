#!/usr/bin/env python
# coding: utf-8
"""Local-first subtitle generation shared by UAV Browser and UAV Watcher.

Japanese speech recognition runs via the official whisper.cpp Windows build.
English and Taiwan Traditional Chinese translation defaults to pinned,
integrity-checked local models.  A user can instead opt in to a configured LLM
provider, in which case only Japanese subtitle cue text is sent to that
provider; video and audio always remain on the computer.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Iterable, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from uav_downloader.core import config
from uav_downloader.core.paths import product_data_dir
from uav_downloader.metadata import PRODUCT_NAME
from uav_downloader.sites.base import locate_ffmpeg
from uav_downloader.core.ssl import SharedSSLAdapter
from uav_downloader.core.video_identity import normalize_source_subtitle_evidence


WHISPER_VERSION = 'v1.9.1'
WHISPER_ARCHIVE_URL = (
    'https://github.com/ggml-org/whisper.cpp/releases/download/'
    f'{WHISPER_VERSION}/whisper-bin-x64.zip'
)
WHISPER_ARCHIVE_SHA256 = (
    '7d8be46ecd31828e1eb7a2ecdd0d6b314feafd82163038ab6092594b0a063539'
)
WHISPER_ARCHIVE_SIZE = 7_982_101
WHISPER_RUNTIME_FILES = {
    'Release/whisper-cli.exe': (
        479_232,
        '58245314fb73b30fbd0cf0542c5c172e23f02b6eb7cad7b51e792439cf5e1755',
    ),
    'Release/whisper-vad-speech-segments.exe': (
        362_496,
        '69a4dbca1828afc3ffa17e5548ab4b96d866068c84db130980b764b15b15b2eb',
    ),
    'Release/whisper.dll': (
        1_366_016,
        'b31690c12461517fe9774e61318ab63a69972b948151feed98b913be35f708b6',
    ),
    'Release/ggml-base.dll': (
        656_384,
        '8be6f3e06388b3a9aac75d29bec86363e2e2f5b0cee86ce6438866bcac0bcf86',
    ),
    'Release/ggml.dll': (
        67_584,
        'db753141098018ab482796052a61e727ee0106cbc280f28397f6a111b5e667d7',
    ),
    'Release/ggml-cpu-alderlake.dll': (
        790_528,
        '323408503da53ccc67248b26d711f16d73d2d6239f7703a00a6a18b60ed5b8b8',
    ),
    'Release/ggml-cpu-cannonlake.dll': (
        833_536,
        '0f659d98b823bb871c7845787bba7485facd220099cf58aa773652b9b842ab2e',
    ),
    'Release/ggml-cpu-cascadelake.dll': (
        830_976,
        '8116b0e516134139de29400c536ecf06fe708ce1a078a96d30b562b30d524fbe',
    ),
    'Release/ggml-cpu-haswell.dll': (
        791_552,
        'e5925923a47672392f9e9c8c92e4b9b65ea473948bf4f568a0300a3a42485135',
    ),
    'Release/ggml-cpu-icelake.dll': (
        830_976,
        'b726d528bee0c811c6b2ad8775357379d651cabb487bbf800331697fe73da187',
    ),
    'Release/ggml-cpu-sandybridge.dll': (
        783_360,
        '1c49c64817233b2447ca305b41c66afa4bed31b058bc190a98af2a30cc703542',
    ),
    'Release/ggml-cpu-skylakex.dll': (
        833_536,
        '06082dc62a09a82fbba4aab49b2c049b96db84c5fc561a446a8ddbfb9b20bf86',
    ),
    'Release/ggml-cpu-sse42.dll': (
        772_096,
        '9a8f55ff1dfad231aa6250ac52c330c5bfa5c4c37691c8b591a68b52090ce40c',
    ),
    'Release/ggml-cpu-x64.dll': (
        776_704,
        '45ff644d301b8a1fffc7c5e3864205047360eb197814c7311f366d106bb5b19f',
    ),
}

WHISPER_MODEL_URL_PREFIX = (
    'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/')


@dataclass(frozen=True)
class RecognitionProfile:
    key: str
    model_name: str
    model_size: int
    model_sha256: str
    engine: str = 'whisper'

    @property
    def model_url(self) -> str:
        return WHISPER_MODEL_URL_PREFIX + self.model_name


REAZONSPEECH_PACK_VERSION = '1'
REAZONSPEECH_PACK_NAME = (
    f'UAV_reazonspeech_asr_v{REAZONSPEECH_PACK_VERSION}.zip')
REAZONSPEECH_PACK_RELEASE_TAG = 'v3.1.0'
REAZONSPEECH_PACK_URL = (
    'https://github.com/Alos21750/UAV-Downloader/'
    f'releases/download/{REAZONSPEECH_PACK_RELEASE_TAG}/'
    f'{REAZONSPEECH_PACK_NAME}'
)
REAZONSPEECH_PACK_SIZE = 186_186_197
REAZONSPEECH_PACK_SHA256 = (
    'cf55e5485e14715beee6e0b12ca2b0998ad73ec755513e80138fa5161693c700'
)
REAZONSPEECH_MANIFEST_SHA256 = (
    'dceb4ab39275828596eebe6148243ef12cd88a8e2c9a9bef7c9f69a1d61b652c'
)
REAZONSPEECH_MODEL_NAME = 'reazonspeech-k2-v2-hybrid-int8-fp32'
# The immutable pack manifest is the canonical model/runtime identity. Keeping
# a separate, unauditable "combined" digest here would create false assurance.
REAZONSPEECH_MODEL_SHA256 = REAZONSPEECH_MANIFEST_SHA256
REAZONSPEECH_MODEL_REVISION = (
    '291488c8151be24d7da4bf7af26e533fad96e407')
REAZONSPEECH_RUNTIME_VERSION = 'v1.13.4'
REAZONSPEECH_RUNTIME_COMMIT = (
    '142807252687d81b40d6315f23470a1512a00de3')
REAZONSPEECH_REQUIRED_FILES = {
    'licenses/Apache-2.0.txt': (
        11_358,
        'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
    ),
    'licenses/ONNXRuntime-MIT.txt': (
        1_073,
        '2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c',
    ),
    'licenses/ONNXRuntime-ThirdPartyNotices.txt': (
        325_054,
        '0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2',
    ),
    'model/MODEL_CARD.md': (
        1_188,
        '7debad4c9430f3310ad6d119fce385787c1c19f3ebc0cabe685a48dbe72a4de0',
    ),
    'model/decoder-epoch-99-avg-1.onnx': (
        11_767_836,
        '58b18211ae06265466bfa17172dab574df94f76c8bcb61a3640c28ba860e4124',
    ),
    'model/encoder-epoch-99-avg-1.int8.onnx': (
        154_670_139,
        '2c7bd08a8a99f9ddd0d9e458456577b1f6279214e51426f114f9eced44c54e1d',
    ),
    'model/joiner-epoch-99-avg-1.int8.onnx': (
        2_696_970,
        '49cc7ea1d3d35a40a27442db5e89996da64bf0e683a903dce76e99e57a12e4de',
    ),
    'model/tokens.txt': (
        45_754,
        '2c3ac659818a48a0c04010e0593bbc4d7c8a24a054340b01131499c05fd52def',
    ),
    'runtime/onnxruntime.dll': (
        14_438_400,
        '7813cfb15cbcd05c567776a055806920924f2543cfbe1f38850e3bb1410e62bd',
    ),
    'runtime/onnxruntime_providers_shared.dll': (
        104_960,
        'cf52b931b9a76cde2290f654dbad81f037b5de19f75c0a34ec82ebdaf4a555d8',
    ),
    'runtime/sherpa-onnx-offline.exe': (
        2_117_120,
        '5ea3f02ee15e1af3fbf786568dd3f5ed85e67b302883e4cb763cf8ec2c93bb5a',
    ),
}
REAZONSPEECH_BATCH_SIZE = 4
REAZONSPEECH_MAX_BATCH_BYTES = 8 * 1024 * 1024
REAZONSPEECH_PIPELINE_VERSION = (
    'reazonspeech-k2-v2-sherpa-onnx-v1.13.4-context-batch-v1')
REAZONSPEECH_MIN_TIMEOUT_SECONDS = 5 * 60
REAZONSPEECH_STALL_TIMEOUT_SECONDS = 5 * 60
REAZONSPEECH_MAX_TOKENS_PER_WINDOW = 4096
REAZONSPEECH_MAX_TOKEN_CHARS = 128
REAZONSPEECH_CUE_MAX_CHARS = 28
REAZONSPEECH_CUE_MAX_SECONDS = 6.0
REAZONSPEECH_CUE_PAUSE_SECONDS = 0.8
REAZONSPEECH_CUE_PUNCTUATION = '。！？!?…'

RECOGNITION_PROFILES = {
    'auto': RecognitionProfile(
        'auto',
        REAZONSPEECH_MODEL_NAME,
        REAZONSPEECH_PACK_SIZE,
        REAZONSPEECH_MODEL_SHA256,
        'reazonspeech',
    ),
    'quality': RecognitionProfile(
        'quality',
        'ggml-large-v3-turbo-q5_0.bin',
        574_041_195,
        '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2',
    ),
    'balanced': RecognitionProfile(
        'balanced',
        'ggml-small-q5_1.bin',
        190_085_487,
        'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb',
    ),
    'fast': RecognitionProfile(
        'fast',
        'ggml-base-q5_1.bin',
        59_707_625,
        '422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898',
    ),
}

# Compatibility mirrors retained for callers and old tests that import the
# original single-model constants. Runtime selection uses RecognitionProfile.
MODEL_NAME = RECOGNITION_PROFILES['fast'].model_name
MODEL_URL = RECOGNITION_PROFILES['fast'].model_url
MODEL_SHA256 = RECOGNITION_PROFILES['fast'].model_sha256
MODEL_SIZE = RECOGNITION_PROFILES['fast'].model_size

VAD_MODEL_NAME = 'ggml-silero-v6.2.0.bin'
VAD_MODEL_URL = (
    'https://huggingface.co/ggml-org/whisper-vad/resolve/main/'
    + VAD_MODEL_NAME
)
VAD_MODEL_SHA256 = '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987'
VAD_MODEL_SIZE = 885_098

TRANSLATION_PACK_VERSION = '1'
TRANSLATION_PACK_NAME = f'UAV_local_translation_v{TRANSLATION_PACK_VERSION}.zip'
TRANSLATION_PACK_RELEASE_TAG = 'v3.1.0'
TRANSLATION_PACK_URL = (
    'https://github.com/Alos21750/UAV-Downloader/'
    f'releases/download/{TRANSLATION_PACK_RELEASE_TAG}/'
    f'{TRANSLATION_PACK_NAME}'
)
# Generated by scripts/build_local_translation_pack.py from the pinned
# CC BY-SA 4.0 FuguMT ja-en and Apache-2.0 OPUS-MT en-zh revisions documented
# in the pack manifest. These values change only with a reproducible rebuild.
TRANSLATION_PACK_SHA256 = (
    '1259a2abeb5026411da39c6f3dcd69ebd70bed654ac9cfaaee0c7373867c0bb4'
)
TRANSLATION_PACK_SIZE = 147_330_565
TRANSLATION_MANIFEST_SHA256 = (
    '1ac2f8c0a0bae7f934b3d8eb765e8f2b199a02bcecf6ab7f31396915a5f1a135'
)
TRANSLATION_ENGINE_VERSION = (
    f'fugumt-opus-ct2-int8-v{TRANSLATION_PACK_VERSION}')
VALID_SUBTITLE_MODES = {'none', 'ja', 'en', 'zh', 'all'}
VALID_RECOGNITION_QUALITIES = frozenset(RECOGNITION_PROFILES)

# v1.9.1 Silero validation: 0.35 rejected silence, low-level noise, tones,
# and synthetic non-verbal vowels while retaining all speech in the timing
# and Common Voice fixtures. Lower thresholds created false subtitle windows.
VAD_THRESHOLD = 0.35
VAD_MIN_SPEECH_MS = 100
VAD_MAX_SPEECH_SECONDS = 28.0
VAD_SPEECH_PAD_MS = 0
VAD_SAMPLES_OVERLAP_SECONDS = 0.2
VAD_MERGE_GAP_SECONDS = 2.0
RECOGNITION_WINDOW_SPEECH_SPAN_SECONDS = 25.0
RECOGNITION_WINDOW_PADDING_SECONDS = 0.5
RECOGNITION_WINDOW_MAX_SECONDS = 28.0
MAX_VAD_ISLANDS = 20_000
MAX_WHISPER_SEGMENTS_PER_ISLAND = 512
MAX_WHISPER_CUES = 100_000
MAX_WHISPER_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_WHISPER_TEXT_CHARS = 16_384
MAX_SRT_FILE_BYTES = 32 * 1024 * 1024
WHISPER_SAMPLE_RATE = 16_000
WHISPER_BATCH_SIZE = 180
ASR_TEMP_RESERVE_BYTES = 64 * 1024 * 1024
SUBTITLE_PROCESS_HEARTBEAT_SECONDS = 1.0
AUDIO_EXTRACTION_TIMEOUT_SECONDS = 30 * 60
VAD_MIN_TIMEOUT_SECONDS = 5 * 60
WHISPER_MIN_TIMEOUT_SECONDS = 15 * 60
WHISPER_STALL_TIMEOUT_SECONDS = 15 * 60
LOCAL_TRANSLATION_WORKER_SCHEMA = 1
LOCAL_TRANSLATION_WORKER_MAX_BYTES = 64 * 1024 * 1024
LOCAL_TRANSLATION_WORKER_TIMEOUT_SECONDS = 2 * 60 * 60
LOCAL_TRANSLATION_WORKER_STALL_TIMEOUT_SECONDS = 15 * 60
ASR_PIPELINE_VERSION = 'whisper-cpp-v1.9.1-external-vad-context-batch-v4'
SUBTITLE_PROVENANCE_SCHEMA = 1
SUBTITLE_PROVENANCE_KIND = 'uav_subtitle_provenance'
LEGACY_SUBTITLE_PROVENANCE_KINDS = frozenset({
    'jable_subtitle_provenance',
})
SUBTITLE_GENERATOR = 'uav'
MANAGED_SUBTITLE_GENERATORS = frozenset({SUBTITLE_GENERATOR, 'jable'})
MAX_SUBTITLE_PROVENANCE_BYTES = 64 * 1024

_RECOGNITION_DEFAULT = object()

ProgressCallback = Callable[[str, Optional[int]], None]
CancelCheck = Callable[[], bool]
TranslationProgressCallback = Callable[[int, int], None]
RecognitionOutcomeCallback = Callable[[RecognitionProfile, bool], None]

_runtime_lock = threading.Lock()
_generation_lock = threading.Lock()
_translation_runtime_lock = threading.Lock()
_reazonspeech_runtime_lock = threading.Lock()
_translation_memory_lock = threading.Lock()
_translation_provider_state = threading.local()
_recognition_profile_state = threading.local()
_verified_paths: dict[str, tuple[int, int, int, int, int, str]] = {}


class SubtitleError(RuntimeError):
    """A user-facing subtitle generation failure."""


class SubtitleCancelled(SubtitleError):
    """Raised when the owning download job is cancelled."""


class SubtitleProcessTimeout(SubtitleError):
    """Raised when a supervised native subtitle process stops responding."""


class SubtitleStorageError(SubtitleError):
    """Raised before temporary ASR files can exhaust the destination disk."""


class ReazonSpeechStructuralError(SubtitleError):
    """A bounded ReazonSpeech failure eligible for the Whisper fallback."""


@dataclass(frozen=True)
class SubtitleResult:
    files: tuple[str, ...]
    generated: tuple[str, ...]
    no_speech: bool = False
    satisfied_by_source: tuple[str, ...] = ()


@dataclass(frozen=True)
class SrtCue:
    index: str
    timing: str
    text: str


@dataclass(frozen=True)
class SpeechIsland:
    start: float
    end: float


@dataclass(frozen=True)
class RecognizedSegment:
    start: float
    end: float
    text: str


def normalize_recognition_quality(value) -> str:
    quality = str(value or '').strip().lower().replace('_', '-')
    aliases = {
        '': 'auto',
        'default': 'auto',
        'automatic': 'auto',
        'recommended': 'auto',
        'cpu': 'auto',
        'precise': 'quality',
        'precision': 'quality',
        'accurate': 'quality',
        'accuracy': 'quality',
        'high': 'quality',
        'best': 'quality',
        'large': 'quality',
        'large-v3-turbo': 'quality',
        'medium': 'balanced',
        'normal': 'balanced',
        'small': 'balanced',
        'speed': 'fast',
        'quick': 'fast',
        'base': 'fast',
        'legacy': 'fast',
    }
    quality = aliases.get(quality, quality)
    return quality if quality in VALID_RECOGNITION_QUALITIES else 'auto'


def _physical_cpu_count() -> int:
    """Return the number of physical CPU cores without optional packages."""
    fallback = max(1, int(os.cpu_count() or 4))
    if sys.platform != 'win32':
        return fallback
    try:
        import ctypes

        relation_processor_core = 0
        required = ctypes.c_ulong(0)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetLogicalProcessorInformationEx(
            relation_processor_core, None, ctypes.byref(required))
        if required.value <= 0 or required.value > 16 * 1024 * 1024:
            return fallback
        buffer = (ctypes.c_ubyte * required.value)()
        if not kernel32.GetLogicalProcessorInformationEx(
                relation_processor_core, buffer, ctypes.byref(required)):
            return fallback
        offset = 0
        cores = 0
        while offset + 8 <= required.value:
            relationship = int.from_bytes(
                bytes(buffer[offset:offset + 4]), 'little')
            size = int.from_bytes(
                bytes(buffer[offset + 4:offset + 8]), 'little')
            if size < 8 or offset + size > required.value:
                return fallback
            if relationship == relation_processor_core:
                cores += 1
            offset += size
        return max(1, cores) if offset == required.value else fallback
    except Exception:
        return fallback


def resolve_recognition_quality(value) -> str:
    """Resolve a persisted selection to its pinned recognition profile."""
    return normalize_recognition_quality(value)


def _recognition_thread_count() -> int:
    return max(1, min(8, _physical_cpu_count()))


def recognition_profile(value=_RECOGNITION_DEFAULT) -> RecognitionProfile:
    if value is _RECOGNITION_DEFAULT:
        pinned = getattr(_recognition_profile_state, 'profile', None)
        if isinstance(pinned, RecognitionProfile):
            return pinned
        else:
            getter = getattr(config, 'get_recognition_quality', None)
            try:
                value = getter() if callable(getter) else None
            except Exception:
                value = None
    return RECOGNITION_PROFILES[resolve_recognition_quality(value)]


@contextmanager
def _recognition_profile_scope(profile: RecognitionProfile):
    had_previous = hasattr(_recognition_profile_state, 'profile')
    previous = getattr(_recognition_profile_state, 'profile', None)
    _recognition_profile_state.profile = profile
    try:
        yield
    finally:
        if had_previous:
            _recognition_profile_state.profile = previous
        else:
            try:
                del _recognition_profile_state.profile
            except AttributeError:
                pass


def normalize_subtitle_mode(value) -> str:
    mode = str(value or '').strip().lower()
    aliases = {
        'off': 'none', 'disabled': 'none',
        'jp': 'ja', 'japanese': 'ja',
        'english': 'en',
        'zh-tw': 'zh', 'traditional-chinese': 'zh', 'chinese': 'zh',
        'multi': 'all', 'multilingual': 'all',
    }
    mode = aliases.get(mode, mode)
    return mode if mode in VALID_SUBTITLE_MODES else 'none'


def subtitle_languages(mode) -> tuple[str, ...]:
    mode = normalize_subtitle_mode(mode)
    if mode == 'all':
        return ('ja', 'en', 'zh-TW')
    if mode == 'zh':
        return ('zh-TW',)
    if mode in ('ja', 'en'):
        return (mode,)
    return ()


def subtitle_paths(video_path: str) -> dict[str, str]:
    stem = os.path.splitext(os.path.abspath(video_path))[0]
    return {
        'ja': stem + '.ja.srt',
        'en': stem + '.en.srt',
        'zh-TW': stem + '.zh-TW.srt',
    }


def parse_srt(text: str) -> list[SrtCue]:
    normalized = str(text or '').lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    blocks = re.split(r'\n[ \t]*\n', normalized.strip()) if normalized.strip() else []
    cues: list[SrtCue] = []
    for block in blocks:
        lines = block.split('\n')
        if len(lines) < 3 or '-->' not in lines[1]:
            raise SubtitleError('Whisper produced an invalid SRT subtitle file')
        cue_text = '\n'.join(lines[2:]).strip()
        cues.append(SrtCue(lines[0].strip(), lines[1].strip(), cue_text))
    return cues


def render_srt(cues: Iterable[SrtCue]) -> str:
    parts = []
    for cue in cues:
        parts.append(f'{cue.index}\n{cue.timing}\n{cue.text.strip()}')
    return '\n\n'.join(parts) + ('\n' if parts else '')


def _read_srt_text(path: str) -> str:
    try:
        with open(path, 'rb') as handle:
            payload = handle.read(MAX_SRT_FILE_BYTES + 1)
    except OSError as exc:
        raise SubtitleError('Subtitle file could not be read') from exc
    if len(payload) > MAX_SRT_FILE_BYTES:
        raise SubtitleError('Subtitle file is too large')
    try:
        if payload.startswith((b'\xff\xfe', b'\xfe\xff')):
            return payload.decode('utf-16')
        return payload.decode('utf-8-sig')
    except UnicodeError as exc:
        raise SubtitleError('Subtitle file encoding is unsupported') from exc


def _cache_root() -> str:
    base = (os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
            or os.path.join(os.path.expanduser('~'), '.cache'))
    return str(product_data_dir(base) / 'subtitle_tools')


def _component_search_directories() -> tuple[str, ...]:
    """Return trusted locations for manually supplied, pinned components.

    Frozen users naturally place an offline pack beside the executable.  A
    source checkout can use the entry-point directory or current directory.
    Every candidate still has to pass the immutable size and SHA-256 gate (or
    the per-file signed manifest gate for an extracted directory).
    """
    candidates: list[str] = []
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        try:
            candidates.append(os.path.dirname(os.path.abspath(sys.argv[0])))
        except (AttributeError, IndexError, OSError, TypeError):
            pass
        candidates.append(os.getcwd())

    resolved: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = os.path.abspath(candidate)
        key = os.path.normcase(path)
        if key in seen or not os.path.isdir(path):
            continue
        seen.add(key)
        resolved.append(path)
    return tuple(resolved)


def _component_install_candidates(pack_name: str) -> tuple[str, ...]:
    stem = os.path.splitext(os.path.basename(pack_name))[0]
    candidates: list[str] = []
    for root in _component_search_directories():
        candidates.extend((
            os.path.join(root, stem),
            os.path.join(root, 'components', stem),
            root,
        ))
    return tuple(dict.fromkeys(os.path.abspath(path) for path in candidates))


@contextmanager
def _interprocess_cache_lock(
        name: str, cancel_check: Optional[CancelCheck],
        timeout_seconds: float = 15 * 60):
    """Serialize shared-cache installation across both frozen applications."""
    lock_dir = os.path.join(_cache_root(), '.locks')
    os.makedirs(lock_dir, exist_ok=True)
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '-', str(name or 'cache'))
    path = os.path.join(lock_dir, safe_name + '.lock')
    handle = open(path, 'a+b')
    acquired = False
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()

        while not acquired:
            _check_cancel(cancel_check)
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise SubtitleError(
                        'Timed out waiting for another subtitle model install')
                time.sleep(0.1)
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _notify(callback: Optional[ProgressCallback], stage: str,
            percent: Optional[int] = None) -> None:
    if callback:
        callback(stage, percent)


def _check_cancel(cancel_check: Optional[CancelCheck]) -> None:
    if cancel_check and cancel_check():
        raise SubtitleCancelled('Subtitle generation cancelled')


def _session() -> requests.Session:
    session = requests.Session()
    session.mount('https://', SharedSSLAdapter(pool_connections=4, pool_maxsize=4))
    session.headers.update({
        'User-Agent': config.headers.get('User-Agent', PRODUCT_NAME),
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8,ja;q=0.7',
    })
    return session


def _sha256(
        path: str,
        cancel_check: Optional[CancelCheck] = None) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            _check_cancel(cancel_check)
            digest.update(chunk)
    return digest.hexdigest()


def _is_verified(
        path: str, expected_size: int, expected_sha256: str,
        cancel_check: Optional[CancelCheck] = None) -> bool:
    key = os.path.abspath(path)
    try:
        before = os.stat(path)
        if not os.path.isfile(path) or before.st_size != expected_size:
            _verified_paths.pop(key, None)
            return False
        expected_hash = expected_sha256.lower()
        identity = (
            int(before.st_size),
            int(before.st_mtime_ns),
            int(before.st_ctime_ns),
            int(getattr(before, 'st_ino', 0)),
            int(expected_size),
            expected_hash,
        )
        if _verified_paths.get(key) == identity:
            return True
        if _sha256(path, cancel_check).lower() != expected_hash:
            _verified_paths.pop(key, None)
            return False
        after = os.stat(path)
        after_identity = (
            int(after.st_size),
            int(after.st_mtime_ns),
            int(after.st_ctime_ns),
            int(getattr(after, 'st_ino', 0)),
            int(expected_size),
            expected_hash,
        )
        if after_identity != identity:
            _verified_paths.pop(key, None)
            return False
    except OSError:
        _verified_paths.pop(key, None)
        return False
    _verified_paths[key] = identity
    return True


def _find_verified_component_archive(
        filename: str, expected_size: int, expected_sha256: str,
        cancel_check: Optional[CancelCheck]) -> Optional[str]:
    for root in _component_search_directories():
        for candidate in (
                os.path.join(root, filename),
                os.path.join(root, 'components', filename)):
            if _is_verified(
                    candidate, expected_size, expected_sha256,
                    cancel_check):
                return candidate
    return None


def _download_verified(url: str, destination: str, expected_size: int,
                       expected_sha256: str, stage: str,
                       progress_callback: Optional[ProgressCallback],
                       cancel_check: Optional[CancelCheck]) -> str:
    if _is_verified(
            destination, expected_size, expected_sha256, cancel_check):
        return destination

    part = destination + '.part'
    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        try:
            os.remove(part)
        except FileNotFoundError:
            pass
    except OSError as exc:
        raise SubtitleError(
            'Subtitle component storage is unavailable') from exc
    try:
        free_bytes = shutil.disk_usage(
            os.path.dirname(destination)).free
    except OSError:
        free_bytes = None
    required_bytes = expected_size + 64 * 1024 * 1024
    if free_bytes is not None and free_bytes < required_bytes:
        raise SubtitleError(
            'Not enough free disk space for the subtitle component')

    session = _session()
    try:
        kwargs = config.proxy_request_kwargs()
        # A short per-read timeout keeps cancellation responsive when a model
        # host stalls; the timeout resets whenever another chunk arrives.
        with session.get(url, stream=True, timeout=(15, 15), **kwargs) as response:
            response.raise_for_status()
            received = 0
            total = int(response.headers.get('Content-Length') or expected_size)
            with open(part, 'wb') as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    _check_cancel(cancel_check)
                    if not chunk:
                        continue
                    if received + len(chunk) > expected_size:
                        raise SubtitleError(
                            'Downloaded subtitle component is larger than expected')
                    handle.write(chunk)
                    received += len(chunk)
                    pct = int(received * 100 / total) if total > 0 else None
                    _notify(progress_callback, stage, min(100, pct) if pct is not None else None)
                handle.flush()
                os.fsync(handle.fileno())
        if not _is_verified(
                part, expected_size, expected_sha256, cancel_check):
            raise SubtitleError('Downloaded subtitle component failed integrity verification')
        os.replace(part, destination)
        _verified_paths.pop(os.path.abspath(part), None)
        _verified_paths.pop(os.path.abspath(destination), None)
        if not _is_verified(
                destination, expected_size, expected_sha256, cancel_check):
            raise SubtitleError('Installed subtitle component failed integrity verification')
        return destination
    except SubtitleCancelled:
        raise
    except SubtitleError:
        raise
    except Exception as exc:
        raise SubtitleError(f'Unable to download subtitle component: {type(exc).__name__}') from exc
    finally:
        session.close()
        try:
            os.remove(part)
        except FileNotFoundError:
            pass


def _find_whisper_exe(folder: str) -> Optional[str]:
    if not os.path.isdir(folder):
        return None
    for root, _dirs, files in os.walk(folder):
        for filename in files:
            if filename.lower() == 'whisper-cli.exe':
                return os.path.join(root, filename)
    return None


def _verify_whisper_install(
        folder: str, require_marker: bool = True,
        cancel_check: Optional[CancelCheck] = None) -> Optional[str]:
    _check_cancel(cancel_check)
    if not os.path.isdir(folder):
        return None
    if require_marker:
        try:
            with open(
                    os.path.join(folder, '.source-sha256'),
                    'r', encoding='ascii') as handle:
                if (
                        handle.read().strip().lower()
                        != WHISPER_ARCHIVE_SHA256.lower()):
                    return None
        except OSError:
            return None
    for relative_path, (expected_size, expected_sha256) in (
            WHISPER_RUNTIME_FILES.items()):
        _check_cancel(cancel_check)
        path = os.path.join(folder, *relative_path.split('/'))
        # Native executables are security-sensitive and small enough to hash
        # on every install check.  Windows can preserve size and timestamps
        # across a rapid same-length overwrite, so the metadata cache alone
        # cannot safely prove that these files are unchanged.
        _verified_paths.pop(os.path.abspath(path), None)
        if not _is_verified(
                path, expected_size, expected_sha256, cancel_check):
            return None
    return os.path.join(folder, 'Release', 'whisper-cli.exe')


def _safe_extract_zip(
        archive: str, destination: str,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
        progress_stage: Optional[str] = None,
        progress_start: int = 0,
        progress_end: int = 100) -> None:
    root = os.path.abspath(destination)
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > 4096:
                raise SubtitleError(
                    'Subtitle component archive contains too many files')
            total_size = sum(info.file_size for info in infos)
            if total_size > 300 * 1024 * 1024:
                raise SubtitleError(
                    'Subtitle component archive is unexpectedly large')

            seen: set[str] = set()
            extracted = 0
            if progress_stage:
                _notify(progress_callback, progress_stage, progress_start)
            for info in infos:
                _check_cancel(cancel_check)
                member = info.filename.replace('\\', '/')
                is_directory = info.is_dir() or member.endswith('/')
                member = member.rstrip('/')
                parts = member.split('/')
                normalized_member = os.path.normcase(member).casefold()
                if (
                        not member
                        or member.startswith('/')
                        or ':' in member
                        or any(part in ('', '.', '..') for part in parts)
                        or normalized_member in seen):
                    raise SubtitleError(
                        'Unsafe path found in subtitle component archive')
                seen.add(normalized_member)
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise SubtitleError(
                        'Unsafe link found in subtitle component archive')
                if is_directory:
                    continue
                target = os.path.abspath(os.path.join(root, *parts))
                if os.path.commonpath([root, target]) != root:
                    raise SubtitleError(
                        'Unsafe path found in subtitle component archive')
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with bundle.open(info) as source, open(target, 'wb') as output:
                    while True:
                        _check_cancel(cancel_check)
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        extracted += len(chunk)
                        if progress_stage and total_size:
                            percent = progress_start + int(
                                extracted * (progress_end - progress_start)
                                / total_size)
                            _notify(
                                progress_callback, progress_stage,
                                min(progress_end, percent))
            if progress_stage:
                _notify(progress_callback, progress_stage, progress_end)
    except (SubtitleCancelled, SubtitleError):
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SubtitleError(
            'Subtitle component archive could not be extracted') from exc


def _path_has_link_component(root: str, path: str) -> bool:
    """Reject symlink/junction substitution inside a verified install."""
    root = os.path.abspath(root)
    path = os.path.abspath(path)

    def is_link_or_reparse(candidate: str) -> bool:
        try:
            if os.path.islink(candidate):
                return True
            is_junction = getattr(os.path, 'isjunction', None)
            if callable(is_junction) and is_junction(candidate):
                return True
            attributes = int(
                getattr(os.lstat(candidate), 'st_file_attributes', 0))
            # FILE_ATTRIBUTE_REPARSE_POINT. This also covers less-common
            # Windows reparse types that are neither a symlink nor junction.
            return bool(attributes & 0x400)
        except FileNotFoundError:
            return False
        except OSError:
            return True

    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return True
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return True
    if is_link_or_reparse(root):
        return True
    if relative == '.':
        return False
    current = root
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if is_link_or_reparse(current):
            return True
    return False


def _read_reazonspeech_manifest(
        root: str, cancel_check: Optional[CancelCheck] = None) -> dict:
    _check_cancel(cancel_check)
    manifest_path = os.path.join(root, 'manifest.json')
    if _path_has_link_component(root, manifest_path):
        raise SubtitleError(
            'ReazonSpeech model manifest path is unsafe')
    try:
        payload = _read_file_bounded(manifest_path, 64 * 1024)
    except SubtitleError as exc:
        raise SubtitleError(
            'ReazonSpeech model manifest is missing') from exc
    if hashlib.sha256(payload).hexdigest().lower() != (
            REAZONSPEECH_MANIFEST_SHA256.lower()):
        raise SubtitleError(
            'ReazonSpeech model manifest failed integrity verification')

    def reject_constant(_value):
        raise ValueError('non-finite JSON number')

    try:
        manifest = json.loads(
            payload.decode('utf-8'), parse_constant=reject_constant)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SubtitleError(
            'ReazonSpeech model manifest is invalid') from exc
    if (
            not isinstance(manifest, dict)
            or manifest.get('format')
            != 'jable-reazonspeech-asr-pack'
            or str(manifest.get('pack_version'))
            != REAZONSPEECH_PACK_VERSION):
        raise SubtitleError(
            'ReazonSpeech model manifest is invalid')

    model = manifest.get('model')
    runtime = manifest.get('runtime')
    if (
            not isinstance(model, dict)
            or model.get('path') != 'model'
            or model.get('revision') != REAZONSPEECH_MODEL_REVISION
            or not isinstance(runtime, dict)
            or runtime.get('path') != 'runtime'
            or runtime.get('version') != REAZONSPEECH_RUNTIME_VERSION):
        raise SubtitleError(
            'ReazonSpeech model manifest is incomplete')
    # The official manifest also pins the sherpa-onnx source commit. Tiny
    # test packs may omit it, but a conflicting value is never accepted.
    if runtime.get('commit', REAZONSPEECH_RUNTIME_COMMIT) != (
            REAZONSPEECH_RUNTIME_COMMIT):
        raise SubtitleError(
            'ReazonSpeech runtime manifest is invalid')

    entries = manifest.get('files')
    if (
            not isinstance(entries, list)
            or len(entries) != len(REAZONSPEECH_REQUIRED_FILES)):
        raise SubtitleError(
            'ReazonSpeech model manifest file list is invalid')
    seen: set[str] = set()
    for entry in entries:
        _check_cancel(cancel_check)
        if not isinstance(entry, dict):
            raise SubtitleError(
                'ReazonSpeech model manifest file list is invalid')
        path = entry.get('path')
        if not isinstance(path, str):
            raise SubtitleError(
                'ReazonSpeech model manifest file list is invalid')
        normalized = path.replace('\\', '/')
        expected = REAZONSPEECH_REQUIRED_FILES.get(normalized)
        if expected is None or normalized in seen:
            raise SubtitleError(
                'ReazonSpeech model manifest contains an unreviewed file')
        seen.add(normalized)
        try:
            size = entry['size']
            sha256 = entry['sha256']
        except KeyError as exc:
            raise SubtitleError(
                'ReazonSpeech model manifest file list is invalid') from exc
        if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or not isinstance(sha256, str)
                or (size, sha256.lower()) != expected):
            raise SubtitleError(
                'ReazonSpeech model manifest file metadata is invalid')
    if seen != set(REAZONSPEECH_REQUIRED_FILES):
        raise SubtitleError(
            'ReazonSpeech model manifest is incomplete')
    return manifest


def _verify_reazonspeech_install(
        root: str, require_marker: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
        progress_stage: Optional[str] = None,
        progress_start: int = 0,
        progress_end: int = 100) -> str:
    _check_cancel(cancel_check)
    root = os.path.abspath(root)
    if not os.path.isdir(root) or _path_has_link_component(root, root):
        raise SubtitleError(
            'ReazonSpeech model install is missing')
    required_files = set(REAZONSPEECH_REQUIRED_FILES)
    required_files.add('manifest.json')
    allowed_files = set(required_files)
    allowed_files.add('.source-sha256')
    allowed_directories = {
        part
        for relative in allowed_files
        for part in (
            '/'.join(relative.split('/')[:index])
            for index in range(1, len(relative.split('/')))
        )
        if part
    }
    seen_files: set[str] = set()
    try:
        for current, directories, filenames in os.walk(
                root, topdown=True, followlinks=False):
            _check_cancel(cancel_check)
            current_relative = os.path.relpath(current, root)
            prefix = (
                '' if current_relative == '.'
                else current_relative.replace('\\', '/') + '/')
            for directory in directories:
                relative = prefix + directory
                path = os.path.join(current, directory)
                if (
                        relative not in allowed_directories
                        or _path_has_link_component(root, path)):
                    raise SubtitleError(
                        'ReazonSpeech model install contains '
                        'an unreviewed path')
            for filename in filenames:
                relative = prefix + filename
                path = os.path.join(current, filename)
                if (
                        relative not in allowed_files
                        or relative in seen_files
                        or _path_has_link_component(root, path)):
                    raise SubtitleError(
                        'ReazonSpeech model install contains '
                        'an unreviewed file')
                seen_files.add(relative)
    except OSError as exc:
        raise SubtitleError(
            'ReazonSpeech model install could not be inspected') from exc
    if (
            not required_files.issubset(seen_files)
            or (require_marker and '.source-sha256' not in seen_files)):
        raise SubtitleError(
            'ReazonSpeech model install is incomplete')
    if require_marker:
        marker = os.path.join(root, '.source-sha256')
        if _path_has_link_component(root, marker):
            raise SubtitleError(
                'ReazonSpeech model marker is unsafe')
        try:
            with open(marker, 'r', encoding='ascii') as handle:
                marker_value = handle.read(128).strip().lower()
        except OSError as exc:
            raise SubtitleError(
                'ReazonSpeech model marker is missing') from exc
        if marker_value != REAZONSPEECH_PACK_SHA256.lower():
            raise SubtitleError(
                'ReazonSpeech model marker is invalid')

    _read_reazonspeech_manifest(root, cancel_check)
    entries = sorted(REAZONSPEECH_REQUIRED_FILES.items())
    if progress_stage:
        _notify(progress_callback, progress_stage, progress_start)
    for index, (relative, (expected_size, expected_sha256)) in enumerate(
            entries):
        _check_cancel(cancel_check)
        path = _safe_manifest_path(root, relative)
        if _path_has_link_component(root, path):
            raise SubtitleError(
                'ReazonSpeech model install contains an unsafe link')
        # Always re-hash executable code. The metadata cache is safe for the
        # immutable, very large ONNX weights but not for native DLL/EXE files.
        if relative.casefold().endswith(('.exe', '.dll')):
            _verified_paths.pop(os.path.abspath(path), None)
        if not _is_verified(
                path, expected_size, expected_sha256, cancel_check):
            raise SubtitleError(
                'ReazonSpeech model failed integrity verification')
        if progress_stage and entries:
            percent = progress_start + int(
                (index + 1) * (progress_end - progress_start)
                / len(entries))
            _notify(
                progress_callback, progress_stage,
                min(progress_end, percent))
    if progress_stage:
        _notify(progress_callback, progress_stage, progress_end)
    return root


def _remove_reazonspeech_cache_path(path: str, parent: str) -> None:
    path = os.path.abspath(path)
    parent = os.path.abspath(parent)
    if (
            os.path.commonpath([parent, path]) != parent
            or path == parent
            or _path_has_link_component(parent, parent)
            or _path_has_link_component(parent, path)):
        raise SubtitleError('Unsafe ReazonSpeech cache path')
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def _remove_reazonspeech_cache_path_best_effort(
        path: str, parent: str) -> None:
    try:
        _remove_reazonspeech_cache_path(path, parent)
    except (OSError, SubtitleError):
        pass


def _cleanup_reazonspeech_install_dirs(parent: str) -> None:
    parent = os.path.abspath(parent)
    prefix = (
        f'reazonspeech-v{REAZONSPEECH_PACK_VERSION}-install-')
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        target = os.path.abspath(os.path.join(parent, name))
        if (
                os.path.commonpath([parent, target]) == parent
                and target != parent
                and os.path.isdir(target)
                and not _path_has_link_component(parent, parent)
                and not _path_has_link_component(parent, target)):
            shutil.rmtree(target, ignore_errors=True)


def _prepare_reazonspeech_runtime(
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> str:
    lock_name = (
        f'reazonspeech-v{REAZONSPEECH_PACK_VERSION}')
    with _interprocess_cache_lock(lock_name, cancel_check):
        return _prepare_reazonspeech_runtime_locked(
            progress_callback, cancel_check)


def _prepare_reazonspeech_runtime_locked(
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> str:
    with _reazonspeech_runtime_lock:
        _check_cancel(cancel_check)
        root = _cache_root()
        runtime_dir = os.path.join(
            root, 'asr',
            f'reazonspeech-v{REAZONSPEECH_PACK_VERSION}')
        parent = os.path.dirname(runtime_dir)
        os.makedirs(parent, exist_ok=True)
        _cleanup_reazonspeech_install_dirs(parent)
        previous = runtime_dir + '.previous'
        if not os.path.exists(runtime_dir) and os.path.exists(previous):
            os.replace(previous, runtime_dir)
        try:
            installed = _verify_reazonspeech_install(
                runtime_dir, cancel_check=cancel_check)
            if os.path.exists(previous):
                _remove_reazonspeech_cache_path_best_effort(
                    previous, parent)
            return installed
        except SubtitleCancelled:
            raise
        except SubtitleError:
            pass

        if os.path.exists(previous):
            try:
                _verify_reazonspeech_install(
                    previous, cancel_check=cancel_check)
            except SubtitleCancelled:
                raise
            except SubtitleError:
                pass
            else:
                _remove_reazonspeech_cache_path(runtime_dir, parent)
                os.replace(previous, runtime_dir)
                return _verify_reazonspeech_install(
                    runtime_dir, cancel_check=cancel_check)

        for candidate in _component_install_candidates(
                REAZONSPEECH_PACK_NAME):
            try:
                installed = _verify_reazonspeech_install(
                    candidate, require_marker=False,
                    cancel_check=cancel_check)
            except SubtitleCancelled:
                raise
            except SubtitleError:
                continue
            _notify(progress_callback, 'model', 100)
            return installed

        if not REAZONSPEECH_PACK_SIZE or not REAZONSPEECH_PACK_SHA256:
            raise SubtitleError(
                'ReazonSpeech model pack is not configured')
        _notify(progress_callback, 'model', 0)
        managed_archive = os.path.join(
            root, 'downloads', REAZONSPEECH_PACK_NAME)
        archive = _find_verified_component_archive(
            REAZONSPEECH_PACK_NAME, REAZONSPEECH_PACK_SIZE,
            REAZONSPEECH_PACK_SHA256, cancel_check)

        def download_progress(
                _stage: str, percent: Optional[int]) -> None:
            mapped = (
                None if percent is None
                else min(70, max(0, int(percent * 0.70))))
            _notify(progress_callback, 'model', mapped)

        if archive:
            _notify(progress_callback, 'model', 70)
        else:
            archive = _download_verified(
                REAZONSPEECH_PACK_URL, managed_archive,
                REAZONSPEECH_PACK_SIZE, REAZONSPEECH_PACK_SHA256,
                'model', download_progress, cancel_check)
        # The verified archive remains on disk while its similarly sized
        # contents are extracted. Reserve room for that second copy plus the
        # normal processing safety margin before creating an install tree.
        _ensure_asr_temp_space(parent, REAZONSPEECH_PACK_SIZE)
        temp_dir = tempfile.mkdtemp(
            prefix=(
                f'reazonspeech-v{REAZONSPEECH_PACK_VERSION}-install-'),
            dir=parent)
        try:
            _safe_extract_zip(
                archive, temp_dir, progress_callback, cancel_check,
                'model', 70, 85)
            _verify_reazonspeech_install(
                temp_dir, require_marker=False,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                progress_stage='model',
                progress_start=85,
                progress_end=95)
            _check_cancel(cancel_check)
            marker = os.path.join(temp_dir, '.source-sha256')
            with open(marker, 'w', encoding='ascii') as handle:
                handle.write(REAZONSPEECH_PACK_SHA256)
                handle.flush()
                os.fsync(handle.fileno())
            if os.path.exists(previous):
                _remove_reazonspeech_cache_path_best_effort(
                    previous, parent)
            had_previous = os.path.exists(runtime_dir)
            if had_previous:
                os.replace(runtime_dir, previous)
            try:
                os.replace(temp_dir, runtime_dir)
                temp_dir = ''
            except Exception:
                if had_previous and not os.path.exists(runtime_dir):
                    os.replace(previous, runtime_dir)
                raise
            if os.path.exists(previous):
                _remove_reazonspeech_cache_path(previous, parent)
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

        if os.path.normcase(os.path.abspath(archive)) == os.path.normcase(
                os.path.abspath(managed_archive)):
            try:
                os.remove(archive)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        installed = _verify_reazonspeech_install(
            runtime_dir, cancel_check=cancel_check)
        _notify(progress_callback, 'model', 100)
        return installed


def _prepare_runtime(progress_callback: Optional[ProgressCallback],
                     cancel_check: Optional[CancelCheck]) -> tuple[str, str, str]:
    with _interprocess_cache_lock(
            f'whisper-{WHISPER_VERSION}', cancel_check):
        return _prepare_runtime_locked(
            progress_callback, cancel_check)


def _prepare_whisper_model(
        profile: RecognitionProfile,
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> str:
    if profile.engine != 'whisper':
        raise SubtitleError(
            'Speech recognition fallback profile is invalid')
    root = _cache_root()
    model = os.path.join(root, 'models', profile.model_name)
    _notify(progress_callback, 'model', 0)
    return _download_verified(
        profile.model_url, model, profile.model_size, profile.model_sha256,
        'model', progress_callback, cancel_check)


def _prepare_whisper_fallback_model(
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> str:
    """Download the balanced fallback only after Auto structurally fails."""
    profile = RECOGNITION_PROFILES['balanced']
    with _interprocess_cache_lock(
            f'whisper-{WHISPER_VERSION}', cancel_check):
        with _runtime_lock:
            return _prepare_whisper_model(
                profile, progress_callback, cancel_check)


def _prepare_runtime_locked(
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> tuple[str, str, str]:
    with _runtime_lock:
        _check_cancel(cancel_check)
        root = _cache_root()
        archive = os.path.join(root, 'downloads', f'whisper-{WHISPER_VERSION}-x64.zip')
        runtime_dir = os.path.join(root, 'whisper', WHISPER_VERSION)
        exe = _verify_whisper_install(
            runtime_dir, cancel_check=cancel_check)
        if not exe:
            _notify(progress_callback, 'runtime', 0)
            _download_verified(
                WHISPER_ARCHIVE_URL, archive, WHISPER_ARCHIVE_SIZE,
                WHISPER_ARCHIVE_SHA256, 'runtime', progress_callback, cancel_check)
            parent = os.path.dirname(runtime_dir)
            os.makedirs(parent, exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix='whisper-install-', dir=parent)
            try:
                _safe_extract_zip(
                    archive, temp_dir, cancel_check=cancel_check)
                if not _verify_whisper_install(
                        temp_dir, require_marker=False,
                        cancel_check=cancel_check):
                    raise SubtitleError(
                        'whisper.cpp runtime failed integrity verification')
                with open(os.path.join(temp_dir, '.source-sha256'), 'w', encoding='ascii') as handle:
                    handle.write(WHISPER_ARCHIVE_SHA256)
                if os.path.isdir(runtime_dir):
                    shutil.rmtree(runtime_dir)
                os.replace(temp_dir, runtime_dir)
                temp_dir = ''
            finally:
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            exe = _verify_whisper_install(
                runtime_dir, cancel_check=cancel_check)
        if not exe:
            raise SubtitleError('Unable to install whisper.cpp')

        profile = recognition_profile()
        if profile.engine == 'reazonspeech':
            model = _prepare_reazonspeech_runtime(
                progress_callback, cancel_check)
        else:
            model = _prepare_whisper_model(
                profile, progress_callback, cancel_check)
        vad_model = os.path.join(root, 'models', VAD_MODEL_NAME)
        _download_verified(
            VAD_MODEL_URL, vad_model, VAD_MODEL_SIZE, VAD_MODEL_SHA256,
            'model', progress_callback, cancel_check)
        return exe, model, vad_model


def _ensure_asr_temp_space(path: str, additional_bytes: int = 0) -> None:
    try:
        required = max(0, int(additional_bytes)) + ASR_TEMP_RESERVE_BYTES
        free = shutil.disk_usage(path).free
    except (OSError, TypeError, ValueError, OverflowError):
        return
    if free < required:
        raise SubtitleStorageError(
            'Not enough free disk space for subtitle processing')


def _run_process(args: list[str], log_path: str,
                 cancel_check: Optional[CancelCheck],
                 cwd: Optional[str] = None,
                 disk_guard_path: Optional[str] = None,
                 *,
                 timeout_seconds: Optional[float] = None,
                 stall_timeout_seconds: Optional[float] = None,
                 progress_probe: Optional[Callable[[], object]] = None,
                 heartbeat_callback: Optional[Callable[[], None]] = None,
                 ) -> None:
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    if disk_guard_path:
        _ensure_asr_temp_space(disk_guard_path)
    try:
        log_handle = open(log_path, 'wb')
    except OSError as exc:
        raise SubtitleError('Subtitle process could not start') from exc
    with log_handle:
        process = None
        try:
            process = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=log_handle,
                stderr=subprocess.STDOUT, creationflags=creationflags, cwd=cwd)
        except (OSError, ValueError) as exc:
            raise SubtitleError('Subtitle process could not start') from exc
        try:
            _wait_for_process(
                process,
                cancel_check,
                timeout_seconds=timeout_seconds,
                stall_timeout_seconds=stall_timeout_seconds,
                progress_probe=progress_probe,
                heartbeat_callback=heartbeat_callback,
                disk_guard_path=disk_guard_path,
            )
            if process.returncode != 0:
                raise SubtitleError(
                    f'Subtitle process exited with code {process.returncode}')
        finally:
            if process is not None and process.poll() is None:
                _terminate_process(process)


def _extract_audio(video_path: str, wav_path: str, log_path: str,
                   cancel_check: Optional[CancelCheck]) -> None:
    ffmpeg = locate_ffmpeg()
    if not ffmpeg:
        raise _asr_failure('audio')
    try:
        _run_process([
            ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-i', video_path, '-vn', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', wav_path,
        ], log_path, cancel_check,
            disk_guard_path=os.path.dirname(wav_path),
            timeout_seconds=AUDIO_EXTRACTION_TIMEOUT_SECONDS)
    except SubtitleCancelled:
        raise
    except SubtitleStorageError:
        raise
    except SubtitleError as exc:
        raise _asr_failure('audio', log_path) from exc


_VAD_HEADER_RE = re.compile(
    r'^[ \t]*Detected[ \t]+(\d+)[ \t]+speech segments:[ \t]*$',
    re.MULTILINE)
_VAD_SEGMENT_RE = re.compile(
    r'^[ \t]*Speech segment[ \t]+(\d+):[ \t]*'
    r'start[ \t]*=[ \t]*([+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]*,'
    r'[ \t]*end[ \t]*=[ \t]*([+-]?(?:\d+(?:\.\d*)?|\.\d+))[ \t]*$',
    re.MULTILINE)


def _parse_vad_speech_segments(output: str) -> list[SpeechIsland]:
    """Parse v1.9.1 helper output.

    The helper labels its timestamps like seconds but emits whisper's
    centisecond units. Keeping the conversion here prevents the 100x offset
    error that previously ruined cue timing.
    """
    text = str(output or '').replace('\r\n', '\n').replace('\r', '\n')
    headers = _VAD_HEADER_RE.findall(text)
    if len(headers) != 1:
        raise SubtitleError('Speech detection returned an invalid result')
    try:
        declared = int(headers[0])
    except (TypeError, ValueError) as exc:
        raise SubtitleError('Speech detection returned an invalid result') from exc
    if declared < 0 or declared > MAX_VAD_ISLANDS:
        raise SubtitleError('Speech detection returned too many segments')

    matches = list(_VAD_SEGMENT_RE.finditer(text))
    malformed_lines = [
        line for line in text.splitlines()
        if 'Speech segment' in line and not _VAD_SEGMENT_RE.fullmatch(line)
    ]
    if malformed_lines or len(matches) != declared:
        raise SubtitleError('Speech detection returned an invalid result')

    islands: list[SpeechIsland] = []
    previous_start = -1.0
    for expected_index, match in enumerate(matches):
        try:
            index = int(match.group(1))
            # v1.9.1 prints t0/t1 in centiseconds, despite the decimal form.
            start = float(match.group(2)) / 100.0
            end = float(match.group(3)) / 100.0
        except (TypeError, ValueError, OverflowError) as exc:
            raise SubtitleError(
                'Speech detection returned an invalid result') from exc
        if (
                index != expected_index
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0.0
                or end <= start
                or start < previous_start
                or end > 24 * 60 * 60):
            raise SubtitleError('Speech detection returned invalid timing')
        islands.append(SpeechIsland(start, end))
        previous_start = start
    return islands


def _merge_speech_islands(
        islands: Iterable[SpeechIsland],
        max_gap_seconds: float = VAD_MERGE_GAP_SECONDS,
        max_duration_seconds: float = VAD_MAX_SPEECH_SECONDS,
) -> list[SpeechIsland]:
    try:
        gap_limit = float(max_gap_seconds)
        duration_limit = float(max_duration_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SubtitleError('Speech detection settings are invalid') from exc
    if (
            not math.isfinite(gap_limit) or gap_limit < 0.0
            or not math.isfinite(duration_limit) or duration_limit <= 0.0):
        raise SubtitleError('Speech detection settings are invalid')

    merged: list[SpeechIsland] = []
    previous_start = -1.0
    for value in islands:
        try:
            start = float(value.start)
            end = float(value.end)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise SubtitleError('Speech detection returned invalid timing') from exc
        if (
                not math.isfinite(start) or not math.isfinite(end)
                or start < 0.0 or end <= start or start < previous_start):
            raise SubtitleError('Speech detection returned invalid timing')
        previous_start = start
        current = SpeechIsland(start, end)
        if not merged:
            merged.append(current)
            continue
        prior = merged[-1]
        combined_end = max(prior.end, current.end)
        if (
                current.start - prior.end <= gap_limit
                and combined_end - prior.start <= duration_limit):
            merged[-1] = SpeechIsland(prior.start, combined_end)
        else:
            merged.append(current)
        if len(merged) > MAX_VAD_ISLANDS:
            raise SubtitleError('Speech detection returned too many segments')
    return merged


def _build_recognition_windows(
        islands: Iterable[SpeechIsland], audio_duration: float,
        max_speech_span_seconds: float = (
            RECOGNITION_WINDOW_SPEECH_SPAN_SECONDS),
        padding_seconds: float = RECOGNITION_WINDOW_PADDING_SECONDS,
        max_window_seconds: float = RECOGNITION_WINDOW_MAX_SECONDS,
        max_gap_seconds: float = VAD_MERGE_GAP_SECONDS,
) -> list[SpeechIsland]:
    """Group VAD hits into context-rich, non-overlapping ASR windows.

    VAD is a speech gate, not a chopping strategy. Sending every tiny hit to
    Whisper independently destroys sentence context and substantially worsens
    Japanese recognition. Windows therefore retain the original silence
    between nearby utterances while remaining below Whisper's 30-second
    receptive window.
    """
    try:
        duration = float(audio_duration)
        span_limit = float(max_speech_span_seconds)
        padding = float(padding_seconds)
        window_limit = float(max_window_seconds)
        gap_limit = float(max_gap_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SubtitleError('Speech detection settings are invalid') from exc
    if (
            not math.isfinite(duration) or duration < 0.0
            or not math.isfinite(span_limit) or span_limit <= 0.0
            or not math.isfinite(padding) or padding < 0.0
            or not math.isfinite(window_limit) or window_limit <= 0.0
            or not math.isfinite(gap_limit) or gap_limit < 0.0
            or span_limit + 2.0 * padding > window_limit):
        raise SubtitleError('Speech detection settings are invalid')

    source = list(islands)
    if not source:
        return []
    groups: list[SpeechIsland] = []
    previous_start = -1.0
    for value in source:
        try:
            start = float(value.start)
            end = float(value.end)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise SubtitleError('Speech detection returned invalid timing') from exc
        if (
                not math.isfinite(start) or not math.isfinite(end)
                or start < 0.0 or end <= start or end > duration + 0.5
                or start < previous_start):
            raise SubtitleError('Speech detection returned invalid timing')
        start = min(start, duration)
        end = min(end, duration)
        if end - start > window_limit:
            # The VAD helper may extend a max-length segment by its configured
            # speech padding. Trim only that small symmetric margin.
            excess = end - start - window_limit
            if excess > 2.0 * VAD_SPEECH_PAD_MS / 1000.0 + 1e-6:
                raise SubtitleError(
                    'Speech detection returned invalid timing')
            start += excess / 2.0
            end -= excess / 2.0
        previous_start = start
        if (
                groups
                and start - groups[-1].end <= gap_limit
                and end - groups[-1].start <= span_limit):
            groups[-1] = SpeechIsland(
                groups[-1].start, max(groups[-1].end, end))
        else:
            groups.append(SpeechIsland(start, end))

    windows: list[SpeechIsland] = []
    for group in groups:
        context_budget = max(
            0.0, window_limit - (group.end - group.start))
        left_context = min(padding, context_budget / 2.0, group.start)
        right_context = min(
            padding, context_budget - left_context,
            duration - group.end)
        # Reuse boundary-clipped context budget on the other side.
        remaining = context_budget - left_context - right_context
        if remaining > 0.0:
            extra_right = min(
                remaining, padding - right_context,
                duration - group.end - right_context)
            right_context += extra_right
            remaining -= extra_right
        if remaining > 0.0:
            left_context += min(
                remaining, padding - left_context,
                group.start - left_context)
        windows.append(SpeechIsland(
            group.start - left_context,
            group.end + right_context,
        ))
    # Padding must not transcribe the same audio twice. Split any overlap as
    # close as possible to the midpoint between the underlying speech groups,
    # but never expand either already-bounded recognition window.
    for index in range(len(windows) - 1):
        if windows[index].end > windows[index + 1].start:
            target = (
                groups[index].end + groups[index + 1].start) / 2.0
            boundary = min(
                max(target, windows[index + 1].start),
                windows[index].end,
            )
            windows[index] = SpeechIsland(
                windows[index].start, boundary)
            windows[index + 1] = SpeechIsland(
                boundary, windows[index + 1].end)
    for window in windows:
        if (
                window.end <= window.start
                or window.end - window.start > window_limit + 1e-6):
            raise SubtitleError('Speech detection returned invalid timing')
    return windows


def _terminate_process(process) -> None:
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
    except (OSError, ValueError):
        return
    try:
        process.terminate()
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=3)
        return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except (OSError, ValueError):
        pass
    try:
        process.wait(timeout=3)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _wait_for_process(
        process,
        cancel_check: Optional[CancelCheck],
        *,
        timeout_seconds: Optional[float] = None,
        stall_timeout_seconds: Optional[float] = None,
        progress_probe: Optional[Callable[[], object]] = None,
        heartbeat_callback: Optional[Callable[[], None]] = None,
        disk_guard_path: Optional[str] = None,
) -> None:
    """Wait for a native helper while keeping cancellation and liveness bounded."""
    started = time.monotonic()
    last_progress = started
    next_heartbeat = started
    next_disk_check = started
    missing_probe = object()
    previous_probe = missing_probe

    while process.poll() is None:
        if cancel_check and cancel_check():
            raise SubtitleCancelled('Subtitle generation cancelled')

        now = time.monotonic()
        if disk_guard_path and now >= next_disk_check:
            _ensure_asr_temp_space(disk_guard_path)
            next_disk_check = now + 0.5

        if progress_probe is not None:
            try:
                current_probe = progress_probe()
            except (OSError, ValueError):
                current_probe = previous_probe
            if previous_probe is missing_probe:
                previous_probe = current_probe
            elif current_probe != previous_probe:
                previous_probe = current_probe
                last_progress = now

        if heartbeat_callback is not None and now >= next_heartbeat:
            heartbeat_callback()
            next_heartbeat = now + SUBTITLE_PROCESS_HEARTBEAT_SECONDS

        if (
                timeout_seconds is not None
                and now - started >= max(0.05, float(timeout_seconds))):
            raise SubtitleProcessTimeout(
                'Subtitle process stopped responding')
        if (
                stall_timeout_seconds is not None
                and now - last_progress
                >= max(0.05, float(stall_timeout_seconds))):
            raise SubtitleProcessTimeout(
                'Subtitle process stopped responding')
        time.sleep(0.05)


def _vad_timeout_seconds(audio_duration: Optional[float]) -> float:
    try:
        duration = max(0.0, float(audio_duration or 0.0))
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    return max(float(VAD_MIN_TIMEOUT_SECONDS), duration * 2.0 + 120.0)


def _whisper_timeout_seconds(audio_duration: Optional[float]) -> float:
    try:
        duration = max(0.0, float(audio_duration or 0.0))
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    return max(float(WHISPER_MIN_TIMEOUT_SECONDS), duration * 10.0 + 300.0)


def _asr_failure(
        category: str, log_path: Optional[str] = None) -> SubtitleError:
    evidence = ''
    if log_path:
        try:
            with open(log_path, 'rb') as handle:
                evidence = handle.read(256 * 1024).decode(
                    'utf-8', errors='ignore').casefold()
        except OSError:
            evidence = ''
    if any(marker in evidence for marker in (
            'out of memory', 'bad_alloc', 'cannot allocate memory',
            'failed to allocate', 'not enough memory')):
        category = 'oom'
    elif any(marker in evidence for marker in (
            'failed to initialize whisper context',
            'model init failed', 'invalid model', 'failed to load model')):
        category = 'model'

    messages = {
        'oom': (
            'Speech recognition ran out of memory; choose a lighter '
            'recognition quality'),
        'model': 'Speech recognition model could not be loaded',
        'audio': 'Speech recognition could not read the extracted audio',
        'runtime': 'Speech recognition runtime failed',
    }
    return SubtitleError(messages.get(category, messages['runtime']))


def _read_file_bounded(path: str, limit: int) -> bytes:
    try:
        with open(path, 'rb') as handle:
            payload = handle.read(limit + 1)
    except OSError as exc:
        raise _asr_failure('runtime') from exc
    if len(payload) > limit:
        raise SubtitleError('Speech recognition runtime returned too much data')
    return payload


def _run_external_vad(
        vad_exe: str, vad_model: str, wav_path: str, work_dir: str,
        cancel_check: Optional[CancelCheck],
        audio_duration: Optional[float] = None,
        progress_callback: Optional[ProgressCallback] = None,
) -> list[SpeechIsland]:
    output_path = os.path.join(work_dir, 'vad-output.txt')
    log_path = os.path.join(work_dir, 'vad.log')
    threads = max(1, min(8, (os.cpu_count() or 4) - 1))
    args = [
        vad_exe,
        '--file', wav_path,
        '--vad-model', vad_model,
        '--vad-threshold', str(VAD_THRESHOLD),
        '--vad-min-speech-duration-ms', str(VAD_MIN_SPEECH_MS),
        '--vad-max-speech-duration-s', str(int(VAD_MAX_SPEECH_SECONDS)),
        '--vad-speech-pad-ms', str(VAD_SPEECH_PAD_MS),
        '--vad-samples-overlap', str(VAD_SAMPLES_OVERLAP_SECONDS),
        '--threads', str(threads),
        '--no-prints',
    ]
    creationflags = (
        getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    process = None
    try:
        with (
                open(output_path, 'wb') as output_handle,
                open(log_path, 'wb') as log_handle):
            try:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=output_handle,
                    stderr=log_handle,
                    cwd=os.path.dirname(vad_exe) or None,
                    creationflags=creationflags,
                )
            except (OSError, ValueError) as exc:
                raise _asr_failure('runtime', log_path) from exc

            def _progress_probe():
                sizes = []
                for path in (output_path, log_path):
                    try:
                        sizes.append(os.path.getsize(path))
                    except OSError:
                        sizes.append(-1)
                return tuple(sizes)

            _wait_for_process(
                process,
                cancel_check,
                timeout_seconds=_vad_timeout_seconds(audio_duration),
                stall_timeout_seconds=WHISPER_STALL_TIMEOUT_SECONDS,
                progress_probe=_progress_probe,
                heartbeat_callback=lambda: _notify(
                    progress_callback, 'transcribe_ja', 0),
            )
        if process.returncode != 0:
            raise _asr_failure('runtime', log_path)
        try:
            output = _read_file_bounded(
                output_path, 4 * 1024 * 1024).decode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise _asr_failure('runtime', log_path) from exc
        return _parse_vad_speech_segments(output)
    except OSError as exc:
        raise _asr_failure('runtime', log_path) from exc
    finally:
        if process is not None and process.poll() is None:
            _terminate_process(process)


def _validate_pcm16_wav(wav_path: str) -> tuple[int, float]:
    try:
        with wave.open(wav_path, 'rb') as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            frame_rate = source.getframerate()
            frame_count = source.getnframes()
            compression = source.getcomptype()
    except (OSError, EOFError, wave.Error) as exc:
        raise _asr_failure('audio') from exc
    if (
            channels != 1
            or sample_width != 2
            or frame_rate != WHISPER_SAMPLE_RATE
            or compression != 'NONE'
            or frame_count < 0):
        raise _asr_failure('audio')
    duration = frame_count / float(frame_rate)
    if not math.isfinite(duration) or duration > 24 * 60 * 60:
        raise _asr_failure('audio')
    return frame_count, duration


def _slice_pcm16_wav(
        wav_path: str, destination: str, island: SpeechIsland,
        total_frames: int,
) -> float:
    start_frame = max(
        0, min(total_frames, int(round(island.start * WHISPER_SAMPLE_RATE))))
    end_frame = max(
        start_frame,
        min(total_frames, int(round(island.end * WHISPER_SAMPLE_RATE))))
    if end_frame <= start_frame:
        raise SubtitleError('Speech detection returned invalid timing')
    _ensure_asr_temp_space(
        os.path.dirname(destination),
        (end_frame - start_frame) * 2 + 44)
    try:
        with wave.open(wav_path, 'rb') as source:
            source.setpos(start_frame)
            frames = source.readframes(end_frame - start_frame)
        if len(frames) != (end_frame - start_frame) * 2:
            raise _asr_failure('audio')
        with wave.open(destination, 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(WHISPER_SAMPLE_RATE)
            output.writeframes(frames)
    except SubtitleError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise _asr_failure('audio') from exc
    return (end_frame - start_frame) / float(WHISPER_SAMPLE_RATE)


_CLI_TIMESTAMP_RE = re.compile(
    r'^(\d{2,6}):([0-5]\d):([0-5]\d),(\d{3})$')


def _parse_cli_timestamp(value) -> int:
    if not isinstance(value, str):
        raise SubtitleError(
            'Speech recognition runtime returned an invalid response')
    match = _CLI_TIMESTAMP_RE.fullmatch(value)
    if not match:
        raise SubtitleError(
            'Speech recognition runtime returned invalid timing')
    hours, minutes, seconds, milliseconds = (
        int(part) for part in match.groups())
    return (
        hours * 3_600_000
        + minutes * 60_000
        + seconds * 1000
        + milliseconds
    )


def _strict_json_file(path: str) -> dict:
    payload = _read_file_bounded(path, MAX_WHISPER_RESPONSE_BYTES)

    def reject_constant(_value):
        raise ValueError('non-finite JSON number')

    try:
        root = json.loads(
            payload.decode('utf-8'), parse_constant=reject_constant)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SubtitleError(
            'Speech recognition runtime returned an invalid response') from exc
    if not isinstance(root, dict):
        raise SubtitleError(
            'Speech recognition runtime returned an invalid response')
    return root


def _normalize_recognized_text(value: str) -> str:
    if any(
            unicodedata.category(character) == 'Cc'
            and character not in '\r\n\t'
            for character in value):
        raise SubtitleError(
            'Speech recognition runtime returned an invalid response')
    # whisper-cli emits one logical cue per segment. Flattening whitespace
    # prevents model text from accidentally creating extra SRT blocks.
    return ' '.join(value.split())


def _parse_whisper_cli_payload(
        root: dict, island: SpeechIsland, island_duration: float,
) -> list[RecognizedSegment]:
    params = root.get('params')
    result = root.get('result')
    transcription = root.get('transcription')
    if (
            not isinstance(params, dict)
            or params.get('language') != 'ja'
            or params.get('translate') is not False
            or not isinstance(result, dict)
            or result.get('language') != 'ja'
            or not isinstance(transcription, list)
            or len(transcription) > MAX_WHISPER_SEGMENTS_PER_ISLAND
            or not math.isfinite(island_duration)
            or island_duration <= 0.0):
        raise SubtitleError(
            'Speech recognition runtime returned an invalid response')

    recognized: list[RecognizedSegment] = []
    previous_end_ms = -1
    maximum_ms = int(round(island_duration * 1000.0))
    for segment in transcription:
        if not isinstance(segment, dict):
            raise SubtitleError(
                'Speech recognition runtime returned an invalid response')
        timestamps = segment.get('timestamps')
        offsets = segment.get('offsets')
        text = segment.get('text')
        if (
                not isinstance(timestamps, dict)
                or not isinstance(offsets, dict)
                or not isinstance(text, str)
                or len(text) > MAX_WHISPER_TEXT_CHARS
                or '\x00' in text):
            raise SubtitleError(
                'Speech recognition runtime returned an invalid response')
        start_ms = offsets.get('from')
        end_ms = offsets.get('to')
        if (
                isinstance(start_ms, bool)
                or isinstance(end_ms, bool)
                or not isinstance(start_ms, int)
                or not isinstance(end_ms, int)
                or start_ms < 0
                or end_ms <= start_ms
                or start_ms < previous_end_ms
                or end_ms > maximum_ms + 1000
                or _parse_cli_timestamp(timestamps.get('from')) != start_ms
                or _parse_cli_timestamp(timestamps.get('to')) != end_ms):
            raise SubtitleError(
                'Speech recognition runtime returned invalid timing')
        previous_end_ms = end_ms
        cue_text = _normalize_recognized_text(text)
        if not cue_text:
            continue
        local_start = min(max(start_ms / 1000.0, 0.0), island_duration)
        local_end = min(max(end_ms / 1000.0, 0.0), island_duration)
        absolute_start = min(
            max(island.start + local_start, island.start), island.end)
        absolute_end = min(
            max(island.start + local_end, island.start), island.end)
        if absolute_end <= absolute_start:
            raise SubtitleError(
                'Speech recognition runtime returned invalid timing')
        recognized.append(
            RecognizedSegment(absolute_start, absolute_end, cue_text))
    return recognized


def _whisper_cli_batch_args(
        exe: str, model: str, relative_wavs: Iterable[str],
) -> list[str]:
    threads = max(1, min(8, (os.cpu_count() or 4) - 1))
    args = [
        exe,
        '-m', model,
        '-l', 'ja',
        '-oj',
        '-np',
        '-t', str(threads),
        '-bs', '5',
        '-bo', '5',
        '-tp', '0',
        '-tpi', '0.2',
    ]
    for relative_path in relative_wavs:
        if (
                not re.fullmatch(r'island-\d{5}\.wav', relative_path)
                or os.path.basename(relative_path) != relative_path):
            raise SubtitleError(
                'Speech recognition runtime received an unsafe input name')
        args.extend(('-f', relative_path))
    return args


def _run_whisper_cli_batch(
        exe: str, model: str, work_dir: str,
        relative_wavs: list[str], batch_index: int,
        cancel_check: Optional[CancelCheck],
        *,
        completed_offset: int = 0,
        total_windows: Optional[int] = None,
        batch_audio_seconds: Optional[float] = None,
        progress_callback: Optional[ProgressCallback] = None,
) -> list[dict]:
    if not relative_wavs or len(relative_wavs) > WHISPER_BATCH_SIZE:
        raise SubtitleError(
            'Speech recognition runtime received an invalid batch')
    log_path = os.path.join(work_dir, f'batch-{batch_index:05d}.log')

    def _completed_count() -> int:
        return sum(
            os.path.isfile(os.path.join(work_dir, name + '.json'))
            for name in relative_wavs
        )

    total = max(
        len(relative_wavs),
        int(total_windows or len(relative_wavs)),
    )

    def _report_progress() -> None:
        completed = min(len(relative_wavs), _completed_count())
        absolute = min(total, max(0, int(completed_offset)) + completed)
        # The final 100% notification belongs to generate_subtitles after the
        # output has been parsed and written atomically.
        percent = min(99, int(absolute * 100 / total))
        _notify(progress_callback, 'transcribe_ja', percent)

    try:
        _run_process(
            _whisper_cli_batch_args(exe, model, relative_wavs),
            log_path,
            cancel_check,
            cwd=work_dir,
            disk_guard_path=work_dir,
            timeout_seconds=_whisper_timeout_seconds(batch_audio_seconds),
            stall_timeout_seconds=WHISPER_STALL_TIMEOUT_SECONDS,
            progress_probe=_completed_count,
            heartbeat_callback=_report_progress,
        )
        _report_progress()
    except SubtitleCancelled:
        raise
    except SubtitleProcessTimeout:
        raise
    except SubtitleError as exc:
        raise _asr_failure('runtime', log_path) from exc
    results: list[dict] = []
    for relative_path in relative_wavs:
        _check_cancel(cancel_check)
        json_path = os.path.join(work_dir, relative_path + '.json')
        if not os.path.isfile(json_path):
            raise _asr_failure('runtime', log_path)
        results.append(_strict_json_file(json_path))
    return results


def _format_srt_timestamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0.0:
        raise SubtitleError('Speech recognition runtime returned invalid timing')
    milliseconds = int(round(seconds * 1000.0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}'


def _run_whisper(exe: str, model: str, vad_model: str, wav_path: str,
                 output_base: str, log_path: str,
                 cancel_check: Optional[CancelCheck],
                 progress_callback: Optional[ProgressCallback] = None,
                 ) -> Optional[str]:
    del log_path  # All ASR logs live only in the private per-run temp folder.
    runtime_dir = os.path.dirname(os.path.abspath(exe))
    vad_exe = os.path.join(
        runtime_dir, 'whisper-vad-speech-segments.exe')
    if not os.path.isfile(exe) or not os.path.isfile(vad_exe):
        raise _asr_failure('runtime')

    try:
        work_dir = tempfile.mkdtemp(
            prefix='uav-asr-',
            dir=os.path.dirname(os.path.abspath(wav_path)))
    except OSError as exc:
        raise _asr_failure('runtime') from exc
    try:
        total_frames, audio_duration = _validate_pcm16_wav(wav_path)
        islands = _run_external_vad(
            vad_exe, vad_model, wav_path, work_dir, cancel_check,
            audio_duration=audio_duration,
            progress_callback=progress_callback)
        clamped: list[SpeechIsland] = []
        for island in islands:
            if island.start > audio_duration + 0.5:
                raise SubtitleError(
                    'Speech detection returned invalid timing')
            start = min(max(0.0, island.start), audio_duration)
            end = min(max(0.0, island.end), audio_duration)
            if end <= start:
                raise SubtitleError(
                    'Speech detection returned invalid timing')
            clamped.append(SpeechIsland(start, end))
        islands = _merge_speech_islands(clamped)
        if not islands:
            return None
        windows = _build_recognition_windows(islands, audio_duration)
        _notify(progress_callback, 'transcribe_ja', 0)
        slice_bytes = sum(
            (
                max(
                    0,
                    min(
                        total_frames,
                        int(round(window.end * WHISPER_SAMPLE_RATE)),
                    )
                    - max(
                        0,
                        min(
                            total_frames,
                            int(round(window.start * WHISPER_SAMPLE_RATE)),
                        ),
                    ),
                )
                * 2
                + 44
            )
            for window in windows
        )
        _ensure_asr_temp_space(work_dir, slice_bytes)

        relative_wavs: list[str] = []
        window_durations: list[float] = []
        for index, window in enumerate(windows):
            _check_cancel(cancel_check)
            relative_path = f'island-{index:05d}.wav'
            relative_wavs.append(relative_path)
            window_durations.append(_slice_pcm16_wav(
                wav_path,
                os.path.join(work_dir, relative_path),
                window,
                total_frames,
            ))

        recognized: list[RecognizedSegment] = []
        for batch_index, batch_start in enumerate(
                range(0, len(relative_wavs), WHISPER_BATCH_SIZE)):
            _check_cancel(cancel_check)
            batch_end = min(
                batch_start + WHISPER_BATCH_SIZE, len(relative_wavs))
            batch_names = relative_wavs[batch_start:batch_end]
            payloads = _run_whisper_cli_batch(
                exe, model, work_dir, batch_names, batch_index, cancel_check,
                completed_offset=batch_start,
                total_windows=len(relative_wavs),
                batch_audio_seconds=sum(
                    window_durations[batch_start:batch_end]),
                progress_callback=progress_callback)
            if len(payloads) != len(batch_names):
                raise _asr_failure('runtime')
            for offset, payload in enumerate(payloads):
                window_index = batch_start + offset
                cues = _parse_whisper_cli_payload(
                    payload,
                    windows[window_index],
                    window_durations[window_index],
                )
                if len(recognized) + len(cues) > MAX_WHISPER_CUES:
                    raise SubtitleError(
                        'Speech recognition runtime returned too many segments')
                # Do not globally deduplicate text. Distinct windows can
                # legitimately contain the same repeated utterance.
                recognized.extend(cues)

        if not recognized:
            raise SubtitleError(
                'Speech was detected but no valid subtitle cues were produced')
        srt_cues = [
            SrtCue(
                str(index),
                (
                    f'{_format_srt_timestamp(cue.start)} --> '
                    f'{_format_srt_timestamp(cue.end)}'),
                cue.text,
            )
            for index, cue in enumerate(recognized, start=1)
        ]
        result = output_base + '.srt'
        try:
            _atomic_write_text(result, render_srt(srt_cues))
        except OSError as exc:
            raise _asr_failure('runtime') from exc
        _notify(progress_callback, 'transcribe_ja', 100)
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _reazonspeech_timeout_seconds(
        audio_duration: Optional[float]) -> float:
    try:
        duration = max(0.0, float(audio_duration or 0.0))
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    return max(
        float(REAZONSPEECH_MIN_TIMEOUT_SECONDS),
        duration * 4.0 + 120.0)


def _reazonspeech_cli_args(
        pack_root: str, relative_wavs: Iterable[str]) -> list[str]:
    relative_wavs = list(relative_wavs)
    if (
            not relative_wavs
            or len(relative_wavs) > REAZONSPEECH_BATCH_SIZE):
        raise SubtitleError(
            'Speech recognition runtime received an invalid batch')
    for relative_path in relative_wavs:
        if (
                not isinstance(relative_path, str)
                or not re.fullmatch(r'island-\d{5}\.wav', relative_path)
                or os.path.basename(relative_path) != relative_path):
            raise SubtitleError(
                'Speech recognition runtime received an unsafe input name')

    pack_root = os.path.abspath(pack_root)
    runtime = os.path.join(
        pack_root, 'runtime', 'sherpa-onnx-offline.exe')
    model_dir = os.path.join(pack_root, 'model')
    return [
        runtime,
        '--tokens=' + os.path.join(model_dir, 'tokens.txt'),
        '--encoder=' + os.path.join(
            model_dir, 'encoder-epoch-99-avg-1.int8.onnx'),
        '--decoder=' + os.path.join(
            model_dir, 'decoder-epoch-99-avg-1.onnx'),
        '--joiner=' + os.path.join(
            model_dir, 'joiner-epoch-99-avg-1.int8.onnx'),
        '--num-threads=' + str(_recognition_thread_count()),
        '--decoding-method=greedy_search',
        '--provider=cpu',
        '--debug=false',
        '--sample-rate=16000',
        '--feat-dim=80',
        '--dither=0',
        *relative_wavs,
    ]


def _strict_reazonspeech_json_lines(
        path: str, expected_count: int) -> list[dict]:
    try:
        payload = _read_file_bounded(
            path, REAZONSPEECH_MAX_BATCH_BYTES)
        text = payload.decode('utf-8', errors='strict')
    except (SubtitleError, UnicodeError) as exc:
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned an invalid response') from exc
    lines = text.splitlines()
    if (
            len(lines) != expected_count
            or any(not line.strip() for line in lines)):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned the wrong result count')

    def reject_constant(_value):
        raise ValueError('non-finite JSON number')

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    results: list[dict] = []
    for line in lines:
        try:
            root = json.loads(
                line,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate_keys)
        except (ValueError, TypeError) as exc:
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned an invalid response') from exc
        if not isinstance(root, dict):
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned an invalid response')
        results.append(root)
    return results


def _run_reazonspeech_cli_batch(
        pack_root: str, work_dir: str,
        relative_wavs: list[str], batch_index: int,
        cancel_check: Optional[CancelCheck],
        *,
        completed_offset: int = 0,
        total_windows: Optional[int] = None,
        batch_audio_seconds: Optional[float] = None,
        progress_callback: Optional[ProgressCallback] = None,
) -> list[dict]:
    args = _reazonspeech_cli_args(pack_root, relative_wavs)
    stdout_path = os.path.join(
        work_dir, f'reazon-batch-{batch_index:05d}.stdout.jsonl')
    stderr_path = os.path.join(
        work_dir, f'reazon-batch-{batch_index:05d}.stderr.log')
    creationflags = (
        getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if os.name == 'nt' else 0)
    process = None
    total = max(
        len(relative_wavs),
        int(total_windows or len(relative_wavs)))

    def _progress_probe():
        sizes = []
        for path in (stdout_path, stderr_path):
            try:
                sizes.append(os.path.getsize(path))
            except OSError:
                sizes.append(-1)
        return tuple(sizes)

    def _report_progress():
        absolute = min(
            total, max(0, int(completed_offset)))
        _notify(
            progress_callback, 'transcribe_ja',
            min(99, int(absolute * 100 / total)))

    try:
        _ensure_asr_temp_space(work_dir)
        with (
                open(stdout_path, 'wb') as stdout_handle,
                open(stderr_path, 'wb') as stderr_handle):
            try:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    cwd=work_dir,
                    creationflags=creationflags,
                )
            except (OSError, ValueError) as exc:
                raise ReazonSpeechStructuralError(
                    'ReazonSpeech process could not start') from exc
            try:
                _wait_for_process(
                    process,
                    cancel_check,
                    timeout_seconds=_reazonspeech_timeout_seconds(
                        batch_audio_seconds),
                    stall_timeout_seconds=(
                        REAZONSPEECH_STALL_TIMEOUT_SECONDS),
                    progress_probe=_progress_probe,
                    heartbeat_callback=_report_progress,
                    disk_guard_path=work_dir,
                )
            except SubtitleCancelled:
                raise
            except SubtitleStorageError:
                raise
            except SubtitleProcessTimeout as exc:
                raise ReazonSpeechStructuralError(
                    'ReazonSpeech process stopped responding') from exc
            except SubtitleError as exc:
                raise ReazonSpeechStructuralError(
                    'ReazonSpeech process stopped responding') from exc
        if process.returncode != 0:
            raise ReazonSpeechStructuralError(
                'ReazonSpeech process failed')
        payloads = _strict_reazonspeech_json_lines(
            stdout_path, len(relative_wavs))
        completed = min(
            total,
            max(0, int(completed_offset)) + len(relative_wavs))
        _notify(
            progress_callback, 'transcribe_ja',
            min(99, int(completed * 100 / total)))
        return payloads
    except SubtitleCancelled:
        raise
    except SubtitleStorageError:
        # Storage pressure is shared by both engines and a fallback would make
        # it worse, so it must remain a hard failure.
        raise
    except ReazonSpeechStructuralError:
        raise
    except OSError as exc:
        raise ReazonSpeechStructuralError(
            'ReazonSpeech process failed') from exc
    finally:
        if process is not None and process.poll() is None:
            _terminate_process(process)


def _valid_reazonspeech_string(value, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and '\x00' not in value
        and not any(
            unicodedata.category(character) in ('Cc', 'Cf', 'Cs')
            for character in value)
    )


def _parse_reazonspeech_payload(
        root: dict, window: SpeechIsland,
        window_duration: float) -> list[RecognizedSegment]:
    expected_keys = {
        'lang', 'emotion', 'event', 'text', 'timestamps', 'durations',
        'tokens', 'ys_log_probs', 'words',
    }
    if (
            not isinstance(root, dict)
            or set(root) != expected_keys
            or not math.isfinite(window_duration)
            or window_duration <= 0.0
            or not _valid_reazonspeech_string(root.get('lang'), 64)
            or root.get('lang') not in ('', 'ja')
            or not _valid_reazonspeech_string(root.get('emotion'), 256)
            or not _valid_reazonspeech_string(root.get('event'), 256)
            or not _valid_reazonspeech_string(
                root.get('text'), MAX_WHISPER_TEXT_CHARS)):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned an invalid response')

    text = root['text']
    if text != text.strip():
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned an invalid response')
    tokens = root.get('tokens')
    timestamps = root.get('timestamps')
    log_probs = root.get('ys_log_probs')
    durations = root.get('durations')
    words = root.get('words')
    if (
            not isinstance(tokens, list)
            or not isinstance(timestamps, list)
            or not isinstance(log_probs, list)
            or not isinstance(durations, list)
            or not isinstance(words, list)
            or len(tokens) > REAZONSPEECH_MAX_TOKENS_PER_WINDOW
            or len(timestamps) != len(tokens)
            or len(log_probs) != len(tokens)
            or len(durations) not in (0, len(tokens))
            or len(words) > REAZONSPEECH_MAX_TOKENS_PER_WINDOW):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned an invalid response')

    if any(
            not _valid_reazonspeech_string(word, REAZONSPEECH_MAX_TOKEN_CHARS)
            for word in words):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned an invalid response')
    if any(
            not _valid_reazonspeech_string(
                token, REAZONSPEECH_MAX_TOKEN_CHARS)
            or not token
            for token in tokens):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned an invalid response')
    if ''.join(tokens) != text:
        raise ReazonSpeechStructuralError(
            'ReazonSpeech token text did not match its transcript')
    if not text:
        if timestamps or log_probs or durations:
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned an invalid response')
        return []

    checked_timestamps: list[float] = []
    previous = -1.0
    for value in timestamps:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned invalid timing')
        current = float(value)
        if (
                not math.isfinite(current)
                or current < 0.0
                or current < previous
                or current > window_duration + 0.5):
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned invalid timing')
        checked_timestamps.append(current)
        previous = current
    if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in log_probs):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned invalid token scores')
    if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
            or float(value) > window_duration + 0.5
            for value in durations):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech returned invalid token durations')

    recognized: list[RecognizedSegment] = []
    group_start = 0
    group_chars = 0
    punctuation = frozenset(REAZONSPEECH_CUE_PUNCTUATION)
    for index, token in enumerate(tokens):
        group_chars += len(token)
        final = index == len(tokens) - 1
        next_start = (
            window_duration if final
            else checked_timestamps[index + 1])
        group_time = next_start - checked_timestamps[group_start]
        pause = (
            not final
            and checked_timestamps[index + 1]
            - checked_timestamps[index]
            >= REAZONSPEECH_CUE_PAUSE_SECONDS)
        should_break = (
            final
            or token[-1] in punctuation
            or pause
            or group_chars >= REAZONSPEECH_CUE_MAX_CHARS
            or group_time >= REAZONSPEECH_CUE_MAX_SECONDS)
        if (
                should_break
                and not final
                and next_start
                <= checked_timestamps[group_start] + 0.001):
            should_break = False
        if not should_break:
            continue

        local_start = checked_timestamps[group_start]
        if final:
            local_end = min(
                window_duration,
                max(
                    checked_timestamps[index] + 0.5,
                    local_start + 0.04))
        else:
            local_end = min(window_duration, next_start)
        if (
                local_start < 0.0
                or local_end <= local_start
                or local_end > window_duration + 1e-6):
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned invalid timing')
        cue_text = ''.join(tokens[group_start:index + 1])
        absolute_start = min(
            window.end, window.start + local_start)
        absolute_end = min(
            window.end, window.start + local_end)
        if (
                absolute_start < window.start - 1e-6
                or absolute_end > window.end + 1e-6
                or absolute_end <= absolute_start):
            raise ReazonSpeechStructuralError(
                'ReazonSpeech returned invalid timing')
        recognized.append(
            RecognizedSegment(
                absolute_start, absolute_end, cue_text))
        group_start = index + 1
        group_chars = 0

    if (
            group_start != len(tokens)
            or ''.join(cue.text for cue in recognized) != text):
        raise ReazonSpeechStructuralError(
            'ReazonSpeech cue grouping changed the transcript')
    return recognized


def _run_reazonspeech(
        whisper_exe: str, pack_root: str, vad_model: str,
        wav_path: str, output_base: str, log_path: str,
        cancel_check: Optional[CancelCheck],
        progress_callback: Optional[ProgressCallback] = None,
        outcome_callback: Optional[RecognitionOutcomeCallback] = None,
) -> Optional[str]:
    del log_path
    runtime_dir = os.path.dirname(os.path.abspath(whisper_exe))
    vad_exe = os.path.join(
        runtime_dir, 'whisper-vad-speech-segments.exe')
    if (
            not os.path.isfile(whisper_exe)
            or not os.path.isfile(vad_exe)):
        raise _asr_failure('runtime')
    _verify_reazonspeech_install(
        pack_root, cancel_check=cancel_check)

    try:
        work_dir = tempfile.mkdtemp(
            prefix='uav-reazonspeech-',
            dir=os.path.dirname(os.path.abspath(wav_path)))
    except OSError as exc:
        raise _asr_failure('runtime') from exc
    try:
        total_frames, audio_duration = _validate_pcm16_wav(wav_path)
        islands = _run_external_vad(
            vad_exe, vad_model, wav_path, work_dir, cancel_check,
            audio_duration=audio_duration,
            progress_callback=progress_callback)
        clamped: list[SpeechIsland] = []
        for island in islands:
            if island.start > audio_duration + 0.5:
                raise SubtitleError(
                    'Speech detection returned invalid timing')
            start = min(max(0.0, island.start), audio_duration)
            end = min(max(0.0, island.end), audio_duration)
            if end <= start:
                raise SubtitleError(
                    'Speech detection returned invalid timing')
            clamped.append(SpeechIsland(start, end))
        islands = _merge_speech_islands(clamped)
        if not islands:
            return None
        windows = _build_recognition_windows(islands, audio_duration)
        _notify(progress_callback, 'transcribe_ja', 0)
        slice_bytes = sum(
            (
                max(
                    0,
                    min(
                        total_frames,
                        int(round(window.end * WHISPER_SAMPLE_RATE)),
                    )
                    - max(
                        0,
                        min(
                            total_frames,
                            int(round(
                                window.start * WHISPER_SAMPLE_RATE)),
                        ),
                    ),
                )
                * 2
                + 44
            )
            for window in windows
        )
        _ensure_asr_temp_space(work_dir, slice_bytes)

        relative_wavs: list[str] = []
        window_durations: list[float] = []
        for index, window in enumerate(windows):
            _check_cancel(cancel_check)
            relative_path = f'island-{index:05d}.wav'
            relative_wavs.append(relative_path)
            window_durations.append(_slice_pcm16_wav(
                wav_path,
                os.path.join(work_dir, relative_path),
                window,
                total_frames,
            ))

        try:
            recognized: list[RecognizedSegment] = []
            for batch_index, batch_start in enumerate(
                    range(
                        0, len(relative_wavs),
                        REAZONSPEECH_BATCH_SIZE)):
                _check_cancel(cancel_check)
                batch_end = min(
                    batch_start + REAZONSPEECH_BATCH_SIZE,
                    len(relative_wavs))
                batch_names = relative_wavs[batch_start:batch_end]
                payloads = _run_reazonspeech_cli_batch(
                    pack_root, work_dir, batch_names,
                    batch_index, cancel_check,
                    completed_offset=batch_start,
                    total_windows=len(relative_wavs),
                    batch_audio_seconds=sum(
                        window_durations[batch_start:batch_end]),
                    progress_callback=progress_callback)
                if len(payloads) != len(batch_names):
                    raise ReazonSpeechStructuralError(
                        'ReazonSpeech returned the wrong result count')
                for offset, payload in enumerate(payloads):
                    window_index = batch_start + offset
                    cues = _parse_reazonspeech_payload(
                        payload,
                        windows[window_index],
                        window_durations[window_index],
                    )
                    if not cues:
                        raise ReazonSpeechStructuralError(
                            'Speech was detected but ReazonSpeech returned '
                            'an empty window')
                    if len(recognized) + len(cues) > MAX_WHISPER_CUES:
                        raise ReazonSpeechStructuralError(
                            'ReazonSpeech returned too many segments')
                    recognized.extend(cues)
            if not recognized:
                raise ReazonSpeechStructuralError(
                    'Speech was detected but no valid subtitle cues '
                    'were produced')
        except ReazonSpeechStructuralError:
            fallback_model = _prepare_whisper_fallback_model(
                progress_callback, cancel_check)
            result = _run_whisper(
                whisper_exe, fallback_model, vad_model,
                wav_path, output_base, '', cancel_check,
                progress_callback=progress_callback)
            if outcome_callback:
                outcome_callback(RECOGNITION_PROFILES['balanced'], True)
            return result

        srt_cues = [
            SrtCue(
                str(index),
                (
                    f'{_format_srt_timestamp(cue.start)} --> '
                    f'{_format_srt_timestamp(cue.end)}'),
                cue.text,
            )
            for index, cue in enumerate(recognized, start=1)
        ]
        result = output_base + '.srt'
        try:
            _atomic_write_text(result, render_srt(srt_cues))
        except OSError as exc:
            raise _asr_failure('runtime') from exc
        _notify(progress_callback, 'transcribe_ja', 100)
        if outcome_callback:
            outcome_callback(RECOGNITION_PROFILES['auto'], False)
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run_recognition(
        profile: RecognitionProfile,
        exe: str, model: str, vad_model: str, wav_path: str,
        output_base: str, log_path: str,
        cancel_check: Optional[CancelCheck],
        progress_callback: Optional[ProgressCallback] = None,
        outcome_callback: Optional[RecognitionOutcomeCallback] = None,
) -> Optional[str]:
    if profile.engine == 'reazonspeech':
        return _run_reazonspeech(
            exe, model, vad_model, wav_path, output_base,
            log_path, cancel_check,
            progress_callback=progress_callback,
            outcome_callback=outcome_callback)
    result = _run_whisper(
        exe, model, vad_model, wav_path, output_base,
        log_path, cancel_check,
        progress_callback=progress_callback)
    if outcome_callback and result is not None:
        outcome_callback(profile, False)
    return result


def _atomic_copy(source: str, destination: str) -> None:
    destination_dir = os.path.dirname(destination)
    os.makedirs(destination_dir, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(
        prefix=os.path.basename(destination) + '.',
        suffix='.tmp', dir=destination_dir)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, destination)
        temp = ''
    finally:
        if temp:
            try:
                os.remove(temp)
            except FileNotFoundError:
                pass


def _safe_manifest_path(root: str, relative_path: str) -> str:
    relative = str(relative_path or '').replace('\\', '/')
    if not relative or relative.startswith('/') or ':' in relative:
        raise SubtitleError('Local translation model manifest has an unsafe path')
    target = os.path.abspath(os.path.join(root, *relative.split('/')))
    if os.path.commonpath([os.path.abspath(root), target]) != os.path.abspath(root):
        raise SubtitleError('Local translation model manifest has an unsafe path')
    return target


def _read_translation_manifest(
        root: str,
        cancel_check: Optional[CancelCheck] = None) -> dict:
    manifest_path = os.path.join(root, 'manifest.json')
    try:
        manifest_verified = (
            bool(TRANSLATION_MANIFEST_SHA256)
            and _sha256(manifest_path, cancel_check).lower()
            == TRANSLATION_MANIFEST_SHA256.lower())
    except OSError:
        manifest_verified = False
    if not manifest_verified:
        raise SubtitleError('Local translation model manifest failed verification')
    try:
        with open(manifest_path, 'r', encoding='utf-8') as handle:
            manifest = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        raise SubtitleError('Local translation model manifest is invalid') from exc
    if str(manifest.get('pack_version')) != TRANSLATION_PACK_VERSION:
        raise SubtitleError('Local translation model pack version is invalid')
    files = manifest.get('files')
    models = manifest.get('models')
    if not isinstance(files, list) or not isinstance(models, dict):
        raise SubtitleError('Local translation model manifest is incomplete')
    for key in ('ja-en', 'en-zh'):
        model = models.get(key)
        if not isinstance(model, dict) or not model.get('path'):
            raise SubtitleError('Local translation model manifest is incomplete')
    return manifest


def _verify_translation_install(
        root: str, require_marker: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
        progress_stage: Optional[str] = None,
        progress_start: int = 0,
        progress_end: int = 100) -> dict:
    _check_cancel(cancel_check)
    marker = os.path.join(root, '.source-sha256')
    if require_marker:
        try:
            with open(marker, 'r', encoding='ascii') as handle:
                if handle.read().strip().lower() != TRANSLATION_PACK_SHA256.lower():
                    raise SubtitleError('Local translation model marker is invalid')
        except OSError as exc:
            raise SubtitleError('Local translation model marker is missing') from exc

    manifest = _read_translation_manifest(root, cancel_check)
    entries = manifest['files']
    if progress_stage:
        _notify(progress_callback, progress_stage, progress_start)
    for index, entry in enumerate(entries):
        _check_cancel(cancel_check)
        if not isinstance(entry, dict):
            raise SubtitleError('Local translation model manifest is invalid')
        path = _safe_manifest_path(root, entry.get('path', ''))
        try:
            expected_size = int(entry['size'])
            expected_sha256 = str(entry['sha256'])
        except (KeyError, TypeError, ValueError) as exc:
            raise SubtitleError('Local translation model manifest is invalid') from exc
        if not _is_verified(
                path, expected_size, expected_sha256, cancel_check):
            raise SubtitleError('Local translation model failed integrity verification')
        if progress_stage and entries:
            percent = progress_start + int(
                (index + 1) * (progress_end - progress_start)
                / len(entries))
            _notify(
                progress_callback, progress_stage,
                min(progress_end, percent))

    resolved: dict[str, str] = {}
    for key in ('ja-en', 'en-zh'):
        _check_cancel(cancel_check)
        model_dir = _safe_manifest_path(root, manifest['models'][key]['path'])
        for filename in (
                'config.json', 'model.bin', 'shared_vocabulary.json',
                'source.spm', 'target.spm'):
            if not os.path.isfile(os.path.join(model_dir, filename)):
                raise SubtitleError('Local translation model is incomplete')
        if (
                key == 'ja-en'
                and not os.path.isfile(os.path.join(model_dir, 'vocab.json'))):
            raise SubtitleError(
                'Local Japanese translation tokenizer vocabulary is missing')
        resolved[key] = model_dir
    if progress_stage:
        _notify(progress_callback, progress_stage, progress_end)
    return resolved


def _cleanup_translation_install_dirs(parent: str) -> None:
    parent = os.path.abspath(parent)
    prefix = f'translation-v{TRANSLATION_PACK_VERSION}-install-'
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        target = os.path.abspath(os.path.join(parent, name))
        if os.path.commonpath([parent, target]) != parent:
            continue
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)


def _remove_translation_cache_path(path: str, parent: str) -> None:
    path = os.path.abspath(path)
    parent = os.path.abspath(parent)
    if os.path.commonpath([parent, path]) != parent or path == parent:
        raise SubtitleError('Unsafe local translation cache path')
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def _remove_translation_cache_path_best_effort(path: str, parent: str) -> None:
    try:
        _remove_translation_cache_path(path, parent)
    except (OSError, SubtitleError):
        # An obsolete backup must not block an already verified active runtime.
        pass


def _prepare_translation_runtime(
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> dict[str, str]:
    lock_name = f'translation-v{TRANSLATION_PACK_VERSION}'
    with _interprocess_cache_lock(lock_name, cancel_check):
        return _prepare_translation_runtime_locked(
            progress_callback, cancel_check)


def _prepare_translation_runtime_locked(
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck]) -> dict[str, str]:
    with _translation_runtime_lock:
        _check_cancel(cancel_check)
        root = _cache_root()
        runtime_dir = os.path.join(
            root, 'translation', f'v{TRANSLATION_PACK_VERSION}')
        parent = os.path.dirname(runtime_dir)
        os.makedirs(parent, exist_ok=True)
        _cleanup_translation_install_dirs(parent)
        previous = runtime_dir + '.previous'
        if not os.path.exists(runtime_dir) and os.path.exists(previous):
            os.replace(previous, runtime_dir)
        try:
            installed = _verify_translation_install(
                runtime_dir, cancel_check=cancel_check)
            if os.path.exists(previous):
                _remove_translation_cache_path_best_effort(previous, parent)
            return installed
        except SubtitleCancelled:
            raise
        except SubtitleError:
            pass

        # A process may have terminated in the tiny interval between moving
        # the previous runtime aside and activating the new one.  Prefer a
        # fully verified backup over downloading again.
        if os.path.exists(previous):
            try:
                _verify_translation_install(
                    previous, cancel_check=cancel_check)
            except SubtitleCancelled:
                raise
            except SubtitleError:
                pass
            else:
                _remove_translation_cache_path(runtime_dir, parent)
                os.replace(previous, runtime_dir)
                return _verify_translation_install(
                    runtime_dir, cancel_check=cancel_check)

        for candidate in _component_install_candidates(
                TRANSLATION_PACK_NAME):
            try:
                installed = _verify_translation_install(
                    candidate, require_marker=False,
                    cancel_check=cancel_check)
            except SubtitleCancelled:
                raise
            except SubtitleError:
                continue
            _notify(progress_callback, 'translation_model', 100)
            return installed

        if not TRANSLATION_PACK_SIZE or not TRANSLATION_PACK_SHA256:
            raise SubtitleError('Local translation model pack is not configured')
        _notify(progress_callback, 'translation_model', 0)
        managed_archive = os.path.join(
            root, 'downloads', TRANSLATION_PACK_NAME)
        archive = _find_verified_component_archive(
            TRANSLATION_PACK_NAME, TRANSLATION_PACK_SIZE,
            TRANSLATION_PACK_SHA256, cancel_check)

        def download_progress(
                _stage: str, percent: Optional[int]) -> None:
            mapped = (
                None if percent is None
                else min(70, max(0, int(percent * 0.70))))
            _notify(
                progress_callback, 'translation_model', mapped)

        if archive:
            _notify(progress_callback, 'translation_model', 70)
        else:
            archive = _download_verified(
                TRANSLATION_PACK_URL, managed_archive,
                TRANSLATION_PACK_SIZE, TRANSLATION_PACK_SHA256,
                'translation_model', download_progress, cancel_check)

        temp_dir = tempfile.mkdtemp(
            prefix=f'translation-v{TRANSLATION_PACK_VERSION}-install-',
            dir=parent)
        try:
            _safe_extract_zip(
                archive, temp_dir, progress_callback, cancel_check,
                'translation_model', 70, 85)
            _verify_translation_install(
                temp_dir, require_marker=False,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                progress_stage='translation_model',
                progress_start=85,
                progress_end=95)
            _check_cancel(cancel_check)
            with open(
                    os.path.join(temp_dir, '.source-sha256'),
                    'w', encoding='ascii') as handle:
                handle.write(TRANSLATION_PACK_SHA256)
            if os.path.exists(previous):
                _remove_translation_cache_path_best_effort(previous, parent)
            had_previous = os.path.exists(runtime_dir)
            if had_previous:
                os.replace(runtime_dir, previous)
            try:
                os.replace(temp_dir, runtime_dir)
                temp_dir = ''
            except Exception:
                if had_previous and not os.path.exists(runtime_dir):
                    os.replace(previous, runtime_dir)
                raise
            if os.path.exists(previous):
                _remove_translation_cache_path(previous, parent)
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

        # The extracted, per-file verified pack is sufficient.  Do not keep a
        # second ~140 MB archive in the user's cache.
        if os.path.normcase(os.path.abspath(archive)) == os.path.normcase(
                os.path.abspath(managed_archive)):
            try:
                os.remove(archive)
                _verified_paths.pop(os.path.abspath(archive), None)
            except OSError:
                pass
        installed = _verify_translation_install(
            runtime_dir,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            progress_stage='translation_model',
            progress_start=95,
            progress_end=100)
        _notify(progress_callback, 'translation_model', 100)
        return installed


def _translation_memory_path() -> str:
    return os.path.join(
        _cache_root(), 'translation', 'translation-memory.sqlite3')


def _translation_memory_version() -> str:
    from uav_downloader.subtitles.domain import DOMAIN_VERSION
    return (
        f'{TRANSLATION_ENGINE_VERSION}+{DOMAIN_VERSION}'
        f'+manifest:{TRANSLATION_MANIFEST_SHA256}')


def _translation_api_endpoint(profile) -> str:
    """Return the canonical, non-secret endpoint used in cache identity."""
    provider = str(profile.provider)
    model = str(profile.model)
    base_url = str(profile.base_url).rstrip('/')
    if provider in ('openai', 'openai-compatible'):
        endpoint = (
            base_url if base_url.endswith('/chat/completions')
            else base_url + '/chat/completions'
        )
    elif provider == 'anthropic':
        endpoint = (
            base_url if base_url.endswith('/messages')
            else base_url + '/messages'
        )
    elif provider == 'gemini':
        endpoint = (
            base_url if base_url.endswith(':generateContent')
            else base_url + '/models/'
            + quote(model, safe='-._') + ':generateContent'
        )
    else:
        raise SubtitleError('Unsupported translation provider')
    parsed = urlsplit(endpoint)
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path,
        '',
        '',
    ))


def _translation_api_memory_version(profile) -> str:
    """Isolate API cache rows by provider, model, and endpoint—not API key."""
    from uav_downloader.subtitles.domain import DOMAIN_VERSION
    identity = json.dumps(
        {
            'provider': str(profile.provider),
            'model': str(profile.model),
            'endpoint': _translation_api_endpoint(profile),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    digest = hashlib.sha256(identity).hexdigest()
    return f'llm-subtitle-v1+{DOMAIN_VERSION}+profile:{digest}'


def _translation_memory_lookup(
        texts: list[str], source_language: str,
        target_language: str,
        engine_version: Optional[str] = None) -> dict[str, str]:
    if not texts:
        return {}
    version = engine_version or _translation_memory_version()
    path = _translation_memory_path()
    if not os.path.isfile(path):
        return {}
    try:
        with _translation_memory_lock, sqlite3.connect(path, timeout=5) as db:
            rows = []
            for start in range(0, len(texts), 500):
                batch = texts[start:start + 500]
                placeholders = ','.join('?' for _ in batch)
                query = (
                    'SELECT source_text, translated_text FROM translations '
                    'WHERE engine_version = ? AND source_language = ? '
                    f'AND target_language = ? '
                    f'AND source_text IN ({placeholders})'
                )
                rows.extend(db.execute(
                    query,
                    (version, source_language,
                     target_language, *batch),
                ).fetchall())
        return {str(source): str(translated) for source, translated in rows}
    except sqlite3.Error:
        # Translation must remain usable if an old or locked cache is damaged.
        return {}


def _translation_memory_store(
        pairs: dict[str, str], source_language: str,
        target_language: str,
        engine_version: Optional[str] = None) -> None:
    if not pairs:
        return
    version = engine_version or _translation_memory_version()
    path = _translation_memory_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _translation_memory_lock, sqlite3.connect(path, timeout=5) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute(
                'CREATE TABLE IF NOT EXISTS translations ('
                'engine_version TEXT NOT NULL, '
                'source_language TEXT NOT NULL, '
                'target_language TEXT NOT NULL, '
                'source_text TEXT NOT NULL, '
                'translated_text TEXT NOT NULL, '
                'updated_at INTEGER NOT NULL, '
                'PRIMARY KEY (engine_version, source_language, '
                'target_language, source_text))')
            now = int(time.time())
            db.executemany(
                'INSERT OR REPLACE INTO translations '
                '(engine_version, source_language, target_language, '
                'source_text, translated_text, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                [
                    (version, source_language,
                     target_language, source, translated, now)
                    for source, translated in pairs.items()
                ],
            )
    except (OSError, sqlite3.Error):
        # A cache write is an optimization and must never discard subtitles.
        return


def _validate_translation(
        source: str, translated: str,
        origin: str = 'Local translation model') -> str:
    cleaned = re.sub(r'\s+', ' ', str(translated or '')).strip()
    if source and not cleaned:
        raise SubtitleError(f'{origin} returned an empty cue')
    if '-->' in cleaned or re.search(
            r'\d{1,2}:\d{2}:\d{2}[,.]\d{3}', cleaned):
        raise SubtitleError(f'{origin} returned timing text')
    if len(cleaned) > max(240, len(source) * 12 + 80):
        raise SubtitleError(f'{origin} returned an invalid cue')
    return cleaned


def _clean_model_text(text: str) -> str:
    return re.sub(
        r'\s+', ' ',
        unicodedata.normalize('NFKC', str(text or ''))).strip()


def _restore_terminal_punctuation(
        source: str, translated: str, target_language: str) -> str:
    result = str(translated or '').strip()
    if not source or not result:
        return result
    if target_language == 'zh-TW':
        result = re.sub(r'\?$', '？', result)
        result = re.sub(r'!$', '！', result)
        result = re.sub(r'\.$', '。', result)
    if re.search(r'[.!?。！？…]$', result):
        return result
    source = source.rstrip()
    if source.endswith(('?', '？')):
        return result + ('？' if target_language == 'zh-TW' else '?')
    if source.endswith(('!', '！')):
        return result + ('！' if target_language == 'zh-TW' else '!')
    if source.endswith(('...', '…')):
        return result + ('……' if target_language == 'zh-TW' else '…')
    if source.endswith(('.', '。')):
        return result + ('。' if target_language == 'zh-TW' else '.')
    return result


def _load_translation_worker_json(path: str, limit: int) -> object:
    try:
        with open(path, 'rb') as handle:
            encoded = handle.read(limit + 1)
    except OSError as exc:
        raise SubtitleError(
            'Local translation worker data is unavailable') from exc
    if len(encoded) > limit:
        raise SubtitleError('Local translation worker returned too much data')
    try:
        return json.loads(encoded.decode('utf-8', errors='strict'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SubtitleError(
            'Local translation worker returned invalid data') from exc


def _validate_translation_worker_request(
        payload: object) -> tuple[str, list[str], Optional[str]]:
    if not isinstance(payload, dict) or set(payload) != {
            'schema', 'model_dir', 'texts', 'target_token'}:
        raise SubtitleError('Local translation worker request is invalid')
    if (
            type(payload.get('schema')) is not int
            or payload['schema'] != LOCAL_TRANSLATION_WORKER_SCHEMA):
        raise SubtitleError('Local translation worker request is invalid')
    model_dir = payload.get('model_dir')
    texts = payload.get('texts')
    target_token = payload.get('target_token')
    if (
            not isinstance(model_dir, str)
            or not model_dir.strip()
            or not os.path.isabs(model_dir)
            or not os.path.isdir(model_dir)):
        raise SubtitleError('Local translation model is unavailable')
    if (
            not isinstance(texts, list)
            or len(texts) > MAX_WHISPER_CUES
            or any(not isinstance(text, str) for text in texts)):
        raise SubtitleError('Local translation worker request is invalid')
    if target_token not in (None, '>>cmn_Hant<<'):
        raise SubtitleError('Local translation worker request is invalid')
    return os.path.abspath(model_dir), list(texts), target_token


def _run_local_model_in_process(
        model_dir: str, texts: list[str],
        target_token: Optional[str],
        cancel_check: Optional[CancelCheck],
        progress_callback: Optional[TranslationProgressCallback] = None,
) -> list[str]:
    """Run CTranslate2 inside the disposable translation worker process."""
    _check_cancel(cancel_check)
    try:
        import ctranslate2
        import sentencepiece as sentencepiece
    except Exception as exc:
        raise SubtitleError(
            'Local translation runtime is unavailable; reinstall the app') from exc

    _check_cancel(cancel_check)
    source_processor = sentencepiece.SentencePieceProcessor(
        model_file=os.path.join(model_dir, 'source.spm'))
    target_processor = sentencepiece.SentencePieceProcessor(
        model_file=os.path.join(model_dir, 'target.spm'))
    source_vocabulary = None
    vocabulary_path = os.path.join(model_dir, 'vocab.json')
    if os.path.isfile(vocabulary_path):
        try:
            with open(vocabulary_path, 'r', encoding='utf-8') as handle:
                vocabulary_payload = json.load(handle)
            if (
                    not isinstance(vocabulary_payload, dict)
                    or '<unk>' not in vocabulary_payload):
                raise ValueError('invalid tokenizer vocabulary')
            source_vocabulary = frozenset(
                str(token) for token in vocabulary_payload)
        except (OSError, TypeError, ValueError) as exc:
            raise SubtitleError(
                'Local translation tokenizer vocabulary is invalid') from exc
    threads = max(1, min(8, (os.cpu_count() or 4) - 1))
    try:
        _check_cancel(cancel_check)
        translator = ctranslate2.Translator(
            model_dir, device='cpu', compute_type='int8',
            inter_threads=1, intra_threads=threads)
        _check_cancel(cancel_check)
        translated: list[str] = []
        # Smaller independent batches keep cancellation responsive without
        # materially reducing CPU throughput for subtitle-sized cues.  The
        # parent also treats each completed batch as watchdog progress.
        batch_size = 16
        for start in range(0, len(texts), batch_size):
            _check_cancel(cancel_check)
            batch = texts[start:start + batch_size]
            source_tokens = []
            for text in batch:
                tokens = source_processor.encode(text, out_type=str)
                if source_vocabulary is not None:
                    tokens = [
                        token if token in source_vocabulary else '<unk>'
                        for token in tokens
                    ]
                if target_token:
                    tokens.insert(0, target_token)
                # Models converted from Transformers do not add tokenizer
                # special tokens automatically.
                tokens.append('</s>')
                source_tokens.append(tokens)
            results = translator.translate_batch(
                source_tokens, beam_size=4, max_batch_size=batch_size,
                batch_type='examples', max_decoding_length=160)
            _check_cancel(cancel_check)
            if len(results) != len(batch):
                raise SubtitleError(
                    'Local translation model returned the wrong cue count')
            for result in results:
                tokens = [
                    token for token in result.hypotheses[0]
                    if token not in ('<s>', '</s>', '<pad>')
                    and not re.fullmatch(r'>>[^<>]+<<', token)
                ]
                translated.append(target_processor.decode(tokens).strip())
            if progress_callback:
                progress_callback(
                    min(start + len(batch), len(texts)), len(texts))
        return translated
    except SubtitleError:
        raise
    except Exception as exc:
        raise SubtitleError(
            f'Local translation failed: {type(exc).__name__}') from exc


def _local_translation_worker_entry(
        request_path: str, result_path: str, progress_path: str) -> None:
    """Multiprocessing target with a bounded, file-based IPC contract."""
    try:
        payload = _load_translation_worker_json(
            request_path, LOCAL_TRANSLATION_WORKER_MAX_BYTES)
        model_dir, texts, target_token = (
            _validate_translation_worker_request(payload))

        def report_progress(completed: int, total: int) -> None:
            _atomic_write_text(
                progress_path,
                json.dumps(
                    {
                        'schema': LOCAL_TRANSLATION_WORKER_SCHEMA,
                        'completed': int(completed),
                        'total': int(total),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(',', ':'),
                ) + '\n',
            )

        translations = _run_local_model_in_process(
            model_dir, texts, target_token, None, report_progress)
        result = {
            'schema': LOCAL_TRANSLATION_WORKER_SCHEMA,
            'ok': True,
            'translations': translations,
        }
    except BaseException as exc:
        if isinstance(exc, SubtitleError):
            message = str(exc).strip()
        else:
            message = (
                f'Local translation worker failed: '
                f'{type(exc).__name__}')
        if not message or len(message) > 300:
            message = 'Local translation worker failed'
        result = {
            'schema': LOCAL_TRANSLATION_WORKER_SCHEMA,
            'ok': False,
            'error': message,
        }
    try:
        encoded_result = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ) + '\n'
        if (
                len(encoded_result.encode('utf-8'))
                > LOCAL_TRANSLATION_WORKER_MAX_BYTES):
            encoded_result = json.dumps(
                {
                    'schema': LOCAL_TRANSLATION_WORKER_SCHEMA,
                    'ok': False,
                    'error': 'Local translation worker returned too much data',
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(',', ':'),
            ) + '\n'
        _atomic_write_text(result_path, encoded_result)
    except BaseException:
        # The parent treats a missing or malformed result as a worker failure.
        return


def _translation_worker_progress(
        path: str, expected_total: int,
) -> Optional[tuple[int, int]]:
    try:
        payload = _load_translation_worker_json(path, 4096)
    except SubtitleError:
        return None
    if not isinstance(payload, dict) or set(payload) != {
            'schema', 'completed', 'total'}:
        return None
    schema = payload.get('schema')
    completed = payload.get('completed')
    total = payload.get('total')
    if (
            type(schema) is not int
            or schema != LOCAL_TRANSLATION_WORKER_SCHEMA
            or type(completed) is not int
            or type(total) is not int
            or total != expected_total
            or completed < 0
            or completed > total):
        return None
    return completed, total


def _terminate_translation_worker(process) -> bool:
    try:
        alive = process.is_alive()
    except (AssertionError, OSError, ValueError):
        return False
    if alive:
        try:
            process.terminate()
        except (AttributeError, OSError, ValueError):
            pass
        try:
            process.join(5)
        except (AssertionError, OSError, ValueError):
            pass
    try:
        alive = process.is_alive()
    except (AssertionError, OSError, ValueError):
        return False
    if alive:
        try:
            process.kill()
        except (AttributeError, OSError, ValueError):
            pass
        try:
            process.join(5)
        except (AssertionError, OSError, ValueError):
            pass
    try:
        return not process.is_alive()
    except (AssertionError, OSError, ValueError):
        return False


def _validate_translation_worker_result(
        path: str, expected_count: int) -> list[str]:
    payload = _load_translation_worker_json(
        path, LOCAL_TRANSLATION_WORKER_MAX_BYTES)
    if (
            not isinstance(payload, dict)
            or type(payload.get('schema')) is not int
            or payload.get('schema') != LOCAL_TRANSLATION_WORKER_SCHEMA
            or type(payload.get('ok')) is not bool):
        raise SubtitleError(
            'Local translation worker returned invalid data')
    if not payload['ok']:
        if set(payload) != {'schema', 'ok', 'error'}:
            raise SubtitleError(
                'Local translation worker returned invalid data')
        message = payload.get('error')
        if not isinstance(message, str) or not message or len(message) > 300:
            raise SubtitleError('Local translation worker failed')
        raise SubtitleError(message)
    if set(payload) != {'schema', 'ok', 'translations'}:
        raise SubtitleError(
            'Local translation worker returned invalid data')
    translations = payload.get('translations')
    if (
            not isinstance(translations, list)
            or len(translations) != expected_count
            or any(not isinstance(text, str) for text in translations)):
        raise SubtitleError(
            'Local translation worker returned invalid data')
    return list(translations)


def _run_local_model(
        model_dir: str, texts: list[str],
        target_token: Optional[str],
        cancel_check: Optional[CancelCheck],
        progress_callback: Optional[TranslationProgressCallback] = None,
) -> list[str]:
    """Run one model request in a supervised, disposable child process.

    CTranslate2 retains native allocator reservations after a Translator is
    destroyed on Windows.  UAV Watcher processes many videos sequentially, so
    rebuilding translators in the GUI process caused private memory to grow
    on every video.  The OS now reclaims the complete native runtime when this
    one-shot worker exits, and the parent can terminate a stalled inference.
    """
    _check_cancel(cancel_check)
    model_dir = os.path.abspath(model_dir)
    if not os.path.isdir(model_dir):
        raise SubtitleError('Local translation model is unavailable')
    if (
            not isinstance(texts, list)
            or len(texts) > MAX_WHISPER_CUES
            or any(not isinstance(text, str) for text in texts)
            or target_token not in (None, '>>cmn_Hant<<')):
        raise SubtitleError('Local translation worker request is invalid')

    work_dir = tempfile.mkdtemp(prefix='uav-translation-worker-')
    request_path = os.path.join(work_dir, 'request.json')
    result_path = os.path.join(work_dir, 'result.json')
    progress_path = os.path.join(work_dir, 'progress.json')
    process = None
    started = False
    worker_stopped = True
    try:
        request = {
            'schema': LOCAL_TRANSLATION_WORKER_SCHEMA,
            'model_dir': model_dir,
            'texts': texts,
            'target_token': target_token,
        }
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        ) + '\n'
        if len(encoded.encode('utf-8')) > LOCAL_TRANSLATION_WORKER_MAX_BYTES:
            raise SubtitleError(
                'Local translation worker request is too large')
        _atomic_write_text(request_path, encoded)

        try:
            context = multiprocessing.get_context('spawn')
            process = context.Process(
                target=_local_translation_worker_entry,
                args=(request_path, result_path, progress_path),
            )
            process.start()
            started = True
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            started = bool(
                process is not None
                and getattr(process, 'pid', None) is not None)
            raise SubtitleError(
                'Local translation worker could not start') from exc
        began_at = time.monotonic()
        last_activity = began_at
        next_heartbeat = began_at
        last_progress: Optional[tuple[int, int]] = None
        while process.is_alive():
            _check_cancel(cancel_check)
            now = time.monotonic()
            progress = _translation_worker_progress(
                progress_path, len(texts))
            if progress is not None and progress != last_progress:
                last_progress = progress
                last_activity = now
                if progress_callback:
                    progress_callback(*progress)
            if now >= next_heartbeat:
                if progress_callback:
                    progress_callback(
                        *(last_progress or (0, len(texts))))
                next_heartbeat = (
                    now + SUBTITLE_PROCESS_HEARTBEAT_SECONDS)
            if (
                    now - began_at
                    > LOCAL_TRANSLATION_WORKER_TIMEOUT_SECONDS):
                raise SubtitleProcessTimeout(
                    'Local subtitle translation exceeded its time limit')
            if (
                    now - last_activity
                    > LOCAL_TRANSLATION_WORKER_STALL_TIMEOUT_SECONDS):
                raise SubtitleProcessTimeout(
                    'Local subtitle translation stopped responding')
            process.join(0.1)

        process.join()
        if process.exitcode != 0:
            raise SubtitleError(
                'Local translation worker exited unexpectedly')
        translations = _validate_translation_worker_result(
            result_path, len(texts))
        if progress_callback:
            progress_callback(len(texts), len(texts))
        return translations
    finally:
        if process is not None and started:
            worker_stopped = _terminate_translation_worker(process)
            try:
                if worker_stopped:
                    process.close()
            except (AttributeError, AssertionError, OSError, ValueError):
                pass
        if worker_stopped:
            shutil.rmtree(work_dir, ignore_errors=True)
        else:
            # A still-running child may still be reading its request or
            # writing progress.  Preserve that IPC directory instead of
            # racing deletion against the orphan and report a deterministic
            # failure to the owning subtitle job.
            raise SubtitleError(
                'Local translation worker could not be stopped')


@lru_cache(maxsize=1)
def _taiwan_converter():
    from opencc import OpenCC
    return OpenCC('s2twp')


def _to_taiwan_chinese(text: str) -> str:
    try:
        converted = _taiwan_converter().convert(text)
    except Exception as exc:
        raise SubtitleError(
            'Taiwan Chinese conversion runtime is unavailable') from exc
    from uav_downloader.subtitles.domain import postprocess_taiwan
    return postprocess_taiwan(converted)


def _translate_direct(
        texts: list[str], source_language: str, target_language: str,
        progress_stage: str,
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck],
        polish_english_output: bool = True) -> list[str]:
    from uav_downloader.subtitles.domain import (
        exact_translation,
        normalize_cue,
        postprocess_english,
    )

    if (source_language, target_language) not in {
            ('ja', 'en'), ('en', 'zh-TW')}:
        raise SubtitleError(
            f'Unsupported local translation pair: '
            f'{source_language} -> {target_language}')
    if not texts:
        return []

    model_inputs = [_clean_model_text(text) for text in texts]
    normalized = [normalize_cue(text) for text in model_inputs]
    unique = list(dict.fromkeys(text for text in normalized if text))
    representative = {}
    for key, model_input in zip(normalized, model_inputs):
        if key and key not in representative:
            # Use the normalized key for model work so decorative terminal
            # punctuation does not make deduplication input-order dependent.
            representative[key] = key
    resolved: dict[str, str] = {}

    if source_language == 'ja':
        for source in unique:
            override = exact_translation(
                representative[source], source_language, target_language)
            if override is not None:
                resolved[source] = override

    misses = [source for source in unique if source not in resolved]
    cached = _translation_memory_lookup(
        misses, source_language, target_language)
    for source, translated in cached.items():
        try:
            resolved[source] = _validate_translation(source, translated)
        except SubtitleError:
            # Treat a malformed cache record as a miss so the local model can
            # replace it instead of poisoning this cue indefinitely.
            continue
    misses = [source for source in misses if source not in resolved]

    if misses:
        models = _prepare_translation_runtime(
            progress_callback, cancel_check)
        model_key = 'ja-en' if source_language == 'ja' else 'en-zh'
        target_token = (
            '>>cmn_Hant<<' if target_language == 'zh-TW' else None)

        def report_model_progress(completed: int, total: int) -> None:
            percent = 0 if total <= 0 else int(completed * 80 / total)
            _notify(progress_callback, progress_stage, percent)

        model_outputs = _run_local_model(
            models[model_key],
            [representative[source] for source in misses],
            target_token, cancel_check, report_model_progress)
        if len(model_outputs) != len(misses):
            raise SubtitleError(
                'Local translation model returned the wrong cue count')
        new_pairs: dict[str, str] = {}
        for source, translated in zip(misses, model_outputs):
            if target_language == 'zh-TW':
                translated = _to_taiwan_chinese(translated)
            translated = _restore_terminal_punctuation(
                representative[source], translated, target_language)
            translated = _validate_translation(source, translated)
            resolved[source] = translated
            new_pairs[source] = translated
        _translation_memory_store(
            new_pairs, source_language, target_language)

    output: list[str] = []
    for index, source in enumerate(normalized):
        _check_cancel(cancel_check)
        translated = '' if not source else resolved[source]
        if target_language == 'en' and polish_english_output:
            translated = postprocess_english(translated)
        translated = _restore_terminal_punctuation(
            model_inputs[index], translated, target_language)
        output.append(_validate_translation(source, translated))
        _notify(
            progress_callback, progress_stage,
            80 + int((index + 1) * 20 / len(normalized)))
    return output


def _selected_translation_profile():
    """Load the selected provider without importing settings at app startup."""
    try:
        from uav_downloader.subtitles.settings import get_translation_settings
        return get_translation_settings()
    except Exception as exc:
        raise SubtitleError(
            'Translation provider settings could not be loaded') from exc


@contextmanager
def _translation_profile_scope(profile):
    """Pin one provider choice for the duration of a subtitle job."""
    had_previous = hasattr(_translation_provider_state, 'profile')
    previous = getattr(_translation_provider_state, 'profile', None)
    _translation_provider_state.profile = profile
    try:
        yield
    finally:
        if had_previous:
            _translation_provider_state.profile = previous
        else:
            try:
                del _translation_provider_state.profile
            except AttributeError:
                pass


def _translate_api_direct(
        texts: list[str], source_language: str, target_language: str,
        progress_stage: str,
        progress_callback: Optional[ProgressCallback],
        cancel_check: Optional[CancelCheck],
        profile) -> list[str]:
    """Translate Japanese cues directly with an explicitly selected provider."""
    from uav_downloader.subtitles.llm import (
        LlmTranslationCancelled,
        LlmTranslationError,
        LlmTranslationSettings,
        translate_cues as translate_llm_cues,
    )
    from uav_downloader.subtitles.domain import (
        exact_translation,
        normalize_cue,
        postprocess_english,
    )

    if source_language != 'ja' or target_language not in ('en', 'zh-TW'):
        raise SubtitleError(
            'External translation requires Japanese source subtitles')
    if not texts:
        return []

    model_inputs = [_clean_model_text(text) for text in texts]
    normalized = [normalize_cue(text) for text in model_inputs]
    unique = list(dict.fromkeys(text for text in normalized if text))
    representative = {
        key: key for key in normalized if key
    }
    resolved: dict[str, str] = {}
    for source in unique:
        override = exact_translation(
            representative[source], 'ja', target_language)
        if override is not None:
            resolved[source] = override

    cache_version = _translation_api_memory_version(profile)
    misses = [source for source in unique if source not in resolved]
    cached = _translation_memory_lookup(
        misses, 'ja', target_language, cache_version)
    for source, translated in cached.items():
        try:
            resolved[source] = _validate_translation(
                source, translated, 'Translation cache')
        except SubtitleError:
            continue
    misses = [source for source in misses if source not in resolved]

    if misses:
        llm_settings = LlmTranslationSettings(
            enabled=True,
            provider=str(profile.provider),
            model=str(profile.model),
            base_url=str(profile.base_url),
        )

        def report_api_progress(completed: int, total: int) -> None:
            percent = 0 if total <= 0 else int(completed * 80 / total)
            _notify(progress_callback, progress_stage, percent)

        try:
            result = translate_llm_cues(
                [representative[source] for source in misses],
                llm_settings,
                api_key=str(profile.api_key or ''),
                source_language='Japanese',
                target_language=(
                    'English'
                    if target_language == 'en'
                    else 'Taiwan Traditional Chinese'
                ),
                cancel_check=cancel_check,
                progress_callback=report_api_progress,
            )
        except LlmTranslationCancelled as exc:
            raise SubtitleCancelled(
                'Subtitle translation was cancelled') from exc
        except LlmTranslationError as exc:
            message = str(exc).strip()
            secret = str(profile.api_key or '')
            if secret:
                message = message.replace(secret, '[redacted]')
            if not message or len(message) > 300:
                message = (
                    f'{profile.provider} subtitle translation failed')
            # Do not retain a provider exception as ``__cause__``: defensive
            # callers may format chained tracebacks into crash logs, and a
            # third-party adapter must not get a chance to echo credentials.
            raise SubtitleError(message) from None
        except SubtitleCancelled:
            raise
        except Exception as exc:
            raise SubtitleError(
                f'{profile.provider} subtitle translation failed') from exc

        model_outputs = list(result.translations)
        if len(model_outputs) != len(misses):
            raise SubtitleError(
                f'{profile.provider} returned the wrong cue count')
        new_pairs: dict[str, str] = {}
        for source, translated in zip(misses, model_outputs):
            _check_cancel(cancel_check)
            if target_language == 'zh-TW':
                translated = _to_taiwan_chinese(translated)
            translated = _restore_terminal_punctuation(
                representative[source], translated, target_language)
            translated = _validate_translation(
                source, translated, f'{profile.provider} translation')
            resolved[source] = translated
            new_pairs[source] = translated
        _translation_memory_store(
            new_pairs, 'ja', target_language, cache_version)

    output: list[str] = []
    progress_start = 80 if misses else 0
    progress_span = 100 - progress_start
    for index, source in enumerate(normalized):
        _check_cancel(cancel_check)
        translated = '' if not source else resolved[source]
        if target_language == 'en':
            translated = postprocess_english(translated)
        translated = _restore_terminal_punctuation(
            model_inputs[index], translated, target_language)
        output.append(_validate_translation(
            source, translated, f'{profile.provider} translation'))
        _notify(
            progress_callback,
            progress_stage,
            progress_start
            + int((index + 1) * progress_span / len(normalized)),
        )
    return output


def translate_cues(
        texts: list[str], source_language: str, target_language: str,
        progress_stage: str,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None) -> list[str]:
    """Translate independent subtitle cues without changing cue boundaries."""
    _check_cancel(cancel_check)
    source_language = str(source_language)
    target_language = str(target_language)
    profile = getattr(_translation_provider_state, 'profile', None)
    if profile is not None and bool(getattr(profile, 'uses_api', False)):
        return _translate_api_direct(
            texts, source_language, target_language, progress_stage,
            progress_callback, cancel_check, profile)
    if (source_language, target_language) in {
            ('ja', 'en'), ('en', 'zh-TW')}:
        return _translate_direct(
            texts, source_language, target_language, progress_stage,
            progress_callback, cancel_check)
    if (source_language, target_language) == ('ja', 'zh-TW'):
        from uav_downloader.subtitles.domain import exact_translation, normalize_cue
        model_inputs = [_clean_model_text(text) for text in texts]
        normalized = [normalize_cue(text) for text in model_inputs]
        output: list[Optional[str]] = [None] * len(texts)
        unresolved_indices: list[int] = []
        for index, (source, model_input) in enumerate(
                zip(normalized, model_inputs)):
            _check_cancel(cancel_check)
            if not source:
                output[index] = ''
                continue
            override = exact_translation(model_input, 'ja', 'zh-TW')
            if override is None:
                unresolved_indices.append(index)
            else:
                output[index] = _validate_translation(source, override)
        if unresolved_indices:
            japanese = [model_inputs[index] for index in unresolved_indices]

            def pivot_progress(offset: int):
                def report(stage: str, percent: Optional[int]) -> None:
                    if stage == 'translation_model':
                        _notify(progress_callback, stage, percent)
                    elif stage == progress_stage:
                        mapped = (
                            None if percent is None
                            else min(100, offset + int(percent / 2)))
                        _notify(progress_callback, progress_stage, mapped)
                return report

            english = _translate_direct(
                japanese, 'ja', 'en', progress_stage,
                pivot_progress(0), cancel_check,
                polish_english_output=False)
            chinese = _translate_direct(
                english, 'en', 'zh-TW', progress_stage,
                pivot_progress(50), cancel_check)
            for index, translated in zip(unresolved_indices, chinese):
                _check_cancel(cancel_check)
                output[index] = translated
        _check_cancel(cancel_check)
        final = [str(item or '') for item in output]
        _notify(progress_callback, progress_stage, 100)
        _check_cancel(cancel_check)
        return final
    raise SubtitleError(
        f'Unsupported local translation pair: '
        f'{source_language} -> {target_language}')


def translate_srt(
        source_path: str, destination_path: str, target_language: str,
        progress_stage: str,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
        source_language: str = 'ja') -> str:
    _check_cancel(cancel_check)
    cues = parse_srt(_read_srt_text(source_path))
    if not cues:
        raise SubtitleError('Source subtitle does not contain any cues')

    translated_texts = translate_cues(
        [cue.text for cue in cues], source_language, target_language,
        progress_stage, progress_callback, cancel_check)
    if len(translated_texts) != len(cues):
        raise SubtitleError(
            'Local translation model returned the wrong cue count')
    translated_cues = [
        SrtCue(cue.index, cue.timing, translated_texts[index])
        for index, cue in enumerate(cues)
    ]
    _atomic_write_text(destination_path, render_srt(translated_cues))
    return destination_path


def translate_srt_to_zh_tw(
        source_path: str, destination_path: str,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_check: Optional[CancelCheck] = None,
        source_language: str = 'ja') -> str:
    return translate_srt(
        source_path, destination_path, 'zh-TW', 'translate_zh',
        progress_callback, cancel_check, source_language=source_language)


def run_whisper_diagnostic(
        input_path: str, destination_path: str) -> dict:
    """Exercise the real ASR pipeline and write transcript-free evidence."""
    if (
            not str(input_path or '').strip()
            or not str(destination_path or '').strip()):
        raise SubtitleError(
            'Speech recognition diagnostic paths are required')
    source = os.path.abspath(
        os.path.expanduser(str(input_path).strip()))
    destination = os.path.abspath(
        os.path.expanduser(str(destination_path).strip()))
    if os.path.normcase(source) == os.path.normcase(destination):
        raise SubtitleError(
            'Speech recognition diagnostic paths must differ')
    if not os.path.isfile(source):
        raise SubtitleError(
            'Speech recognition diagnostic input was not found')

    profile = recognition_profile()
    started = time.perf_counter()
    with _recognition_profile_scope(profile):
        exe, model, vad_model = _prepare_runtime(None, None)
    if (
            profile.engine == 'whisper'
            and os.path.basename(model) != profile.model_name):
        raise SubtitleError(
            'Speech recognition diagnostic model selection changed')

    cues: list[SrtCue] = []
    audio_duration = 0.0
    execution: dict[str, object] = {}

    def record_execution(
            actual_profile: RecognitionProfile,
            fallback_used: bool) -> None:
        execution.update({
            'profile': actual_profile,
            'fallback_used': bool(fallback_used),
        })

    with tempfile.TemporaryDirectory(
            prefix='uav-whisper-diagnostic-') as temp_dir:
        wav = os.path.join(temp_dir, 'audio.wav')
        log = os.path.join(temp_dir, 'audio.log')
        _extract_audio(source, wav, log, None)
        _frames, audio_duration = _validate_pcm16_wav(wav)
        result = _run_recognition(
            profile, exe, model, vad_model, wav,
            os.path.join(temp_dir, 'diagnostic'), log, None,
            outcome_callback=record_execution)
        if result:
            try:
                cues = parse_srt(_read_srt_text(result))
            except SubtitleError as exc:
                raise _asr_failure('runtime') from exc

    timing_pairs: list[list[int]] = []
    previous_end = -1
    timing_monotonic = True
    transcript_texts: list[str] = []
    for cue in cues:
        parts = cue.timing.split(' --> ')
        if len(parts) != 2:
            raise SubtitleError(
                'Speech recognition diagnostic returned invalid timing')
        start_ms = _parse_cli_timestamp(parts[0])
        end_ms = _parse_cli_timestamp(parts[1])
        if end_ms <= start_ms or start_ms < previous_end:
            timing_monotonic = False
        previous_end = end_ms
        timing_pairs.append([start_ms, end_ms])
        transcript_texts.append(cue.text)

    transcript_bytes = '\n'.join(transcript_texts).encode('utf-8')
    timing_bytes = json.dumps(
        timing_pairs, separators=(',', ':')).encode('ascii')
    wall_seconds = max(0.0, time.perf_counter() - started)
    actual_profile = execution.get('profile')
    if isinstance(actual_profile, RecognitionProfile):
        actual_engine = actual_profile.engine
        actual_runtime_version = (
            REAZONSPEECH_RUNTIME_VERSION
            if actual_engine == 'reazonspeech'
            else WHISPER_VERSION)
        actual_model_sha256 = actual_profile.model_sha256
        fallback_used = bool(execution.get('fallback_used'))
    else:
        # Auto can legitimately stop at the shared VAD gate when no speech is
        # present. In that case no ASR model or fallback was executed.
        actual_engine = 'vad'
        actual_runtime_version = WHISPER_VERSION
        actual_model_sha256 = None
        fallback_used = False
    payload = {
        'schema': 1,
        'kind': 'uav_whisper_diagnostic',
        'frozen': bool(getattr(sys, 'frozen', False)),
        'runtime_version': (
            REAZONSPEECH_RUNTIME_VERSION
            if profile.engine == 'reazonspeech'
            else WHISPER_VERSION),
        'profile': profile.key,
        'model_sha256': profile.model_sha256,
        'actual_engine': actual_engine,
        'actual_runtime_version': actual_runtime_version,
        'actual_model_sha256': actual_model_sha256,
        'fallback_used': fallback_used,
        'asr_signature': _asr_signature(profile),
        'audio_duration_ms': int(round(audio_duration * 1000.0)),
        'cue_count': len(cues),
        'no_speech': not bool(cues),
        'timing_monotonic': timing_monotonic,
        'first_cue_start_ms': (
            timing_pairs[0][0] if timing_pairs else None),
        'last_cue_end_ms': (
            timing_pairs[-1][1] if timing_pairs else None),
        'timing_sha256': hashlib.sha256(timing_bytes).hexdigest(),
        'transcript_characters': len(
            '\n'.join(transcript_texts)),
        'transcript_sha256': hashlib.sha256(
            transcript_bytes).hexdigest(),
        'wall_milliseconds': int(round(wall_seconds * 1000.0)),
        'real_time_factor': (
            round(wall_seconds / audio_duration, 4)
            if audio_duration > 0.0 else None
        ),
    }
    _atomic_write_text(
        destination,
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(',', ':')) + '\n')
    return payload


def run_local_translation_diagnostic(destination_path: str) -> dict:
    """Run the packaged FuguMT -> OPUS-MT -> OpenCC path and save evidence.

    Both GUI entry points expose this only when an explicit diagnostic
    environment variable is present.  It gives release verification a way to
    exercise the actual frozen native runtime without opening a window or
    adding a normal user-facing command-line mode.
    """

    if not str(destination_path or '').strip():
        raise SubtitleError(
            'Local translation diagnostic output path is required')
    destination = os.path.abspath(
        os.path.expanduser(str(destination_path).strip()))

    source = '今日は撮影の前に機材を確認してから始めます。'
    english = translate_cues(
        [source], 'ja', 'en', 'diagnostic_translate_en')[0]
    taiwan = translate_cues(
        [source], 'ja', 'zh-TW', 'diagnostic_translate_zh')[0]
    from uav_downloader.subtitles.domain import (
        DOMAIN_VERSION,
        GLOSSARY_VERSION,
        NONLEXICAL_COUNT,
        PHRASE_COUNT,
    )
    payload = {
        'schema': 1,
        'frozen': bool(getattr(sys, 'frozen', False)),
        'engine_version': _translation_memory_version(),
        'domain_version': DOMAIN_VERSION,
        'glossary_version': GLOSSARY_VERSION,
        'phrase_count': PHRASE_COUNT,
        'nonlexical_count': NONLEXICAL_COUNT,
        'source': source,
        'english': english,
        'taiwan': taiwan,
        'opencc': _to_taiwan_chinese('软件和视频在这里。'),
    }
    _atomic_write_text(
        destination,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
    return payload


def _translation_soak_process_metrics() -> dict[str, int]:
    """Return Windows parent-process metrics for frozen soak evidence."""
    if os.name != 'nt':
        raise SubtitleError(
            'Local translation soak diagnostic requires Windows')
    try:
        import ctypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ('cb', ctypes.c_ulong),
                ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
                ('PrivateUsage', ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetProcessHandleCount.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetProcessHandleCount.restype = ctypes.c_bool
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCountersEx),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_bool
        process = kernel32.GetCurrentProcess()
        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
                process, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        handle_count = ctypes.c_ulong()
        if not kernel32.GetProcessHandleCount(
                process, ctypes.byref(handle_count)):
            raise ctypes.WinError()
        return {
            'working_set_bytes': int(counters.WorkingSetSize),
            'private_bytes': int(counters.PrivateUsage),
            'handles': int(handle_count.value),
        }
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise SubtitleError(
            'Local translation soak metrics are unavailable') from exc


def run_local_translation_worker_soak_diagnostic(
        destination_path: str, iterations: int = 20) -> dict:
    """Exercise many one-shot native workers in one frozen parent process."""
    if not str(destination_path or '').strip():
        raise SubtitleError(
            'Local translation soak diagnostic output path is required')
    if (
            type(iterations) is not int
            or iterations < 1
            or iterations > 25):
        raise SubtitleError(
            'Local translation soak diagnostic iteration count is invalid')
    destination = os.path.abspath(
        os.path.expanduser(str(destination_path).strip()))
    models = _prepare_translation_runtime(None, None)
    rows = []
    for job in range(1, iterations + 1):
        started = time.perf_counter()
        english = _run_local_model(
            models['ja-en'],
            [f'これは字幕の連続テスト {job} です。'],
            None,
            None,
        )
        taiwan = _run_local_model(
            models['en-zh'],
            [f'This is sequential subtitle test number {job}.'],
            '>>cmn_Hant<<',
            None,
        )
        if (
                len(english) != 1
                or len(taiwan) != 1
                or not english[0]
                or not taiwan[0]):
            raise SubtitleError(
                'Local translation soak worker returned invalid output')
        try:
            temp_worker_dirs = sum(
                name.startswith('uav-translation-worker-')
                and os.path.isdir(os.path.join(tempfile.gettempdir(), name))
                for name in os.listdir(tempfile.gettempdir())
            )
        except OSError:
            temp_worker_dirs = -1
        rows.append({
            'job': job,
            'elapsed_ms': int(
                (time.perf_counter() - started) * 1000),
            **_translation_soak_process_metrics(),
            'temp_worker_dirs': int(temp_worker_dirs),
        })
    payload = {
        'schema': 1,
        'kind': 'uav_local_translation_worker_soak',
        'frozen': bool(getattr(sys, 'frozen', False)),
        'iterations': iterations,
        'worker_cycles': iterations * 2,
        'rows': rows,
    }
    _atomic_write_text(
        destination,
        json.dumps(payload, ensure_ascii=True, sort_keys=True) + '\n')
    return payload


def run_llm_translation_diagnostic(destination_path: str) -> dict:
    """Exercise the selected external provider and save redacted evidence.

    The frozen entry points expose this only through the explicit
    ``UAV_LLM_TRANSLATION_DIAGNOSTIC_OUTPUT`` release-diagnostic hook.  A
    unique, harmless cue prevents the request from being satisfied by the
    reviewed phrase corpus or translation memory.  Neither endpoint contents,
    credentials, source/response text, nor the full selected profile are
    written to disk.
    """

    if not str(destination_path or '').strip():
        raise SubtitleError(
            'LLM translation diagnostic output path is required')
    destination = os.path.abspath(
        os.path.expanduser(str(destination_path).strip()))

    profile = _selected_translation_profile()
    provider = str(getattr(profile, 'provider', '') or '').strip()
    model = str(getattr(profile, 'model', '') or '').strip()
    if not bool(getattr(profile, 'uses_api', False)):
        raise SubtitleError(
            'Select an external translation provider before this diagnostic')

    try:
        endpoint = _translation_api_endpoint(profile)
        endpoint_sha256 = hashlib.sha256(
            endpoint.encode('utf-8')).hexdigest()
        nonce = secrets.token_hex(8)
        source = (
            'これは字幕翻訳APIの接続診断です。'
            f'確認番号は{nonce}です。')
        with _translation_profile_scope(profile):
            translated = translate_cues(
                [source], 'ja', 'en', 'diagnostic_llm_translate_en')[0]
    except Exception:
        # Provider adapters intentionally expose only safe messages, but this
        # diagnostic is release plumbing: discard every chained detail so a
        # future adapter cannot leak a key, endpoint, or response body.
        label = provider if provider else 'External provider'
        raise SubtitleError(
            f'{label} LLM translation diagnostic failed') from None

    translated_bytes = translated.encode('utf-8')
    source_bytes = source.encode('utf-8')
    payload = {
        'schema': 1,
        'kind': 'llm_translation',
        'frozen': bool(getattr(sys, 'frozen', False)),
        'provider': provider,
        'model': model,
        'target_language': 'en',
        'cue_count': 1,
        'endpoint_sha256': endpoint_sha256,
        'source_characters': len(source),
        'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'translation_characters': len(translated),
        'translation_sha256': hashlib.sha256(
            translated_bytes).hexdigest(),
    }
    _atomic_write_text(
        destination,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
    return payload


def _atomic_write_text(destination: str, text: str) -> None:
    destination_dir = os.path.dirname(destination)
    os.makedirs(destination_dir, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(
        prefix=os.path.basename(destination) + '.',
        suffix='.tmp', dir=destination_dir)
    try:
        handle = os.fdopen(
            descriptor, 'w', encoding='utf-8', newline='\n')
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, destination)
        temp = ''
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temp:
            try:
                os.remove(temp)
            except FileNotFoundError:
                pass


def _stable_signature(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _asr_signature(profile: RecognitionProfile) -> str:
    """Identify every setting that can materially change an ASR sidecar."""
    cli_sha256 = WHISPER_RUNTIME_FILES['Release/whisper-cli.exe'][1]
    vad_helper_sha256 = WHISPER_RUNTIME_FILES[
        'Release/whisper-vad-speech-segments.exe'][1]
    if profile.engine == 'reazonspeech':
        fallback = RECOGNITION_PROFILES['balanced']
        return _stable_signature({
            'pipeline': REAZONSPEECH_PIPELINE_VERSION,
            'runtime': {
                'version': REAZONSPEECH_RUNTIME_VERSION,
                'commit': REAZONSPEECH_RUNTIME_COMMIT,
                'pack_version': REAZONSPEECH_PACK_VERSION,
                'pack_sha256': REAZONSPEECH_PACK_SHA256,
                'manifest_sha256': REAZONSPEECH_MANIFEST_SHA256,
                'executable_sha256': REAZONSPEECH_REQUIRED_FILES[
                    'runtime/sherpa-onnx-offline.exe'][1],
                'onnxruntime_sha256': REAZONSPEECH_REQUIRED_FILES[
                    'runtime/onnxruntime.dll'][1],
            },
            'model': {
                'key': profile.key,
                'name': profile.model_name,
                'revision': REAZONSPEECH_MODEL_REVISION,
                'size': profile.model_size,
                'sha256': profile.model_sha256,
            },
            'vad': {
                'runtime_version': WHISPER_VERSION,
                'runtime_archive_sha256': WHISPER_ARCHIVE_SHA256,
                'helper_sha256': vad_helper_sha256,
                'model_sha256': VAD_MODEL_SHA256,
                'threshold': VAD_THRESHOLD,
                'minimum_speech_ms': VAD_MIN_SPEECH_MS,
                'maximum_speech_seconds': VAD_MAX_SPEECH_SECONDS,
                'speech_padding_ms': VAD_SPEECH_PAD_MS,
                'sample_overlap_seconds': VAD_SAMPLES_OVERLAP_SECONDS,
                'merge_gap_seconds': VAD_MERGE_GAP_SECONDS,
            },
            'windows': {
                'speech_span_seconds':
                    RECOGNITION_WINDOW_SPEECH_SPAN_SECONDS,
                'padding_seconds':
                    RECOGNITION_WINDOW_PADDING_SECONDS,
                'maximum_seconds': RECOGNITION_WINDOW_MAX_SECONDS,
            },
            'decoder': {
                'language': 'ja',
                'method': 'greedy_search',
                'provider': 'cpu',
                'sample_rate': WHISPER_SAMPLE_RATE,
                'feature_dimension': 80,
                'dither': 0,
                'batch_size': REAZONSPEECH_BATCH_SIZE,
            },
            'cue_grouping': {
                'maximum_characters': REAZONSPEECH_CUE_MAX_CHARS,
                'maximum_seconds': REAZONSPEECH_CUE_MAX_SECONDS,
                'pause_seconds': REAZONSPEECH_CUE_PAUSE_SECONDS,
                'punctuation': REAZONSPEECH_CUE_PUNCTUATION,
            },
            'structural_fallback': {
                'pipeline': ASR_PIPELINE_VERSION,
                'key': fallback.key,
                'name': fallback.model_name,
                'size': fallback.model_size,
                'sha256': fallback.model_sha256,
                'cli_sha256': cli_sha256,
            },
        })
    return _stable_signature({
        'pipeline': ASR_PIPELINE_VERSION,
        'runtime': {
            'version': WHISPER_VERSION,
            'archive_sha256': WHISPER_ARCHIVE_SHA256,
            'cli_sha256': cli_sha256,
            'vad_helper_sha256': vad_helper_sha256,
        },
        'model': {
            'key': profile.key,
            'name': profile.model_name,
            'size': profile.model_size,
            'sha256': profile.model_sha256,
        },
        'vad': {
            'model_sha256': VAD_MODEL_SHA256,
            'threshold': VAD_THRESHOLD,
            'minimum_speech_ms': VAD_MIN_SPEECH_MS,
            'maximum_speech_seconds': VAD_MAX_SPEECH_SECONDS,
            'speech_padding_ms': VAD_SPEECH_PAD_MS,
            'sample_overlap_seconds': VAD_SAMPLES_OVERLAP_SECONDS,
            'merge_gap_seconds': VAD_MERGE_GAP_SECONDS,
        },
        'windows': {
            'speech_span_seconds': RECOGNITION_WINDOW_SPEECH_SPAN_SECONDS,
            'padding_seconds': RECOGNITION_WINDOW_PADDING_SECONDS,
            'maximum_seconds': RECOGNITION_WINDOW_MAX_SECONDS,
        },
        'decoder': {
            'language': 'ja',
            'translate': False,
            'beam_size': 5,
            'best_of': 5,
            'temperature_fallback': True,
            'temperature': 0.0,
            'temperature_increment': 0.2,
            'suppress_non_speech_tokens': False,
            'output': 'json',
            'batch_size': WHISPER_BATCH_SIZE,
        },
    })


def _media_identity(path: str) -> str:
    """Return a path-free, copy-stable sampled identity for downloaded media."""
    try:
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            metadata = os.fstat(handle.fileno())
            size = int(metadata.st_size)
            sample_size = 64 * 1024
            offsets = sorted({
                0,
                max(0, size // 2 - sample_size // 2),
                max(0, size - sample_size),
            })
            for offset in offsets:
                handle.seek(offset)
                chunk = handle.read(sample_size)
                digest.update(offset.to_bytes(8, 'big', signed=False))
                digest.update(len(chunk).to_bytes(4, 'big', signed=False))
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise SubtitleError('Downloaded video file was not found') from exc
    if (
            int(after.st_size) != size
            or int(after.st_mtime_ns) != int(metadata.st_mtime_ns)):
        raise SubtitleError('Downloaded video file changed during subtitle setup')
    return _stable_signature({
        'schema': 'sample-v1',
        'size': size,
        'sample_sha256': digest.hexdigest(),
    })


def _asr_source_identity(
        asr_signature: str, media_identity: str) -> str:
    return f'asr:{asr_signature}:{media_identity}'


def _translation_signature(profile) -> str:
    if profile is None:
        raise SubtitleError('Translation provider settings are unavailable')
    uses_api = bool(getattr(profile, 'uses_api', False))
    if uses_api:
        endpoint_sha256 = hashlib.sha256(
            _translation_api_endpoint(profile).encode('utf-8')).hexdigest()
        identity = {
            'pipeline': 'llm-subtitle-v1',
            'provider_sha256': hashlib.sha256(
                str(getattr(profile, 'provider', '')).encode(
                    'utf-8')).hexdigest(),
            'model_sha256': hashlib.sha256(
                str(getattr(profile, 'model', '')).encode(
                    'utf-8')).hexdigest(),
            'endpoint_sha256': endpoint_sha256,
            'memory_version': _translation_api_memory_version(profile),
        }
    else:
        identity = {
            'pipeline': 'local-subtitle-v1',
            'memory_version': _translation_memory_version(),
        }
    return _stable_signature(identity)


def _subtitle_provenance_path(video_path: str) -> str:
    return os.path.splitext(os.path.abspath(video_path))[0] + (
        '.uav-subtitles.json')


def _legacy_subtitle_provenance_path(path: str) -> str:
    suffix = '.uav-subtitles.json'
    if path.endswith(suffix):
        return path[:-len(suffix)] + '.jable-subtitles.json'
    return path + '.jable-subtitles.json'


def _empty_subtitle_provenance() -> dict:
    return {
        'schema': SUBTITLE_PROVENANCE_SCHEMA,
        'kind': SUBTITLE_PROVENANCE_KIND,
        'tracks': {},
    }


def _load_subtitle_provenance(path: str) -> dict:
    """Load bounded app metadata; malformed metadata never owns a subtitle."""
    source_path = path
    if not os.path.isfile(source_path):
        legacy_path = _legacy_subtitle_provenance_path(path)
        if os.path.isfile(legacy_path):
            source_path = legacy_path
    try:
        if (
                not os.path.isfile(source_path)
                or os.path.getsize(source_path) <= 0
                or os.path.getsize(source_path) > MAX_SUBTITLE_PROVENANCE_BYTES):
            return _empty_subtitle_provenance()
        with open(source_path, 'rb') as handle:
            raw = handle.read(MAX_SUBTITLE_PROVENANCE_BYTES + 1)
        if len(raw) > MAX_SUBTITLE_PROVENANCE_BYTES:
            return _empty_subtitle_provenance()
        payload = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, ValueError, TypeError):
        return _empty_subtitle_provenance()
    if (
            not isinstance(payload, dict)
            or payload.get('schema') != SUBTITLE_PROVENANCE_SCHEMA
            or payload.get('kind') not in (
                {SUBTITLE_PROVENANCE_KIND}
                | LEGACY_SUBTITLE_PROVENANCE_KINDS
            )
            or not isinstance(payload.get('tracks'), dict)):
        return _empty_subtitle_provenance()
    tracks = {
        language: dict(entry)
        for language, entry in payload['tracks'].items()
        if language in ('ja', 'en', 'zh-TW') and isinstance(entry, dict)
    }
    return {
        'schema': SUBTITLE_PROVENANCE_SCHEMA,
        'kind': SUBTITLE_PROVENANCE_KIND,
        'tracks': tracks,
    }


def _save_subtitle_provenance(path: str, payload: dict) -> None:
    tracks = payload.get('tracks') if isinstance(payload, dict) else None
    if not isinstance(tracks, dict):
        raise SubtitleError('Subtitle provenance is invalid')
    safe_payload = {
        'schema': SUBTITLE_PROVENANCE_SCHEMA,
        'kind': SUBTITLE_PROVENANCE_KIND,
        'tracks': {
            language: entry
            for language, entry in tracks.items()
            if language in ('ja', 'en', 'zh-TW')
            and isinstance(entry, dict)
        },
    }
    encoded = json.dumps(
        safe_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(',', ':'),
    ) + '\n'
    if len(encoded.encode('utf-8')) > MAX_SUBTITLE_PROVENANCE_BYTES:
        raise SubtitleError('Subtitle provenance is too large')
    _atomic_write_text(path, encoded)


def _valid_sha256(value) -> bool:
    return bool(re.fullmatch(r'[0-9a-f]{64}', str(value or '').lower()))


@dataclass(frozen=True)
class _SubtitleTrackState:
    status: str
    sha256: Optional[str] = None

    @property
    def current(self) -> bool:
        return self.status in ('manual', 'current')


def _subtitle_track_state(
        language: str, path: str, entry,
        asr_signature: str,
        media_identity: str,
        translation_signature: Optional[str] = None,
        source_identity: Optional[str] = None,
) -> _SubtitleTrackState:
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return _SubtitleTrackState('missing')
    except OSError:
        return _SubtitleTrackState('missing')
    try:
        actual_sha256 = _sha256(path)
    except OSError:
        # An unreadable existing sidecar cannot be proven app-owned. Preserve
        # it rather than risking replacement of a user-authored subtitle.
        return _SubtitleTrackState('manual')
    if (
            not isinstance(entry, dict)
            or entry.get('generator') not in MANAGED_SUBTITLE_GENERATORS
            or not _valid_sha256(entry.get('srt_sha256'))
            or actual_sha256.lower()
            != str(entry.get('srt_sha256')).lower()):
        # No trustworthy ownership record means the sidecar may be authored
        # or edited by the user. It is always preserved.
        return _SubtitleTrackState('manual', actual_sha256)
    if not _existing(path):
        # A malformed file whose bytes still match our manifest is safe to
        # regenerate; foreign or edited files already returned as manual.
        return _SubtitleTrackState('stale', actual_sha256)
    if language == 'ja':
        status = (
            'current'
            if (
                entry.get('asr_signature') == asr_signature
                and entry.get('media_identity') == media_identity
            )
            else 'stale')
        return _SubtitleTrackState(status, actual_sha256)

    recorded_source = entry.get('source_identity')
    if (
            not recorded_source
            and entry.get('asr_signature') == asr_signature
            and entry.get('media_identity') == media_identity):
        # Compatibility for the first provenance schema written during
        # prerelease testing.
        recorded_source = _asr_source_identity(
            asr_signature, media_identity)
    status = (
        'current'
        if (
            translation_signature
            and entry.get('translation_signature')
            == translation_signature
            and source_identity
            and recorded_source == source_identity
        )
        else 'stale'
    )
    return _SubtitleTrackState(status, actual_sha256)


def _record_asr_track(
        manifest: dict, language: str, path: str,
        asr_signature: str, media_identity: str,
) -> None:
    manifest['tracks'][language] = {
        'generator': SUBTITLE_GENERATOR,
        'srt_sha256': _sha256(path),
        'asr_signature': asr_signature,
        'media_identity': media_identity,
    }


def _record_derived_track(
        manifest: dict, language: str, path: str,
        asr_signature: str, media_identity: str,
        translation_signature: str,
        source_identity: str, source_sha256: str,
) -> None:
    manifest['tracks'][language] = {
        'generator': SUBTITLE_GENERATOR,
        'srt_sha256': _sha256(path),
        'asr_signature': asr_signature,
        'media_identity': media_identity,
        'translation_signature': translation_signature,
        'source_identity': source_identity,
        'source_sha256': source_sha256,
    }


def _existing(path: str) -> bool:
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        return bool(parse_srt(_read_srt_text(path)))
    except (OSError, SubtitleError):
        return False


@contextmanager
def _generation_slot(cancel_check: Optional[CancelCheck]):
    acquired = False
    try:
        while not acquired:
            _check_cancel(cancel_check)
            acquired = _generation_lock.acquire(timeout=0.1)
        yield
    finally:
        if acquired:
            _generation_lock.release()


def generate_subtitles(video_path: str, mode,
                       progress_callback: Optional[ProgressCallback] = None,
                       cancel_check: Optional[CancelCheck] = None,
                       source_subtitle_evidence=()) -> SubtitleResult:
    """Generate requested sidecar SRT files next to ``video_path``.

    Output names are ``.ja.srt``, ``.en.srt``, and ``.zh-TW.srt`` so media
    players can expose them as independently selectable subtitle tracks.
    """
    normalized = normalize_subtitle_mode(mode)
    requested = subtitle_languages(normalized)
    if not requested:
        return SubtitleResult((), ())
    video_path = os.path.abspath(video_path)
    if not os.path.isfile(video_path):
        raise SubtitleError('Downloaded video file was not found')
    paths = subtitle_paths(video_path)

    source_evidence = normalize_source_subtitle_evidence(
        source_subtitle_evidence)
    satisfied_by_source = (
        ('zh-TW',)
        if 'zh-TW' in requested and source_evidence
        else ()
    )
    source_satisfied_files = (
        (paths['zh-TW'],)
        if satisfied_by_source and _existing(paths['zh-TW'])
        else ()
    )
    if satisfied_by_source:
        requested = tuple(
            language for language in requested
            if language not in satisfied_by_source)
    if not requested:
        _notify(progress_callback, 'done', 100)
        return SubtitleResult(
            source_satisfied_files, (),
            satisfied_by_source=satisfied_by_source)

    profile = recognition_profile()
    asr_signature = _asr_signature(profile)
    media_identity = _media_identity(video_path)
    provenance_path = _subtitle_provenance_path(video_path)
    generated: list[str] = []
    with _generation_slot(cancel_check):
        _check_cancel(cancel_check)
        manifest = _load_subtitle_provenance(provenance_path)
        entries = manifest['tracks']

        ja_state = _subtitle_track_state(
            'ja', paths['ja'], entries.get('ja'),
            asr_signature, media_identity)
        ja_expected_identity = (
            'file:' + str(ja_state.sha256)
            if ja_state.status == 'manual'
            else _asr_source_identity(
                asr_signature, media_identity)
        )

        # Manual/edited derived tracks never require provider settings merely
        # to be reused. Missing or app-managed tracks do, because the selected
        # translation engine is part of freshness.
        derived_requested = [
            language for language in requested
            if language in ('en', 'zh-TW')
        ]
        derived_preliminary = {
            language: _subtitle_track_state(
                language, paths[language], entries.get(language),
                asr_signature, media_identity)
            for language in derived_requested
        }
        translation_profile = None
        translation_signature = None
        if any(
                state.status != 'manual'
                for state in derived_preliminary.values()):
            translation_profile = _selected_translation_profile()
            translation_signature = _translation_signature(
                translation_profile)
        uses_api = bool(
            translation_profile is not None
            and getattr(translation_profile, 'uses_api', False)
        )

        en_state = _subtitle_track_state(
            'en', paths['en'], entries.get('en'), asr_signature,
            media_identity, translation_signature, ja_expected_identity)
        if uses_api or ja_state.current:
            zh_expected_identity = ja_expected_identity
        elif en_state.current:
            zh_expected_identity = 'file:' + str(en_state.sha256)
        else:
            zh_expected_identity = _asr_source_identity(
                asr_signature, media_identity)
        zh_state = _subtitle_track_state(
            'zh-TW', paths['zh-TW'], entries.get('zh-TW'), asr_signature,
            media_identity, translation_signature, zh_expected_identity)
        states = {
            'ja': ja_state,
            'en': en_state,
            'zh-TW': zh_state,
        }
        missing = [
            language for language in requested
            if not states[language].current
        ]
        if not missing:
            return SubtitleResult(
                tuple(paths[language] for language in requested)
                + source_satisfied_files,
                (),
                satisfied_by_source=satisfied_by_source)

        # Once an app-managed file's content hash changes, permanently treat
        # it as user-owned the next time another track writes the manifest.
        for language, state in states.items():
            if (
                    state.status == 'manual'
                    and isinstance(entries.get(language), dict)
                    and entries[language].get('generator')
                    in MANAGED_SUBTITLE_GENERATORS):
                entries.pop(language, None)

        _notify(progress_callback, 'queued', None)
        existing_japanese = (
            paths['ja'] if ja_state.current else None)
        existing_english = (
            paths['en'] if en_state.current else None)
        need_japanese_source = bool(
            'ja' in missing
            or 'en' in missing
            or (
                'zh-TW' in missing
                and (uses_api or not existing_english)
            )
        )
        need_whisper = bool(
            need_japanese_source and not existing_japanese)
        exe = model = vad_model = None
        if need_whisper:
            with _recognition_profile_scope(profile):
                exe, model, vad_model = _prepare_runtime(
                    progress_callback, cancel_check)

        with tempfile.TemporaryDirectory(
                prefix='uav-subtitle-',
                dir=os.path.dirname(os.path.abspath(video_path))) as temp_dir:
            wav = os.path.join(temp_dir, 'audio.wav')
            log = os.path.join(temp_dir, 'process.log')
            if need_whisper:
                _notify(progress_callback, 'audio', None)
                _extract_audio(video_path, wav, log, cancel_check)

            japanese_source = existing_japanese
            japanese_source_identity = (
                ja_expected_identity if existing_japanese else None)
            if need_japanese_source and not japanese_source:
                _notify(progress_callback, 'transcribe_ja', None)
                japanese_source = _run_recognition(
                    profile, exe, model, vad_model, wav,
                    os.path.join(temp_dir, 'japanese'), log, cancel_check,
                    progress_callback=progress_callback)
                if not japanese_source:
                    stale_languages = {
                        language for language in requested
                        if states[language].status == 'stale'
                    }
                    if (
                            need_japanese_source
                            and ja_state.status == 'stale'):
                        stale_languages.add('ja')
                    provenance_changed = False
                    for language in stale_languages:
                        try:
                            os.remove(paths[language])
                        except FileNotFoundError:
                            pass
                        except OSError as exc:
                            raise SubtitleError(
                                'Obsolete subtitle file could not be removed'
                            ) from exc
                        entries.pop(language, None)
                        provenance_changed = True
                    if provenance_changed:
                        _save_subtitle_provenance(
                            provenance_path, manifest)
                    _notify(progress_callback, 'done', 100)
                    return SubtitleResult(
                        tuple(
                            paths[language]
                            for language in requested
                            if states[language].current
                        ) + source_satisfied_files,
                        tuple(generated),
                        no_speech=True,
                        satisfied_by_source=satisfied_by_source,
                    )
                japanese_source_identity = _asr_source_identity(
                    asr_signature, media_identity)

            if 'ja' in missing and japanese_source:
                _atomic_copy(japanese_source, paths['ja'])
                japanese_source = paths['ja']
                _record_asr_track(
                    manifest, 'ja', paths['ja'],
                    asr_signature, media_identity)
                _save_subtitle_provenance(
                    provenance_path, manifest)
                generated.append(paths['ja'])

            with _translation_profile_scope(translation_profile):
                if 'en' in missing:
                    if (
                            not japanese_source
                            or not japanese_source_identity
                            or not translation_signature):
                        raise SubtitleError(
                            'Japanese transcription is unavailable')
                    _notify(progress_callback, 'translate_en', None)
                    source_sha256 = _sha256(japanese_source)
                    try:
                        translate_srt(
                            japanese_source, paths['en'], 'en', 'translate_en',
                            progress_callback, cancel_check)
                        _record_derived_track(
                            manifest, 'en', paths['en'],
                            asr_signature, media_identity,
                            translation_signature,
                            japanese_source_identity, source_sha256)
                        _save_subtitle_provenance(
                            provenance_path, manifest)
                        generated.append(paths['en'])
                        existing_english = paths['en']
                    except SubtitleCancelled:
                        raise
                    except Exception:
                        raise

                if 'zh-TW' in missing:
                    english_source = (
                        paths['en']
                        if paths['en'] in generated
                        else existing_english
                    )
                    if uses_api:
                        # External providers translate Japanese directly to
                        # the target. Video and audio never leave the computer.
                        chinese_source = japanese_source
                        chinese_source_language = 'ja'
                        chinese_source_identity = japanese_source_identity
                    else:
                        # The local path prefers Japanese so reviewed exact
                        # phrases win, then pivots unknown cues through English.
                        chinese_source = japanese_source or english_source
                        chinese_source_language = (
                            'ja' if japanese_source else 'en')
                        chinese_source_identity = (
                            japanese_source_identity
                            if japanese_source
                            else (
                                'file:' + _sha256(english_source)
                                if english_source else None
                            )
                        )
                    if (
                            not chinese_source
                            or not chinese_source_identity
                            or not translation_signature):
                        raise SubtitleError(
                            'Japanese or English transcription is unavailable')
                    source_sha256 = _sha256(chinese_source)
                    try:
                        translate_srt_to_zh_tw(
                            chinese_source, paths['zh-TW'],
                            progress_callback, cancel_check,
                            source_language=chinese_source_language)
                        _record_derived_track(
                            manifest, 'zh-TW', paths['zh-TW'],
                            asr_signature, media_identity,
                            translation_signature,
                            chinese_source_identity, source_sha256)
                        _save_subtitle_provenance(
                            provenance_path, manifest)
                        generated.append(paths['zh-TW'])
                    except SubtitleCancelled:
                        raise
                    except Exception:
                        raise

        _notify(progress_callback, 'done', 100)
        return SubtitleResult(
            tuple(
                paths[language]
                for language in requested
                if _existing(paths[language])
            ) + source_satisfied_files,
            tuple(generated),
            satisfied_by_source=satisfied_by_source,
        )
