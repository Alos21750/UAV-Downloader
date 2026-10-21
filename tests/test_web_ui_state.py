"""Execute the real UI JS: polling must preserve local reading/focus state."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from uav_downloader.web.ui import JS


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for the offline JS DOM harness')
@pytest.mark.parametrize('scenario', [
    'logs', 'button-focus', 'summary-focus', 'outside-focus', 'reorder', 'changed-action',
])
def test_actual_queue_render_preserves_reader_state_without_mutation(scenario):
    harness = Path(__file__).with_name('web_ui_dom_harness.cjs')
    result = subprocess.run(
        [shutil.which('node'), str(harness)],
        input=json.dumps({'source': JS, 'scenario': scenario}),
        text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'PASS ' + scenario
