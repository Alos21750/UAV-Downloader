from pathlib import Path

from scripts import build_reazonspeech_asr_pack as builder
from uav_downloader.subtitles import engine


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'windows-build.yml'

PACK_NAME = 'UAV_reazonspeech_asr_v1.zip'
PACK_SIZE = 186_186_197
PACK_SHA256 = (
    'cf55e5485e14715beee6e0b12ca2b0998ad73ec755513e80138fa5161693c700'
)
MANIFEST_SHA256 = (
    'dceb4ab39275828596eebe6148243ef12cd88a8e2c9a9bef7c9f69a1d61b652c'
)
TRANSLATION_PACK_NAME = 'UAV_local_translation_v1.zip'
TRANSLATION_PACK_SIZE = 147_330_565
TRANSLATION_PACK_SHA256 = (
    '1259a2abeb5026411da39c6f3dcd69ebd70bed654ac9cfaaee0c7373867c0bb4'
)


def test_on_demand_components_keep_immutable_pinned_identities():
    assert builder.REAZON_MODEL_CARD_URL == (
        'https://huggingface.co/reazon-research/reazonspeech-k2-v2/'
        'resolve/291488c8151be24d7da4bf7af26e533fad96e407/README.md'
    )
    assert builder.MODEL_FILES['README.md'] == (
        1_188,
        '7debad4c9430f3310ad6d119fce385787c1c19f3ebc0cabe685a48dbe72a4de0',
    )
    assert engine.REAZONSPEECH_PACK_NAME == PACK_NAME
    assert engine.REAZONSPEECH_PACK_SIZE == PACK_SIZE
    assert engine.REAZONSPEECH_PACK_SHA256 == PACK_SHA256
    assert engine.REAZONSPEECH_MANIFEST_SHA256 == MANIFEST_SHA256
    assert engine.REAZONSPEECH_PACK_RELEASE_TAG == 'v3.1.0'
    assert '/latest/' not in engine.REAZONSPEECH_PACK_URL
    assert engine.TRANSLATION_PACK_NAME == TRANSLATION_PACK_NAME
    assert engine.TRANSLATION_PACK_SIZE == TRANSLATION_PACK_SIZE
    assert engine.TRANSLATION_PACK_SHA256 == TRANSLATION_PACK_SHA256
    assert engine.TRANSLATION_PACK_RELEASE_TAG == 'v3.1.0'
    assert '/latest/' not in engine.TRANSLATION_PACK_URL


def test_windows_ci_attests_and_uploads_only_end_user_app_assets():
    workflow = WORKFLOW.read_text(encoding='utf-8')

    assert PACK_NAME not in workflow
    assert TRANSLATION_PACK_NAME not in workflow
    assert 'actions/attest@v4' in workflow
    assert 'compression-level: 0' in workflow
    assert 'UAV_SHA256SUMS.txt' in workflow
    for asset in (
        'UAV_Browser.exe',
        'UAV_Watcher.exe',
        'UAV_Watcher_portable.zip',
    ):
        assert f'dist/{asset}' in workflow
    for legacy_asset in (
        'ALOS_Browse.exe',
        'ALOS_Watch.exe',
        'ALOS_Watch_portable.zip',
        'ALOS_reazonspeech_asr_v1.zip',
        'JableTV_Modern.exe',
        'Jable_smalltool.exe',
        'Jable_smalltool_portable.zip',
        'Jable_reazonspeech_asr_v1.zip',
    ):
        assert legacy_asset not in workflow


def test_reazonspeech_notices_pin_sources_and_explain_mixed_licensing():
    notices = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    security = (ROOT / 'WINDOWS_SECURITY.md').read_text(encoding='utf-8')

    assert PACK_NAME in notices
    assert TRANSLATION_PACK_NAME in notices
    assert 'mixed-license' in notices
    assert builder.REAZON_MODEL_REVISION in notices
    assert builder.SHERPA_COMMIT in notices
    assert builder.ONNX_RUNTIME_COMMIT in notices
    assert 'licenses/Apache-2.0.txt' in notices
    assert 'licenses/ONNXRuntime-MIT.txt' in notices
    assert 'licenses/ONNXRuntime-ThirdPartyNotices.txt' in notices
    assert PACK_NAME in security
    assert f'gh attestation verify .\\{PACK_NAME}' in security
    assert TRANSLATION_PACK_NAME in security
    assert f'gh attestation verify .\\{TRANSLATION_PACK_NAME}' in security
