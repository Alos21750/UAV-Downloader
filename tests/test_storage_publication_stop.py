"""Stopping during final hashing or metadata flush must not publish success."""
import json

import pytest

from uav_downloader.core import download_storage as storage
from uav_downloader.core.stop import DownloadStopped, StopContext, stop_scope


@pytest.mark.parametrize('boundary', ['before_hash', 'after_hash', 'metadata_flush'])
def test_stopped_output_publication_keeps_reserved_record(tmp_path, monkeypatch, boundary):
    store = storage.DownloadStorage(tmp_path, storage.task_identity(
        'https://jable.tv/videos/publish-stop/', 'publish-stop', '720'))
    context = StopContext()
    with store.session():
        store.bind_media(dict(kind='direct', url='controlled-source'))
        output = store.reserve_output(lambda attempt, owner: tmp_path / 'video.mp4')
        output.write_bytes(b'completed media bytes')
        real_digest, real_fsync = storage.file_digest, storage.os.fsync

        def digest(*args, **kwargs):
            if boundary == 'before_hash':
                pytest.fail('an already stopped publisher must not hash a large file')
            value = real_digest(*args, **kwargs)
            if boundary == 'after_hash':
                context.request()
            return value

        def fsync(fd):
            real_fsync(fd)
            context.request()

        monkeypatch.setattr(storage, 'file_digest', digest)
        if boundary == 'metadata_flush':
            monkeypatch.setattr(storage.os, 'fsync', fsync)
        if boundary == 'before_hash':
            context.request()
        with stop_scope(context), pytest.raises(DownloadStopped):
            store.publish_output()
        assert json.loads(store.output_record.read_text(encoding='utf-8'))['state'] == 'reserved'
        assert output.read_bytes() == b'completed media bytes'
        assert not list(store.output_record.parent.glob('*.tmp'))


def test_hashing_output_observes_stop_between_reads(tmp_path, monkeypatch):
    output = tmp_path / 'video.mp4'
    output.write_bytes(b'large-media' * 300000)
    context = StopContext()
    reads = []
    real_open = open

    class Reader:
        def __enter__(self):
            self.stream = real_open(output, 'rb')
            return self

        def read(self, length):
            reads.append(length)
            value = self.stream.read(length)
            context.request()
            return value

        def __exit__(self, *args):
            self.stream.close()

    monkeypatch.setattr(storage, 'open', lambda *a, **kw: Reader(), raising=False)
    with pytest.raises(DownloadStopped):
        storage.file_digest(output, stop_context=context)
    assert len(reads) == 1
