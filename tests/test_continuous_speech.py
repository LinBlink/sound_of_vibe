import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sound_of_vibe.models import Narration
from sound_of_vibe.robotic import mechanical_audio
from sound_of_vibe.speech import DEFAULT_VOICES, EdgeSpeech, Speaker


class ContinuousSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_sentence_synthesizes_during_playback_and_is_reused(self):
        class Backend(EdgeSpeech):
            async def initialize(self):
                pass

        backend = Backend(DEFAULT_VOICES, robotic=False)
        first_playing, next_ready, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests, plays = [], []
        paths = []

        def communicate(text, voice, rate):
            async def save(path):
                requests.append((text, voice, rate))
                paths.append(path)
                Path(path).write_bytes(text.encode())
                if text == 'Next sentence.':
                    self.assertTrue(first_playing.is_set())
                    self.assertFalse(release.is_set())
                    next_ready.set()
            return SimpleNamespace(save=save)

        music = Mock()
        music.load.side_effect = lambda path, **_: plays.append(Path(path).read_bytes().decode())
        music.play.side_effect = first_playing.set
        music.get_busy.side_effect = lambda: len(plays) == 1 and not release.is_set()
        backend.edge = SimpleNamespace(Communicate=communicate)
        backend.mixer = SimpleNamespace(music=music, get_init=lambda: False)
        speaker = Speaker(backend, lambda _: None, self.fail, preserve_progress=True)
        speaker.submit(Narration('第一句。', 'zh', 'commentary'))
        await asyncio.wait_for(first_playing.wait(), 1)
        # Queueing while speech is already playing also triggers prefetch.
        speaker.submit(Narration('Next sentence.', 'en', 'commentary'))
        await asyncio.wait_for(next_ready.wait(), 1)
        release.set()
        await speaker.close()
        self.assertEqual(plays, ['第一句。', 'Next sentence.'])
        self.assertEqual(len(requests), 2)
        self.assertEqual([request[1] for request in requests], [DEFAULT_VOICES['zh'], DEFAULT_VOICES['en']])
        self.assertEqual(speaker.interval, 0)
        self.assertTrue(all(not Path(path).exists() for path in paths))

    async def test_cancel_removes_inflight_prefetch_and_partial_file(self):
        backend = EdgeSpeech(DEFAULT_VOICES, robotic=False)
        started = asyncio.Event()
        paths = []

        async def save(path):
            paths.append(path)
            Path(path).write_bytes(b'partial')
            started.set()
            await asyncio.Event().wait()

        backend.edge = SimpleNamespace(Communicate=lambda *_, **__: SimpleNamespace(save=save))
        backend.next_narration = lambda: (Narration('Next.', 'en', 'commentary'), [])
        backend.prefetch_next()
        task = backend.prefetched[2]
        await started.wait()
        backend.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(backend.prefetched)
        self.assertTrue(all(not Path(path).exists() for path in paths))

    async def test_trim_removes_edge_padding_but_preserves_internal_pause(self):
        import numpy as np
        import soundfile as sf
        rate = 24000
        tone = .3 * np.sin(2 * np.pi * 120 * np.arange(rate // 2) / rate)
        silence = np.zeros(rate // 3)
        waveform = np.concatenate([silence, tone, silence, tone, silence])
        source = BytesIO()
        sf.write(source, waveform, rate, format='WAV')
        samples, _ = sf.read(BytesIO(mechanical_audio(source.getvalue(), 'Male')))
        self.assertLess(len(samples) / rate, 1.4)
        self.assertGreater(len(samples) / rate, 1.3)
        self.assertLess(float(np.max(np.abs(samples[round(rate*.6):round(rate*.75)]))), .003)
