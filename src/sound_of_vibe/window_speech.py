"""Independent wrapper invocations participate in global voice reservations."""

import uuid

from .speech import EdgeSpeech
from .voice_assignment import VoiceAssignments


class WindowSpeech(EdgeSpeech):
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
