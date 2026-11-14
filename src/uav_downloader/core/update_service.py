"""Update network and disk work isolated from the desktop event loop."""
from pathlib import Path
import tempfile
import threading

from uav_downloader import metadata
from uav_downloader.core import updater
from uav_downloader.core.latest_job import LatestJob


class UpdateService:
    def __init__(self, channel=None, checker=updater.check_latest, downloader=updater.download_asset):
        self.channel, self.checker, self.downloader = channel, checker, downloader
        self._worker = LatestJob()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._progress = (0, 0)

    def check(self):
        return self._worker.submit(self.checker)

    def can_apply(self):
        return (updater.is_frozen() and self.channel in {'browse', 'watch'} and
                updater.current_exe_name() in metadata.release_asset_candidates(self.channel))

    def progress(self):
        with self._lock:
            return self._progress

    def download(self, release):
        if not self.can_apply():
            raise ValueError('Automatic updates are unavailable for this build')
        url = updater.find_asset_url(release, self.channel)
        if not url:
            raise ValueError('No compatible application asset was found')
        with self._lock:
            self._progress = (0, 0)
        def operation():
            folder = Path(tempfile.mkdtemp(prefix='uav-update-'))
            target = folder / updater.current_exe_name()
            def progress(done, total):
                with self._lock:
                    self._progress = done, total
            if not self.downloader(url, str(target), progress_cb=progress, cancel_check=self._stop.is_set):
                try:
                    folder.rmdir()
                except OSError:
                    pass
                raise RuntimeError('Application update could not be downloaded')
            if self._stop.is_set():
                target.unlink(missing_ok=True)
                folder.rmdir()
                raise InterruptedError('Application update was cancelled')
            return str(target)
        return self._worker.submit(operation)

    def close(self):
        self._stop.set()
        self._worker.close()
