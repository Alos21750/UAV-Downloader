"""The published image must pass offline runtime gates, not just build."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def smoke_module():
    spec = importlib.util.spec_from_file_location('docker_runtime_smoke', ROOT / 'scripts/docker_runtime_smoke.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_smoke_has_bounded_local_http_and_private_headers():
    module = smoke_module()
    assert module.READY_TIMEOUT <= 90
    assert module.SYNTHETIC_URL.startswith('https://jable.tv/videos/')
    assert module.SYNTHETIC_URL.endswith('/')
    with pytest.raises(AssertionError, match='HTTP status'):
        module.expect_status((401, {}, b'not a secret'), 200)


def test_both_architectures_are_offline_smoked_before_publish():
    workflow = (ROOT / '.github/workflows/docker-publish.yml').read_text(encoding='utf-8')
    publish = workflow.index('name: Build & push (amd64 + arm64)')
    for arch in ('amd64', 'arm64'):
        load = workflow.index(f'name: Build runtime smoke image ({arch})')
        smoke = workflow.index(f'name: Offline container runtime smoke ({arch})')
        assert load < smoke < publish
        section = workflow[smoke:workflow.index('\n      - ', smoke)]
        assert '--network none' in section
        assert f'--platform linux/{arch}' in section
        assert '--help' in section
        assert '--entrypoint python' in section
        assert 'docker_runtime_smoke.py' in section
        assert 'readonly' in section
    assert "'scripts/docker_runtime_smoke.py'" in workflow
