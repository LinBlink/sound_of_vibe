import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sound_of_vibe.models import Narration
from sound_of_vibe.speech import EdgeSpeech, Speaker


class FakeSpeech:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.spoken = []
        self.closed = False

    async def initialize(self):
        pass

    async def speak(self, narration, stale):
        self.spoken.append(narration.text)
        if len(self.spoken) == 1:
            self.started.set()
            await self.release.wait()

    def close(self):
        self.closed = True


def utterance(text, terminal=False):
    return Narration(text, "en", "complete" if terminal else "read", terminal)


class SpeakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_pending_replaces_backlog_and_terminal_supersedes(self):
        backend = FakeSpeech()
        output = []
        speaker = Speaker(backend, output.append, self.fail, interval=0)
        speaker.submit(utterance("first"))
        await backend.started.wait()
        speaker.submit(utterance("second"))
        speaker.submit(utterance("third"))
        speaker.submit(utterance("complete", True))
        speaker.submit(utterance("too late"))
        backend.release.set()
        await speaker.close()
        self.assertEqual(backend.spoken, ["first", "complete"])
        self.assertEqual([item.text for item in output], ["first", "second", "third", "complete"])
        self.assertTrue(backend.closed)

    async def test_initialization_failure_degrades_to_text(self):
        backend = FakeSpeech()
        backend.initialize = AsyncMock(side_effect=TimeoutError())
        diagnostics = []
        output = []
        speaker = Speaker(backend, output.append, diagnostics.append, interval=0)
        speaker.submit(utterance("first"))
        await asyncio.sleep(0)
        speaker.submit(utterance("complete", True))
        await speaker.close()
        self.assertEqual(backend.spoken, [])
        self.assertTrue(backend.closed)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(len(output), 2)

    async def test_cancellation_closes_backend(self):
        backend = FakeSpeech()
        speaker = Speaker(backend, lambda _: None, self.fail, interval=0)
        speaker.submit(utterance("first"))
        await backend.started.wait()
        await speaker.close(cancel=True)
        self.assertTrue(backend.closed)
        self.assertTrue(speaker.task.done())

    async def test_playback_interval_does_not_block_submit(self):
        backend = FakeSpeech()
        backend.release.set()
        speaker = Speaker(backend, lambda _: None, self.fail, interval=0.04)
        speaker.submit(utterance("first"))
        await backend.started.wait()
        speaker.submit(utterance("second"))
        await asyncio.sleep(0.01)
        speaker.submit(utterance("latest"))
        await speaker.close()
        self.assertEqual(backend.spoken, ["first", "latest"])

    async def test_synthesis_retry_and_tempfile_cleanup(self):
        speech = EdgeSpeech({"en": "en-US-AriaNeural"}, timeout=0.02)
        attempts = []

        async def save(path):
            attempts.append(path)
            Path(path).write_bytes(b"fake mp3")
            if len(attempts) == 1:
                raise OSError("network")

        speech.edge = SimpleNamespace(Communicate=lambda *args, **kwargs: SimpleNamespace(save=save))
        music = Mock()
        music.get_busy.return_value = False
        speech.mixer = SimpleNamespace(music=music)
        await speech.speak(utterance("hello"), lambda: False)
        self.assertEqual(len(attempts), 2)
        self.assertFalse(Path(attempts[0]).exists())
        music.play.assert_called_once()
        music.unload.assert_called_once()

    async def test_synthesis_timeout_and_stale_progress(self):
        speech = EdgeSpeech({"en": "en-US-AriaNeural"}, timeout=0.01)
        paths = []

        async def save(path):
            paths.append(path)
            await asyncio.sleep(10)

        speech.edge = SimpleNamespace(Communicate=lambda *args, **kwargs: SimpleNamespace(save=save))
        speech.mixer = SimpleNamespace(music=Mock())
        with self.assertRaises(TimeoutError):
            await speech.speak(utterance("hello"), lambda: False)
        self.assertEqual(len(paths), 2)
        self.assertFalse(Path(paths[0]).exists())
        speech.mixer.music.play.assert_not_called()

    async def test_cancelled_synthesis_removes_partial_audio(self):
        speech = EdgeSpeech({"en": "en-US-AriaNeural"})
        started = asyncio.Event()
        paths = []

        async def save(path):
            paths.append(path)
            Path(path).write_bytes(b"partial")
            started.set()
            await asyncio.Event().wait()

        speech.edge = SimpleNamespace(Communicate=lambda *args, **kwargs: SimpleNamespace(save=save))
        speech.mixer = SimpleNamespace(music=Mock())
        task = asyncio.create_task(speech.speak(utterance("hello"), lambda: False))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(Path(paths[0]).exists())

    async def test_stale_synthesis_is_not_played(self):
        speech = EdgeSpeech({"en": "en-US-AriaNeural"})
        speech.edge = SimpleNamespace(Communicate=lambda *args, **kwargs: SimpleNamespace(save=AsyncMock()))
        speech.mixer = SimpleNamespace(music=Mock())
        await speech.speak(utterance("old progress"), lambda: True)
        speech.mixer.music.play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
