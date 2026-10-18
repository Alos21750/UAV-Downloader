import sys

import pytest

from uav_downloader.cli import headless as docker_cli


def test_docker_worker_limit_is_forwarded_to_each_video(monkeypatch, tmp_path):
    captured = []

    class _FakeSite:
        def is_url_vaildate(self):
            return True

        def start_download(self):
            return True

    def _create(url, dest, max_workers=None):
        captured.append((url, dest, max_workers))
        return _FakeSite()

    monkeypatch.setattr(
        sys, 'argv', ['docker_cli.py', 'https://jable.tv/videos/example/'])
    monkeypatch.setenv('DOWNLOAD_DIR', str(tmp_path))
    monkeypatch.setenv('MAX_WORKERS_PER_VIDEO', '3')
    monkeypatch.delenv('URLS', raising=False)
    monkeypatch.delenv('URL', raising=False)
    monkeypatch.setattr(docker_cli.M3U8Sites, 'CreateSite', _create)

    assert docker_cli.main() == 0
    assert captured == [
        ('https://jable.tv/videos/example/', str(tmp_path), 3),
    ]


def test_headless_help_is_real_cli_help(capsys):
    with pytest.raises(SystemExit) as caught:
        docker_cli.main(['--help'])

    assert caught.value.code == 0
    assert '--output' in capsys.readouterr().out


def test_headless_local_default_is_project_relative(monkeypatch, tmp_path):
    captured = []

    class _FakeSite:
        def is_url_vaildate(self):
            return True

        def start_download(self):
            return True

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('DOWNLOAD_DIR', raising=False)
    monkeypatch.delenv('URLS', raising=False)
    monkeypatch.delenv('URL', raising=False)
    monkeypatch.setattr(
        docker_cli.M3U8Sites,
        'CreateSite',
        lambda url, dest, max_workers=None: (
            captured.append((url, dest)) or _FakeSite()),
    )

    assert docker_cli.main(['https://jable.tv/videos/example/']) == 0
    assert captured == [(
        'https://jable.tv/videos/example/',
        str(tmp_path / 'download'),
    )]


def test_headless_false_download_result_is_failure(monkeypatch, tmp_path):
    class _FakeSite:
        def is_url_vaildate(self):
            return True

        def start_download(self):
            return False

    monkeypatch.setattr(
        docker_cli.M3U8Sites, 'CreateSite', lambda *_args, **_kwargs: _FakeSite())

    assert docker_cli.main([
        '--output', str(tmp_path), 'https://jable.tv/videos/example/',
    ]) == 1
