"""Explicit Watcher storage locations for frontends and isolated integration runs."""
import json
import threading
from pathlib import Path

from uav_downloader.core.watcher import _atomic_write, _default_config, _normalize_loaded_config


class WatcherStore:
    def __init__(self, directory, on_config=None):
        self.directory = Path(directory)
        self._lock = threading.RLock()
        self._on_config = on_config

    def _read(self, name):
        try:
            value = json.loads((self.directory / name).read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError(f'Watcher {name} must contain a JSON object')
            return value
        except FileNotFoundError:
            return {}

    def _write(self, name, value):
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(str(self.directory / name), json.dumps(value, ensure_ascii=False, indent=2))

    def load_config(self):
        with self._lock:
            cfg = _normalize_loaded_config(self._read('config.json') or _default_config())
            if self._on_config:
                self._on_config(cfg)
            return cfg

    def update_config(self, patch, *, remove=()):
        with self._lock:
            cfg = self.load_config()
            cfg.update(patch)
            for key in remove:
                cfg.pop(key, None)
            cfg = _normalize_loaded_config(cfg)
            self._write('config.json', cfg)
            if self._on_config:
                self._on_config(cfg)
            return cfg

    def load_seen(self):
        with self._lock:
            return self._read('seen.json')

    def save_seen(self, seen):
        with self._lock:
            self._write('seen.json', seen)
