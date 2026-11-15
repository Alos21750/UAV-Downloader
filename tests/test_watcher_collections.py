"""Watcher collection jobs must not collide with individual episode history."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from uav_downloader.apps import watch
from uav_downloader.apps.watch_categories import find_target


def episodes(series='123', numbers=(1, 2, 3)):
    return [dict(url=f'https://huangguoai.com/video/{series}/' +
                 (f'ep-{n}/' if n > 1 else ''), title=f'Drama - EP {n}',
                 episode=n, series_id=series, date='2026-11-14') for n in numbers]


@pytest.fixture
def scan(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'save_seen', lambda _seen: None)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    logs, calls = [], []
    worker = watch.SmallToolWorker(logs.append)
    worker._download_one = lambda video, dest: calls.append((video, dest)) or 'ok'
    worker._fetch_page_for_site = lambda _site, url: (
        [] if url.rstrip('/').endswith('/2') else episodes(numbers=(3, 1, 2)))
    target = find_target('Huangguo', 'category:ai-duanju')
    cfg = dict(output_folder=str(tmp_path), baseline_date='2026-01-01',
               huangguo_collection=True, first_run_done=False,
               selected_targets=[{'site': 'Huangguo', 'id': target['id']}])
    return worker, cfg, calls, logs


def test_enabled_groups_numeric_episodes_into_one_job(scan):
    worker, cfg, calls, _ = scan
    assert worker._scan_and_download(cfg)
    assert len(calls) == 1
    video = calls[0][0]
    assert video['_huangguo_collection'] is True
    assert [ep['episode'] for ep in video['_collection_episodes']] == [1, 2, 3]


def test_old_episode_history_does_not_suppress_collection(scan):
    worker, cfg, calls, _ = scan
    worker._seen = {ep['url']: {'skipped': False, 'title': ep['title']}
                    for ep in episodes()}
    original = dict(worker._seen)
    assert worker._scan_and_download(cfg)
    assert len(calls) == 1
    assert worker._seen == original


def test_older_collection_cannot_overwrite_successful_episode_history(scan):
    worker, cfg, calls, _ = scan
    cfg['baseline_date'] = '2026-11-15'
    worker._seen = {episodes()[0]['url']: {'skipped': False, 'title': 'Original EP1'}}
    original = dict(worker._seen)
    assert worker._scan_and_download(cfg)
    assert not calls
    assert worker._seen == original


def test_checkbox_persists_setting_and_requests_new_scan(monkeypatch):
    app = watch.SmallToolApp.__new__(watch.SmallToolApp)
    app._cfg = {'first_run_done': True}
    app._huangguo_collection_var = SimpleNamespace(get=lambda: True)
    saved = []
    monkeypatch.setattr(watch, 'update_config', lambda patch: saved.append(patch))
    app._on_huangguo_collection_change()
    assert app._cfg == {'huangguo_collection': True, 'first_run_done': False}
    assert saved == [app._cfg]


@pytest.mark.parametrize('enabled', [False, None, 'false', 'true', 1])
def test_non_boolean_setting_keeps_individual_episode_mode(scan, enabled):
    worker, cfg, calls, _ = scan
    cfg['huangguo_collection'] = enabled
    assert worker._scan_and_download(cfg)
    assert len(calls) == 3
    assert not any(video.get('_huangguo_collection') for video, _dest in calls)


def test_daily_collections_scan_past_completed_first_page(scan):
    worker, cfg, calls, _ = scan
    cfg['first_run_done'] = True
    worker._seen = {ep['url']: {'skipped': False} for ep in episodes()}
    def fetch(_site, url):
        if url.rstrip('/').endswith('/3'):
            return []
        return episodes('124' if url.rstrip('/').endswith('/2') else '123')
    worker._fetch_page_for_site = fetch
    assert worker._scan_and_download(cfg)
    assert [video['series_id'] for video, _dest in calls] == ['123', '124']


def test_series_repeated_across_categories_runs_once(scan):
    worker, cfg, calls, _ = scan
    cfg['selected_targets'].append({'site': 'Huangguo', 'id': 'category:ai-manju'})
    assert worker._scan_and_download(cfg)
    assert len(calls) == 1


def test_collection_failure_keeps_scan_pending(scan):
    worker, cfg, _calls, _ = scan
    worker._download_one = lambda *_args: 'collection_failed'
    assert worker._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is False


def test_cancel_after_last_job_does_not_complete_scan(scan):
    worker, cfg, _calls, _ = scan
    worker._download_one = lambda *_args: worker._stop.set()
    assert worker._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is False


def test_collection_obeys_latest_site_folder_but_not_variant_subfolder(scan, tmp_path):
    worker, cfg, calls, _ = scan
    cfg['version_preference'] = 'all'
    latest = dict(cfg, site_settings={'Huangguo': {'output_folder': str(tmp_path / 'new')}})
    assert worker._scan_and_download(cfg, config_loader=lambda: latest)
    assert len(calls) == 1
    assert calls[0][1] == str(tmp_path / 'new')


def test_collection_setting_default_and_normalization():
    assert watch._default_config()['huangguo_collection'] is False
    for value in (None, 'true', 'false', 1, {}, []):
        assert watch._normalize_loaded_config({'huangguo_collection': value})['huangguo_collection'] is False
    assert watch._normalize_loaded_config({'huangguo_collection': True})['huangguo_collection'] is True


@pytest.fixture
def collection_job(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    logs = []
    worker = watch.SmallToolWorker(logs.append)
    video = watch.group_collection_rows(episodes())[0]
    video['_collection_resolution'] = '720'
    monkeypatch.setattr(watch.SITES['Huangguo']['browser'], 'fetch_episodes',
                        lambda *_args, **_kwargs: episodes())
    from uav_downloader.sites import base
    monkeypatch.setattr(base, 'locate_ffmpeg', lambda: 'reviewed-ffmpeg')
    output = tmp_path / 'Collection.mp4'
    from uav_downloader.subtitles import engine
    monkeypatch.setattr(engine, 'invalidate_stale_managed_subtitles', lambda *_args, **_kwargs: (), raising=False)
    def build(*_args, **kwargs):
        kwargs['on_ready'](output)
        return output
    monkeypatch.setattr(watch, 'build_collection', build)
    return worker, video, output, logs


def test_collection_subtitles_use_merged_timeline_only(collection_job, monkeypatch):
    worker, video, output, _logs = collection_job
    calls = []
    monkeypatch.setattr(watch, 'generate_subtitles', lambda path, mode, **kw:
                        calls.append((path, mode)) or SimpleNamespace(
                            files=['merged.srt'], no_speech=False, satisfied_by_source=False))
    assert worker._download_one(video, str(output.parent), 'zh') == 'ok'
    assert calls == [(str(output), 'zh')]
    assert worker._seen == {}


def test_empty_collection_subtitles_stay_pending(collection_job, monkeypatch):
    worker, video, output, _logs = collection_job
    monkeypatch.setattr(watch, 'generate_subtitles', lambda *_args, **_kwargs:
                        SimpleNamespace(files=[], no_speech=False, satisfied_by_source=False))
    assert worker._download_one(video, str(output.parent), 'zh') == 'subtitle_failed'
    assert worker._seen == {}


@pytest.mark.parametrize('fresh', [episodes(numbers=(1, 2)), episodes('999'), []])
def test_refresh_rejects_lost_or_wrong_membership(collection_job, monkeypatch, fresh):
    worker, video, output, _logs = collection_job
    monkeypatch.setattr(watch.SITES['Huangguo']['browser'], 'fetch_episodes',
                        lambda *_args, **_kwargs: fresh)
    monkeypatch.setattr(watch, 'build_collection', lambda *_args, **_kwargs:
                        pytest.fail('incomplete membership must not reach merge'))
    assert worker._download_one(video, str(output.parent), 'none') == 'collection_failed'


def test_refresh_accepts_new_episode_and_bypasses_short_ttl(collection_job, monkeypatch):
    worker, video, output, _logs = collection_job
    requests, builds = [], []
    def fetch(_url, **kwargs):
        requests.append(kwargs)
        return episodes(numbers=(1, 2, 3, 4))
    monkeypatch.setattr(watch.SITES['Huangguo']['browser'], 'fetch_episodes', fetch)
    monkeypatch.setattr(watch, 'build_collection', lambda rows, *_args, **kwargs:
                        builds.append((rows, kwargs)) or output)
    assert worker._download_one(video, str(output.parent), 'none') == 'ok'
    assert requests[0]['revision'].isdigit()
    assert len(builds[0][0]) == 4
    assert builds[0][1]['resolution'] == '720'


@pytest.mark.parametrize('result', [None, 'source_failed', 'download_incomplete', 'blocked'])
def test_unsuccessful_episode_cannot_become_cache_receipt(collection_job, monkeypatch, result):
    worker, video, output, _logs = collection_job
    monkeypatch.setattr(worker, '_download_one', lambda *_args, **_kwargs: result)
    def build(rows, destination, **kwargs):
        kwargs['download_episode'](rows[0], Path(destination) / 'attempt')
        pytest.fail('failed episode was accepted')
    monkeypatch.setattr(watch, 'build_collection', build)
    assert worker._download_huangguo_collection(video, str(output.parent), 'none') == 'collection_failed'


def test_episode_download_freezes_quality_and_disables_individual_subtitles(collection_job, monkeypatch):
    worker, video, output, _logs = collection_job
    calls = []
    def download(part, dest, subtitle_mode=None):
        calls.append((part, dest, subtitle_mode))
        part['_downloaded_path'] = str(Path(dest) / 'ep.mp4')
        return 'episode_ready'
    monkeypatch.setattr(worker, '_download_one', download)
    def build(rows, destination, **kwargs):
        assert kwargs['download_episode'](rows[0], Path(destination) / 'attempt').name == 'ep.mp4'
        return output
    monkeypatch.setattr(watch, 'build_collection', build)
    assert worker._download_huangguo_collection(video, str(output.parent), 'none') == 'ok'
    assert calls[0][0]['_collection_resolution'] == '720'
    assert calls[0][2] == 'none'


def test_collection_storage_failure_halts_worker(collection_job, monkeypatch):
    worker, video, output, _logs = collection_job
    from uav_downloader.core.storage import StorageWriteError
    def build(*_args, **_kwargs):
        raise StorageWriteError(output, OSError(28, 'disk full'))
    monkeypatch.setattr(watch, 'build_collection', build)
    assert worker._download_one(video, str(output.parent), 'none') == 'storage_failed'
    assert worker._stop.is_set()


def test_nested_episode_storage_failure_is_not_reported_as_cancellation(collection_job, monkeypatch):
    worker, video, output, _logs = collection_job
    def download(*_args, **_kwargs):
        worker._stop.set()
        return 'storage_failed'
    monkeypatch.setattr(worker, '_download_one', download)
    def build(rows, destination, **kwargs):
        kwargs['download_episode'](rows[0], Path(destination) / 'attempt')
    monkeypatch.setattr(watch, 'build_collection', build)
    assert worker._download_huangguo_collection(video, str(output.parent), 'none') == 'storage_failed'
