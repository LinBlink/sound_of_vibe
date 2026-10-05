"""Non-blocking synthesis, cancellable playback, and a latest-progress queue."""

import asyncio
import os
import tempfile
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .models import Narration
from .voice_assignment import voice_profiles

DEFAULT_VOICES = {"zh": "zh-CN-YunyangNeural", "en": "en-US-EricNeural"}
SILENT_ACTIONS = {"working", "tool_wait"}
CHIME_ACTIONS = {"complete", "ask", "permission"}


class EdgeSpeech:
    def __init__(self, voices: dict[str, str], rate: str = "+0%", timeout: float = 10, robotic: bool = True):
        self.voices = voices
        self.rate = rate
        self.timeout = timeout
        self.mixer = None
        self.edge = None
        self.catalog = []
        self.robotic = robotic

    async def initialize(self):
        import edge_tts

        self.initialize_audio()
        self.edge = edge_tts
        voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=self.timeout)
        self.catalog = voice_profiles(voices)
        names = {voice["ShortName"] for voice in self.catalog}
        missing = set(self.voices.values()) - names
        if missing:
            raise ValueError("Unavailable Edge TTS voice: " + ", ".join(sorted(missing)))
    def initialize_audio(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        from pygame import mixer

        self.mixer = mixer
        if not mixer.get_init():
            mixer.init()

    def voice_for(self, narration: Narration) -> str:
        return self.voices[narration.language]

    async def speak(self, narration: Narration, stale: Callable[[], bool]):
        if narration.action in CHIME_ACTIONS:
            self.initialize_audio()
            name = "complete" if narration.action == "complete" else "ask"
            try:
                self.mixer.music.load(str(Path(__file__).parent / "assets" / (name + ".ogg")))
                self.mixer.music.play()
                while self.mixer.music.get_busy():
                    await asyncio.sleep(0.05)
                return True
            finally:
                self.mixer.music.stop()
                self.mixer.music.unload()
        if self.edge is None or self.mixer is None:
            raise RuntimeError("Speech backend has not been initialized")
        descriptor, filename = tempfile.mkstemp(prefix="sound-of-vibe-", suffix=".mp3")
        os.close(descriptor)
        path = Path(filename)
        try:
            voice = self.voice_for(narration)
            profile = next((v for v in self.catalog if v["ShortName"] == voice), {})
            provider_voice = profile.get("BaseVoice", voice)
            for attempt in range(2):
                try:
                    communicate = self.edge.Communicate(narration.text,
                                                       provider_voice, rate=self.rate)
                    await asyncio.wait_for(communicate.save(str(path)), timeout=self.timeout)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if attempt or stale():
                        raise
            if self.robotic:
                from .robotic import mechanical_audio
                gender = profile.get("Gender", "Female")
                audio = await asyncio.to_thread(mechanical_audio, path.read_bytes(), gender, profile.get("PitchHz"))
                path.write_bytes(audio)
            # New progress can arrive while the network request is running.
            if stale():
                return False
            self.mixer.music.load(str(path), namehint="wav" if self.robotic else "mp3")
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
                 max_pending: int = 3, prefer_commentary: bool = False,
                 max_commentary_pending: int = 256):
        self.backend = backend
        self.output = output
        self.diagnostic = diagnostic
        self.interval = interval
        self.continuous = continuous
        self.preserve_progress = preserve_progress
        self.max_pending = max(1, max_pending)
        self.prefer_commentary = prefer_commentary
        self.max_commentary_pending = max(self.max_pending, max_commentary_pending)
        self.backlog: deque[Narration] = deque()
        self.pending: Narration | None = None
        self.wake = asyncio.Event()
        self.generation = 0
        self.closed = False
        self.terminal = False
        self.task: asyncio.Task | None = None
        self.current: Narration | None = None

    def submit(self, narration: Narration):
        if self.closed or (self.terminal and not self.continuous):
            return
        self.output(narration)
        if narration.action in SILENT_ACTIONS:
            return
        self.terminal = narration.terminal
        if self.prefer_commentary:
            if narration.action == "commentary":
                if self.pending is not None and self.pending.session == narration.session and not self.pending.terminal and self.pending.action not in CHIME_ACTIONS | {"commentary"}:
                    self.pending = None
                self.backlog = deque(item for item in self.backlog if item.session != narration.session or item.terminal or item.action in CHIME_ACTIONS | {"commentary"})
            elif narration.action not in CHIME_ACTIONS and not narration.terminal and any(item.action == "commentary" and item.session == narration.session for item in
                                               ([self.pending] if self.pending else []) + list(self.backlog)):
                return
        if self.preserve_progress and self.pending is None and self.backlog:
            self.pending = self.backlog.popleft()
        if self.preserve_progress and self.pending is not None:
            # Preserve the next action and a bounded number of recent updates. A
            # completion must follow progress rather than overwrite all of it.
            if len(self.backlog) >= self.max_pending - 1:
                if self.backlog:
                    discard = next((item for item in self.backlog if item.action not in CHIME_ACTIONS and not item.terminal
                                    and (not self.prefer_commentary or item.action != "commentary")), None)
                    if discard:
                        self.backlog.remove(discard)
                    elif narration.action not in CHIME_ACTIONS and not narration.terminal:
                        if not (self.prefer_commentary and narration.action == "commentary"
                                and len(self.backlog) < self.max_commentary_pending - 1):
                            if narration.action == "commentary":
                                self.diagnostic("Visible narration backlog limit reached; text retained in the log.")
                            return
                else:
                    if self.prefer_commentary and self.pending.action == "commentary":
                        if narration.action == "commentary" or narration.terminal or narration.action in CHIME_ACTIONS:
                            self.backlog.append(narration)
                            self.generation += 1
                            self.wake.set()
                        return
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
                    if self.pending.action in CHIME_ACTIONS:
                        if hasattr(self.backend, "initialize_audio"):
                            self.backend.initialize_audio()
                        else:
                            await self.backend.initialize()
                            initialized = True
                    else:
                        await self.backend.initialize()
                        initialized = True
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.diagnostic(f"Audio unavailable; continuing with text ({type(error).__name__}).")
                    if hasattr(self.backend, "initialize_audio") and self.backend.mixer is not None:
                        self.backend.edge = None
                        initialized = True
                    else:
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
                    self.current = narration
                    last_started = loop.time()
                    await self.backend.speak(narration, lambda: not self.preserve_progress and generation != self.generation)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.diagnostic(f"Speech failed; continuing with text ({type(error).__name__}).")
                finally:
                    self.current = None
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
