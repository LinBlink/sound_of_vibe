import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from sound_of_vibe.kimi_hooks import LoggedSpeech, load_settings
from sound_of_vibe.models import Narration
from sound_of_vibe.speech import CHIME_ACTIONS, DEFAULT_VOICES, EdgeSpeech, Speaker
from sound_of_vibe.voice_picker import save_settings


class AdaptiveRateTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_accelerates_then_recovers_without_losing_text_or_chime(self):
        class RecordingSpeech(EdgeSpeech):
            async def initialize(self):
                pass

            async def speak(self, narration, stale):
                played.append((narration.text, self.rate_for(), narration.action))
                if len(played) == 1:
                    started.set()
                    await release.wait()

        played = []
        started, release = asyncio.Event(), asyncio.Event()
        backend = RecordingSpeech(DEFAULT_VOICES, '+10%')
        speaker = Speaker(backend, lambda _: None, self.fail, interval=0,
                          continuous=True, preserve_progress=True, prefer_commentary=True)
        first = Narration('开始检查。', 'zh', 'commentary')
        speaker.submit(first)
        await started.wait()
        items = [Narration(('检查结果并继续执行下一步。' * 10) if i % 2 else
                           ('Review the results and continue checking the next step. ' * 8),
                           'zh' if i % 2 else 'en', 'commentary') for i in range(6)]
        for item in items:
            speaker.submit(item)
        speaker.submit(Narration('完成', 'zh', 'complete', True))
        release.set()
        await speaker.close()
        self.assertEqual([row[0] for row in played], [first.text] + [i.text for i in items] + ['完成'])
        self.assertEqual(played[1][1], '+100%')
        self.assertEqual(played[-2][1], '+10%')
        self.assertEqual(played[-1][2], 'complete')
        self.assertEqual(backend.rate, '+10%')

    async def test_manual_rate_cap_disable_and_chime_exclusion(self):
        backend = EdgeSpeech(DEFAULT_VOICES, '-10%')
        backend.max_rate = 60
        work = Narration('测试结果。' * 100, 'zh', 'commentary')
        backend.set_backlog([work])
        self.assertEqual(backend.rate_for(), '+60%')
        backend.adaptive_rate = False
        self.assertEqual(backend.rate_for(), '-10%')
        backend.adaptive_rate = True
        backend.set_backlog([Narration(work.text, 'zh', action) for action in CHIME_ACTIONS])
        self.assertEqual(backend.rate_for(), '-10%')

    async def test_saved_preferences_reload_on_next_utterance(self):
        catalog = [{'ShortName': voice, 'Locale': language + '-XX'}
                   for language, voice in DEFAULT_VOICES.items()]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = LoggedSpeech(root, load_settings(root))
            backend.set_backlog([Narration('继续检查。' * 100, 'zh', 'commentary')])
            preferences = dict(voices=DEFAULT_VOICES, rate='+20%', per_session_voice=False,
                               adaptive_rate=True, max_rate=70)
            save_settings(root, preferences, catalog)
            backend.voice_for(Narration('测试', 'zh', 'commentary'))
            self.assertEqual(backend.rate_for(), '+70%')
            save_settings(root, {**preferences, 'adaptive_rate': False}, catalog)
            backend.voice_for(Narration('测试', 'zh', 'commentary'))
            self.assertEqual(backend.rate_for(), '+20%')
            for invalid in ({'max_rate': 101}, {'max_rate': True}, {'max_rate': 10},
                            {'adaptive_rate': 'yes'}):
                with self.assertRaises(ValueError):
                    save_settings(root, {**preferences, **invalid}, catalog)
            self.assertFalse(json.loads((root / 'settings.json').read_text())['enabled'])
