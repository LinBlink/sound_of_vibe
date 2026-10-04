"""Non-blocking synthesis, cancellable playback, and a latest-progress queue."""

import asyncio
import os
import tempfile
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .models import Narration

DEFAULT_VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "en": "en-US-AriaNeural"}


class EdgeSpeech:
    def __init__(self, voices: dict[str, str], rate: str = "+10%", timeout: float = 10):
        self.voices = voices
        self.rate = rate
        self.timeout = timeout
        self.mixer = None
        self.edge = None

    async def initialize(self):
        import edge_tts

        self.edge = edge_tts
        voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=self.timeout)
        names = {voice["ShortName"] for voice in voices}
        missing = set(self.voices.values()) - names
        if missing:
            raise ValueError("Unavailable Edge TTS voice: " + ", ".join(sorted(missing)))
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        from pygame import mixer

        self.mixer = mixer
        mixer.init()

    async def speak(self, narration: Narration, stale: Callable[[], bool]):
        if self.edge is None or self.mixer is None:
            raise RuntimeError("Speech backend has not been initialized")
        descriptor, filename = tempfile.mkstemp(prefix="sound-of-vibe-", suffix=".mp3")
        os.close(descriptor)
        path = Path(filename)
        try:
            for attempt in range(2):
                try:
                    communicate = self.edge.Communicate(narration.text,
                                                       self.voices[narration.language], rate=self.rate)
                    await asyncio.wait_for(communicate.save(str(path)), timeout=self.timeout)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if attempt or stale():
                        raise
            # New progress can arrive while the network request is running.
            if stale():
                return False
            self.mixer.music.load(str(path))
            self.mixer.music.play()
            while self.mixer.music.get_busy():
                await asyncio.sleep(0.05)
            return True
        finally:
            try:
                if self.mixer:
                    self.mixer.music.stop()
                    self.mixer.music.unload()
            finally:
                path.unlink(missing_ok=True)

    def close(self):
        if self.mixer and self.mixer.get_init():
            self.mixer.music.stop()
            self.mixer.music.unload()
            self.mixer.quit()
        self.mixer = None


class Speaker:
    """One current utterance and a bounded pending queue.

    Default: latest pending only. Preserved mode uses a configurable queue bound.
    Text output is immediate. Audio pacing never blocks the event reader.
    """

    def __init__(self, backend: EdgeSpeech | None,
                 output: Callable[[Narration], None], diagnostic: Callable[[str], None],
                 interval: float = 3, continuous: bool = False, preserve_progress: bool = False,
                 max_pending: int = 3):
        self.backend = backend
        self.output = output
        self.diagnostic = diagnostic
        self.interval = interval
        self.continuous = continuous
        self.preserve_progress = preserve_progress
        self.max_pending = max(1, max_pending)
        self.backlog: deque[Narration] = deque()
        self.pending: Narration | None = None
        self.wake = asyncio.Event()
        self.generation = 0
        self.closed = False
        self.terminal = False
        self.task: asyncio.Task | None = None

    def submit(self, narration: Narration):
        if self.closed or (self.terminal and not self.continuous):
            return
        self.output(narration)
        self.terminal = narration.terminal
        if self.preserve_progress and self.pending is None and self.backlog:
            self.pending = self.backlog.popleft()
        if self.preserve_progress and self.pending is not None:
            # Preserve the next action and a bounded number of recent updates. A
            # completion must follow progress rather than overwrite all of it.
            if len(self.backlog) >= self.max_pending - 1:
                if self.backlog:
                    self.backlog.popleft()
                else:
                    self.pending = narration
                    self.generation += 1
                    self.wake.set()
                    return
            self.backlog.append(narration)
        else:
            self.pending = narration
        self.generation += 1
        self.wake.set()
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        initialized = False
        last_started = float("-inf")
        loop = asyncio.get_running_loop()
        while True:
            await self.wake.wait()
            self.wake.clear()
            if self.pending is None:
                if self.closed:
                    return
                continue
            if self.backend is not None and not initialized:
                try:
                    await self.backend.initialize()
                    initialized = True
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.diagnostic(f"Audio unavailable; continuing with text ({type(error).__name__}).")
                    self.backend.close()
                    self.backend = None
            if self.backend is not None and not self.pending.terminal:
                delay = self.interval - (loop.time() - last_started)
                if delay > 0:
                    await asyncio.sleep(delay)
            narration, self.pending = self.pending, None
            generation = self.generation
            if self.backend is not None:
                try:
                    last_started = loop.time()
                    await self.backend.speak(narration, lambda: not self.preserve_progress and generation != self.generation)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.diagnostic(f"Speech failed; continuing with text ({type(error).__name__}).")
            if self.preserve_progress and self.pending is None and self.backlog:
                self.pending = self.backlog.popleft()
                self.wake.set()
            if self.closed and self.pending is None:
                return

    async def close(self, cancel: bool = False):
        self.closed = True
        self.wake.set()
        if self.task:
            if cancel:
                self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                if not cancel:
                    raise
        if self.backend:
            self.backend.close()

    async def interrupt(self):
        """Stop this utterance immediately, retaining the reusable speaker."""
        self.pending = None
        self.backlog.clear()
        self.generation += 1
        self.wake.clear()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        if self.backend:
            self.backend.close()
        self.terminal = False
