"""Manual smoke test: synthesize and play one Chinese and one English sentence."""

import asyncio

from sound_of_vibe.models import Narration
from sound_of_vibe.speech import DEFAULT_VOICES, EdgeSpeech


async def main():
    speech = EdgeSpeech(DEFAULT_VOICES)
    try:
        await speech.initialize()
        for narration in (Narration("正在查看文件。", "zh", "read"),
                          Narration("Reading files.", "en", "read")):
            await speech.speak(narration, lambda: False)
            print(f"Synthesized and played: {narration.language}", flush=True)
    finally:
        speech.close()


if __name__ == "__main__":
    asyncio.run(main())
