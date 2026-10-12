"""Identify destination I/O failures separately from retryable transport errors."""

import errno


class StorageWriteError(OSError):
    """Destination storage failed; stop writes and require an explicit retry."""

    def __init__(self, path, cause):
        self.path = str(path)
        self.cause = cause
        super().__init__(
            getattr(cause, 'errno', errno.EIO),
            f'Destination storage I/O failed: {cause}. Downloads stopped; '
            'check free space and device health before retrying.', self.path)


class _StorageFile:
    def __init__(self, path, mode, *args, **kwargs):
        self.path = path
        try:
            self.file = open(path, mode, *args, **kwargs)
        except OSError as exc:
            raise StorageWriteError(path, exc) from exc

    def _io(self, method, *args, **kwargs):
        try:
            return getattr(self.file, method)(*args, **kwargs)
        except OSError as exc:
            raise StorageWriteError(self.path, exc) from exc

    def write(self, data):
        written = self._io('write', data)
        if written != len(data):
            raise StorageWriteError(self.path, OSError(errno.EIO, 'short write'))
        return written

    def read(self, *args):
        return self._io('read', *args)

    def seek(self, *args):
        return self._io('seek', *args)

    def truncate(self, *args):
        return self._io('truncate', *args)

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self._io('close')
        except StorageWriteError:
            if kind is None:
                raise


def storage_open(path, mode, *args, **kwargs):
    return _StorageFile(path, mode, *args, **kwargs)
