"""Measure actual offline synthesis; optionally play bilingual narration/chimes."""

import argparse
import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import soundfile as sf

from sound_of_vibe.local_tts import LOCAL_VOICES, local_catalog, local_engine
from sound_of_vibe.models import Narration
from sound_of_vibe.speech import Speaker
from sound_of_vibe.voice_picker import preview
from sound_of_vibe.window_speech import WindowSpeech


async def smoke(play):
    # Loading, synthesis, previews and playback must succeed without sockets.
    with patch('socket.socket.connect', side_effect=AssertionError('TTS attempted network access')):
        started = time.perf_counter()
        await asyncio.to_thread(local_engine)
        print(f'Cold model load: {time.perf_counter() - started:.3f}s', flush=True)
        catalog = local_catalog()
        for language in ('zh', 'en'):
            for rate in ('+0%', '+100%'):
                started = time.perf_counter()
                audio = await preview(LOCAL_VOICES[language], rate, catalog)
                duration = sf.info(BytesIO(audio)).duration
                assert duration > .5
                print(f'{language} {rate}: synthesis {time.perf_counter() - started:.3f}s; audio {duration:.3f}s', flush=True)
        if play:
            with tempfile.TemporaryDirectory() as temporary:
                first = WindowSpeech(LOCAL_VOICES, '+0%', Path(temporary))
                second = WindowSpeech(LOCAL_VOICES, '+0%', Path(temporary))
                # Initialize once, then verify independent bilingual assignments.
                await first.initialize()
                second.catalog = first.catalog
                for language in ('zh', 'en'):
                    item = Narration('voice allocation', language, 'commentary')
                    assert first.voice_for(item) != second.voice_for(item)
                diagnostics = []
                speaker = Speaker(first, lambda _: None, diagnostics.append,
                                  preserve_progress=True, prefer_commentary=True)
                try:
                    speaker.submit(Narration('文件修改已完成。FastAPI 测试全部通过。', 'zh', 'commentary'))
                    speaker.submit(Narration('The update is complete. All tests passed.', 'en', 'commentary'))
                    speaker.submit(Narration('Task finished', 'en', 'complete', True))
                    await speaker.close()
                    assert not diagnostics, diagnostics
                finally:
                    await speaker.close(cancel=True)
                    second.close()
            print('Offline bilingual playback, mixed identifiers, distinct sessions and completion chime passed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--play', action='store_true')
    asyncio.run(smoke(parser.parse_args().play))
