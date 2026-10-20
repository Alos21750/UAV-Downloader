import csv
import json
from types import SimpleNamespace

import pytest

from uav_downloader.apps.downloads import export_items


@pytest.mark.parametrize('name', ['=1+1', '+SUM(1,1)', '-2+1', '@SUM(1,1)', '\t=1+1'])
def test_csv_export_neutralizes_spreadsheet_formulas(tmp_path, name):
    path = tmp_path / 'export.csv'
    export_items([SimpleNamespace(name=name, url='https://jable.tv/videos/abc-123/')], str(path))
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]['name'] == "'" + name


def test_json_export_retains_literal_text(tmp_path):
    path = tmp_path / 'export.json'
    export_items([SimpleNamespace(name='=1+1', url='https://jable.tv/videos/abc-123/')], str(path))
    assert json.loads(path.read_text(encoding='utf-8'))['items'][0]['name'] == '=1+1'
