"""Independent wrapper invocations participate in global voice reservations."""

import uuid

from .local_tts import LocalSpeech
from .voice_assignment import VoiceAssignments


class WindowSpeech(LocalSpeech):
    def __init__(self, voices, rate, directory):
        super().__init__(voices, rate)
        self.registry = VoiceAssignments(directory)
        self.identity = "wrapper:" + uuid.uuid4().hex

    def voice_for(self, narration):
        return self.registry.choose(self.identity, self.voices, self.catalog)[narration.language]

    def close(self):
        try:
            super().close()
        finally:
            self.registry.release(self.identity)
