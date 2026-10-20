"""Identify destination I/O failures separately from retryable transport errors."""

import errno
import re


def storage_failure_message(message):
    return bool(re.search(
        r'no space left|disk (?:is )?full|input/output error|i/o error|'
        r'device (?:is )?not ready|no such device|device not connected|'
        r'error (?:writing|closing)|failed to (?:write|flush)',
        str(message), re.IGNORECASE))


class StorageWriteError(OSError):
    """Destination storage failed; stop writes and require an explicit retry."""

    def __init__(self, path, cause, operation='access'):
        self.path = str(path)
        self.cause = cause
        self.operation = operation
        code = getattr(cause, 'errno', None) or errno.EIO
        if code == errno.ENOENT:
            hint = ('The path is missing or unavailable; check the drive and '
                    'choose an existing writable download folder')
        elif code == errno.ENOSPC:
            hint = 'Free space on the selected download drive before retrying'
        elif code in (errno.EACCES, errno.EPERM):
            hint = ('Check folder permission and whether another application '
                    'has locked the file')
        elif code == errno.ENOTDIR:
            hint = 'A path component is not a directory; select a download folder'
        elif code == errno.EINVAL:
            hint = 'Check the destination path and drive; try a shorter writable path'
        else:
            hint = 'Check free space, drive availability and device health before retrying'
        detail = getattr(cause, 'strerror', None) or str(cause)
        super().__init__(
            code,
            f"Destination storage I/O failed [{operation}] at '{self.path}': "
            f'{hint}. Details: {detail}. Downloads stopped.', self.path)


class _StorageFile:
    def __init__(self, path, mode, *args, **kwargs):
        self.path = path
        try:
            self.file = open(path, mode, *args, **kwargs)
        except OSError as exc:
            operation = ('open for writing' if any(flag in mode for flag in 'wax+')
                         else 'open for reading')
            raise StorageWriteError(path, exc, operation=operation) from exc

    def _io(self, method, *args, **kwargs):
        try:
            return getattr(self.file, method)(*args, **kwargs)
        except OSError as exc:
            raise StorageWriteError(self.path, exc, operation=method) from exc

    def write(self, data):
        written = self._io('write', data)
        if written != len(data):
            raise StorageWriteError(
                self.path, OSError(errno.EIO, 'short write'), operation='write')
        return written

    def read(self, *args):
        return self._io('read', *args)

    def seek(self, *args):
        return self._io('seek', *args)

    def truncate(self, *args):
        return self._io('truncate', *args)

    def flush(self):
        return self._io('flush')

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
