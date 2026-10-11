"""Build the standalone Windows tray application (models stay in local cache)."""

import subprocess
import sys
from pathlib import Path


def build():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--windowed', '--onedir', '--name', 'SoundOfVibe',
                    '--collect-all', 'sherpa_onnx', '--collect-all', 'sherpa_onnx_core',
                    '--collect-all', 'sound_of_vibe', '--collect-all', 'parselmouth', '--collect-all', 'pyworld',
                    '--hidden-import', 'pystray._win32', str(root / 'tools/windows_entry.py')],
                   cwd=root, check=True)
    print(root / 'dist/SoundOfVibe/SoundOfVibe.exe')


if __name__ == '__main__':
    build()
