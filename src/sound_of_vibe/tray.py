"""Windows notification-area application for local narration controls."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
import webbrowser

from .control import ServiceController
from .hook_config import atomic_write
from .kimi_hooks import state_directory
from .voice_picker import create_server


def tray_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    python = Path(sys.executable)
    return [str(python.with_name('pythonw.exe')), '-m', 'sound_of_vibe.tray']


def startup_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            winreg.QueryValueEx(key, 'SoundOfVibe')
        return True
    except FileNotFoundError:
        return False


def set_startup(enabled):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, 'SoundOfVibe', 0, winreg.REG_SZ, subprocess.list2cmdline(tray_command()))
        else:
            try:
                winreg.DeleteValue(key, 'SoundOfVibe')
            except FileNotFoundError:
                pass


class SingleInstance:
    def __init__(self, root):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        name = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:20]
        self.handle = self.kernel.CreateMutexW(None, False, 'Local\\SoundOfVibeTray-' + name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.acquired = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def icon_image(enabled=True, muted=False):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = '#6255c7' if enabled and not muted else '#7b8190'
    draw.rounded_rectangle((3, 3, 61, 61), radius=15, fill=color)
    for x, height in ((17, 14), (27, 30), (37, 40), (47, 22)):
        draw.rounded_rectangle((x - 3, 32 - height // 2, x + 3, 32 + height // 2), radius=3, fill='white')
    return image


class TrayApp:
    def __init__(self, root=None):
        self.controller = ServiceController(root)
        self.root = self.controller.root
        self.root.mkdir(parents=True, exist_ok=True)
        self.done = Event()
        self.server = create_server(self.root, controller=self.controller, on_exit=self.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}/'
        self.state_path = self.root / 'tray.json'
        self.icon = None

    def preferences(self):
        status = self.controller.status()
        return next((v for v in status.values() if v['installed']), next(iter(status.values())))

    def publish(self, ready):
        atomic_write(self.state_path, json.dumps(dict(pid=os.getpid(), url=self.url, ready=ready), indent=2) + '\n')

    def refresh(self):
        status = self.controller.status()
        enabled = any(value['enabled'] for value in status.values())
        preferences = self.preferences()
        self.icon.title = f"Sound of Vibe · {'运行 / Running' if enabled else '停止 / Stopped'} · {preferences['rate']} · {preferences['volume']}%"
        self.icon.icon = icon_image(enabled, preferences['muted'])
        self.icon.update_menu()

    def action(self, callback):
        def invoke(icon=None, item=None):
            try:
                callback()
                if not self.done.is_set():
                    self.refresh()
            except Exception as error:
                self.icon.notify(str(error), 'Sound of Vibe')
        return invoke

    def menu(self):
        import pystray
        item = pystray.MenuItem
        def rate_item(rate):
            return item(f'{rate:+d}%', self.action(lambda: self.controller.patch({'rate': f'{rate:+d}%'})),
                        checked=lambda _: self.preferences()['rate'] == f'{rate:+d}%', radio=True)
        def volume_item(volume):
            return item(f'{volume}%', self.action(lambda: self.controller.patch({'volume': volume})),
                        checked=lambda _: self.preferences()['volume'] == volume, radio=True)
        return pystray.Menu(
            item('控制面板 / Control panel', self.action(lambda: webbrowser.open(self.url)), default=True),
            item('启动播报服务 / Start narration', self.action(lambda: self.controller.set_enabled(True))),
            item('停止播报服务 / Stop narration', self.action(lambda: self.controller.set_enabled(False))),
            item('静音 / Mute', self.action(lambda: self.controller.patch({'muted': not self.preferences()['muted']})),
                 checked=lambda _: self.preferences()['muted']),
            item('语速 / Speech rate', pystray.Menu(*(rate_item(rate) for rate in (-50, -25, 0, 25, 50, 75, 100)))),
            item('音量 / Volume', pystray.Menu(*(volume_item(volume) for volume in (0, 25, 50, 75, 100)))),
            item('登录时启动 / Run at sign-in', self.action(lambda: set_startup(not startup_enabled())), checked=lambda _: startup_enabled()),
            item('查看日志 / Open logs', self.action(lambda: os.startfile(str(self.root)))),
            pystray.Menu.SEPARATOR,
            item('退出并停止播报 / Exit and stop narration', self.action(self.shutdown)))

    def shutdown(self):
        if self.done.is_set():
            return
        self.done.set()
        self.controller.set_enabled(False)
        if self.icon:
            self.icon.stop()

    def run(self):
        import pystray
        server_thread = Thread(target=self.server.serve_forever, daemon=True)
        server_thread.start()
        self.icon = pystray.Icon('SoundOfVibe', icon_image(), 'Sound of Vibe', self.menu())
        def setup(icon):
            icon.visible = True
            self.refresh()
            self.publish(True)
            for name, value in self.controller.status().items():
                if value['enabled']:
                    self.controller.set_enabled(True, name)
            while not self.done.wait(2):
                self.refresh()
        try:
            self.icon.run(setup)
        finally:
            self.shutdown()
            self.server.shutdown()
            self.server.server_close()
            self.publish(False)


def launch():
    if os.name != 'nt':
        raise ValueError('The tray application requires Windows')
    root = state_directory()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'tray.log').open('ab') as log:
        subprocess.Popen(tray_command(), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    print('Sound of Vibe 已在 Windows 托盘启动 / Started in the Windows tray')


def main():
    if os.name != 'nt':
        raise ValueError('The tray application requires Windows')
    instance = SingleInstance(state_directory())
    try:
        if not instance.acquired:
            return 0
        TrayApp().run()
        return 0
    finally:
        instance.close()


if __name__ == '__main__':
    main()
