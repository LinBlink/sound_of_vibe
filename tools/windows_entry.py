"""Entry point for the standalone, console-free Windows distribution."""

import multiprocessing
import os
from pathlib import Path
import sys


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if sys.stderr is None:
        directory = Path(os.environ.get('SOUND_OF_VIBE_STATE_DIR', Path(os.environ['LOCALAPPDATA']) / 'SoundOfVibe'))
        directory.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = (directory / 'tray.log').open('a', encoding='utf-8', buffering=1)
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        from sound_of_vibe.kimi_hooks import main
        raise SystemExit(main(sys.argv[1:]))
    if len(sys.argv) > 1:
        from sound_of_vibe.cli import main
        raise SystemExit(main(sys.argv[1:]))
    from sound_of_vibe.tray import main
    raise SystemExit(main())
