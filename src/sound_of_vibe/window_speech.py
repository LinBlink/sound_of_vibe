"""Independent wrapper invocations participate in global voice reservations."""

import uuid
import time

from .local_tts import LocalSpeech
from .voice_assignment import VoiceAssignments


class WindowSpeech(LocalSpeech):
    def __init__(self, voices, rate, directory):
        super().__init__(voices, rate)
        self.registry = VoiceAssignments(directory)
        self.identity = "wrapper:" + uuid.uuid4().hex
        self.directory = directory
        from .kimi_hooks import load_settings
        self.settings = load_settings(directory)
        self.last_control_rate = self.settings['rate']
        self.last_controls_read = 0

    def playback_controls(self):
        from .kimi_hooks import load_settings
        now = time.monotonic()
        if now - self.last_controls_read >= .05:
            self.settings = load_settings(self.directory)
            self.last_controls_read = now
        return self.settings

    def voice_for(self, narration):
        settings = self.playback_controls()
        if settings['rate'] != self.last_control_rate:
            self.rate = settings['rate']
            self.last_control_rate = self.rate
        self.adaptive_rate = settings['adaptive_rate']
        self.max_rate = settings['max_rate']
        return self.registry.choose(self.identity, self.voices, self.catalog)[narration.language]

    def close(self):
        try:
            super().close()
        finally:
            self.registry.release(self.identity)
