"""The Windows release build runs these offline checks before packaging."""
from pathlib import Path
import re

import pytest

from uav_downloader.metadata import GITHUB_REPOSITORY, VERSION


ROOT = Path(__file__).resolve().parents[1]
FILES = ("README.md", "README.en.md", "README.zh-CN.md", "README.ja.md")
ENTRY = re.compile(
    r"^### \[(v\d+\.\d+\.\d+)\]\(https://github\.com/"
    + re.escape(GITHUB_REPOSITORY)
    + r"/releases/tag/\1\) [^\n]+\n\n([^#\s][^\n]*)",
    re.MULTILINE,
)


def versions(filename):
    return [tag for tag, _ in ENTRY.findall((ROOT / filename).read_text(encoding="utf-8"))]


@pytest.mark.parametrize("filename", FILES)
def test_readme_has_current_and_synchronized_release_notes(filename):
    tags = versions(filename)
    assert tags[:1] == [f"v{VERSION}"], f"{filename}: add the current release summary first"
    assert tags == versions(FILES[0]), f"{filename}: incomplete or reordered translation"
    keys = [tuple(map(int, tag[1:].split("."))) for tag in tags]
    assert keys == sorted(set(keys), reverse=True)
    assert {f"v3.8.{patch}" for patch in range(7)} <= set(tags)
