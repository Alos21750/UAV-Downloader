# coding: utf-8
"""Segment temp-file naming (collision-free by playlist index) and AES IV derivation
(implicit IV = media-sequence base + segment index; explicit IV overrides)."""
import sys
import shutil
import types
from pathlib import Path


def _stub(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


_stub('cloudscraper')
_stub('customtkinter')

from Crypto.Cipher import AES
from uav_downloader.sites.base import M3U8Crawler


def test_seg_savename_unique_by_index():
    fake = types.SimpleNamespace(_temp_folder='/tmp/x')
    n0 = M3U8Crawler._seg_savename(fake, 0)
    n1 = M3U8Crawler._seg_savename(fake, 1)
    assert n0 != n1
    assert n0.endswith('000000.mp4')
    assert n1.endswith('000001.mp4')


def test_seg_savename_no_collision_on_shared_basename():
    # The bug: seg.ts?n=1 and seg.ts?n=2 (and same-named files in different dirs) share a
    # basename and collided into one temp file — silent corruption. Index naming can't.
    fake = types.SimpleNamespace(_temp_folder='/tmp/x')
    urls = ['https://a/seg.ts?n=1', 'https://a/seg.ts?n=2', 'https://b/seg.ts']
    names = [M3U8Crawler._seg_savename(fake, i) for i in range(len(urls))]
    assert len(set(names)) == len(urls)   # all distinct despite identical basenames


def test_make_cipher_implicit_iv_uses_media_sequence():
    key = b'0' * 16
    fake = types.SimpleNamespace(_key_content=key, _key_iv=None, _media_sequence=100)
    plaintext = b'A' * 16
    iv = (5 + 100).to_bytes(16, 'big')      # segment index 5 + EXT-X-MEDIA-SEQUENCE 100
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(plaintext)
    cipher = M3U8Crawler._make_cipher(fake, 5)
    assert cipher.decrypt(enc) == plaintext


def test_make_cipher_explicit_iv_overrides_index():
    key = b'0' * 16
    fake = types.SimpleNamespace(_key_content=key,
                                 _key_iv='0x000102030405060708090a0b0c0d0e0f',
                                 _media_sequence=999)
    iv = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
    plaintext = b'B' * 16
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(plaintext)
    cipher = M3U8Crawler._make_cipher(fake, 7)   # explicit IV ignores seq/media_sequence
    assert cipher.decrypt(enc) == plaintext


def test_make_cipher_none_without_key():
    fake = types.SimpleNamespace(_key_content=None, _key_iv=None, _media_sequence=0)
    assert M3U8Crawler._make_cipher(fake, 0) is None


def test_crawler_source_subtitle_evidence_is_bound_to_its_source_site():
    crawler = object.__new__(M3U8Crawler)
    crawler._url = 'https://jable.tv/videos/ipzz-905/'
    crawler._source_subtitle_evidence = ()

    crawler.add_source_subtitle_evidence(
        ('missav-category-chinese-subtitle',))
    assert crawler.source_subtitle_evidence() == ()

    crawler.add_source_subtitle_evidence(
        ('jable-category-chinese-subtitle',))
    assert crawler.source_subtitle_evidence() == (
        'jable-category-chinese-subtitle',)


def test_remux_workspace_uses_the_download_destination_volume(
        monkeypatch, tmp_path):
    destination = tmp_path / 'downloads'
    segments = destination / 'segments'
    segments.mkdir(parents=True)
    (segments / '000000.mp4').write_bytes(b'segment')

    crawler = object.__new__(M3U8Crawler)
    crawler._dest_folder = str(destination)
    crawler._temp_folder = str(segments)
    crawler._targetName = 'example'
    crawler._tsList = ['https://cdn.example.test/000000.ts']
    crawler._cancel_job = False
    crawler._deleteMp4Chunks = lambda: None

    created = {}

    def fake_mkdtemp(prefix, dir=None):
        created['dir'] = dir
        root = Path(dir) if dir else tmp_path / 'system-temp'
        workspace = root / f'{prefix}test'
        workspace.mkdir(parents=True)
        return str(workspace)

    def fake_remux(merged, output, _workdir):
        shutil.copyfile(merged, output)
        return True

    monkeypatch.setattr(
        'uav_downloader.sites.base.tempfile.mkdtemp', fake_mkdtemp)
    crawler._remux_to_mp4 = fake_remux

    crawler._mergeMp4Chunks()

    assert Path(created['dir']).resolve() == destination.resolve()
    assert (destination / 'example.mp4').read_bytes() == b'segment'
