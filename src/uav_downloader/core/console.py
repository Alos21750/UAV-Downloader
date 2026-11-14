"""Bounded console relay for the desktop message panel; no disk persistence."""
from collections import deque
import threading


class ConsoleRelay:
    def __init__(self, stream=None):
        self.stream = stream
        self._lock = threading.Lock()
        self._lines = deque(maxlen=256)
        self._tail = ''

    def write(self, text):
        text = str(text)
        if self.stream is not None:
            try:
                self.stream.write(text)
            except (OSError, ValueError):
                pass
        with self._lock:
            parts = (self._tail + text).split('\n')
            self._tail = parts.pop()[-8192:]
            self._lines.extend(line[-8192:] for line in parts)
        return len(text)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except (OSError, ValueError):
                pass

    def drain(self):
        with self._lock:
            lines = list(self._lines)
            self._lines.clear()
        return '\n'.join(lines)

    def clear(self):
        with self._lock:
            self._lines.clear()
            self._tail = ''

    def isatty(self):
        return False
