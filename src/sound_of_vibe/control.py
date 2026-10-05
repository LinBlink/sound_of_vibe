"""Live controls shared by the tray, local settings page and audio workers."""

import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time

from .hook_config import atomic_write
from .kimi_hooks import HookQueue, launch_worker, load_settings, state_directory

SETTINGS_LOCK = RLock()


class ServiceController:
    def __init__(self, root=None):
        self.root = Path(root or state_directory())

    def directories(self, source='both'):
        if source not in {'both', 'kimi', 'codex'}:
            raise ValueError('Invalid source')
        return [(name, self.root / 'Codex' if name == 'codex' else self.root)
                for name in ('kimi', 'codex') if source == 'both' or source == name]

    def status(self):
        result = {}
        for name, directory in self.directories():
            settings = load_settings(directory)
            installed = (directory / 'settings.json').exists()
            pid = None
            database = directory / 'events.sqlite3'
            if database.exists():
                try:
                    with closing(sqlite3.connect(database, timeout=.2)) as connection:
                        row = connection.execute('SELECT pid FROM lease WHERE expires > ?', (time.time(),)).fetchone()
                    pid = row[0] if row else None
                except sqlite3.Error:
                    pass
            result[name] = dict(installed=installed, enabled=installed and settings['enabled'], worker_pid=pid,
                                rate=settings['rate'], volume=settings['volume'], muted=settings['muted'])
        return result

    def patch(self, data):
        source = data.get('source', 'both')
        values = {key: value for key, value in data.items() if key != 'source'}
        if not values or values.keys() - {'rate', 'max_rate', 'adaptive_rate', 'volume', 'muted'}:
            raise ValueError('Invalid live controls')
        for key in ('adaptive_rate', 'muted'):
            if key in values and type(values[key]) is not bool:
                raise ValueError(f'Invalid {key}')
        for key in ('volume', 'max_rate'):
            minimum = 0 if key == 'volume' else -50
            if key in values and (type(values[key]) is not int or not minimum <= values[key] <= 100):
                raise ValueError(f'{key} must be an integer from {minimum} to 100')
        if 'rate' in values and (not isinstance(values['rate'], str) or
                not re.fullmatch(r'[+-]\d{1,3}%', values['rate']) or not -50 <= int(values['rate'][:-1]) <= 100):
            raise ValueError('Rate must be between -50% and +100%')
        with SETTINGS_LOCK:
            updates = []
            for _, directory in self.directories(source):
                path = directory / 'settings.json'
                settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'enabled': False}
                settings.update(values)
                # A higher base rate must not leave an inconsistent catch-up cap.
                settings['max_rate'] = max(settings.get('max_rate', 100), int(settings.get('rate', '+0%')[:-1]))
                updates.append((path, settings))
            for path, settings in updates:
                atomic_write(path, json.dumps(settings, ensure_ascii=False, indent=2) + '\n')
        return self.status()

    def set_enabled(self, enabled, source='both'):
        if type(enabled) is not bool:
            raise ValueError('Invalid service state')
        changed = []
        with SETTINGS_LOCK:
            for _, directory in self.directories(source):
                path = directory / 'settings.json'
                if not path.exists():
                    continue
                settings = json.loads(path.read_text(encoding='utf-8'))
                settings['enabled'] = enabled
                atomic_write(path, json.dumps(settings, ensure_ascii=False, indent=2) + '\n')
                if not enabled:
                    # Discard obsolete queued hooks so starting cannot replay them.
                    queue = HookQueue(directory)
                    with queue.connect() as connection:
                        connection.execute('DELETE FROM events')
                        connection.execute('DELETE FROM lease')
                changed.append(directory)
        if enabled and not changed:
            raise ValueError('Install CLI hooks first: sound-of-vibe hooks install')
        if enabled:
            for directory in changed:
                queue = HookQueue(directory)
                token = queue.reserve()
                if token:
                    launch_worker(queue, token)
        return self.status()
