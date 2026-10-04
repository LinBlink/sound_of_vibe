import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sound_of_vibe.codex_transcript import RolloutAdapter
from sound_of_vibe.kimi_transcript import WireAdapter
from sound_of_vibe.kimi_hooks import HookNarrator
from sound_of_vibe.models import Narration
from sound_of_vibe.rules import asks_user, classify_tool
from sound_of_vibe.speech import EdgeSpeech, Speaker
from sound_of_vibe.voice_assignment import VoiceAssignments, voice_profiles
from sound_of_vibe.voice_picker import create_server, save_settings


CATALOG = [{"ShortName": f"{lang}-{i}", "Locale": lang + "-XX", "Gender": "Female"}
           for lang in ("zh", "en") for i in range(10)]
PREFERRED = {"zh": "zh-0", "en": "en-0"}


class VoiceFeatureTests(unittest.TestCase):
    def test_chinese_profiles_include_all_tags_and_are_idempotent(self):
        voice = {'ShortName': 'zh-CN-YunjianNeural', 'Locale': 'zh-CN', 'Gender': 'Male',
                 'VoiceTag': {'VoicePersonalities': ['Passion']}}
        profiles = voice_profiles([voice])
        self.assertEqual(len(profiles), 3)
        self.assertEqual({v['PitchHz'] for v in profiles}, {100, 120, 150})
        self.assertEqual({v['BaseVoice'] for v in profiles}, {'zh-CN-YunjianNeural'})
        self.assertEqual(voice_profiles(profiles), profiles)
        with tempfile.TemporaryDirectory() as root:
            registry = VoiceAssignments(Path(root))
            catalog = profiles + [v for v in CATALOG if v['Locale'].startswith('en-')]
            pairs = [registry.choose(str(n), {'zh': voice['ShortName'], 'en': 'en-0'}, catalog) for n in range(3)]
            self.assertEqual(len({v['zh'] for v in pairs}), 3)

    def test_mechanical_resynthesis_flattens_pitch_and_preserves_duration(self):
        import numpy as np
        import parselmouth
        import soundfile as sf
        from io import BytesIO
        from sound_of_vibe.robotic import mechanical_audio
        rate = 24000
        t = np.arange(rate) / rate
        signal = 0.3 * np.sin(2 * np.pi * (160 * t + 40 * t * t))
        source = BytesIO()
        sf.write(source, signal, rate, format='WAV')
        output = mechanical_audio(source.getvalue(), 'Male')
        data, actual_rate = sf.read(BytesIO(output))
        self.assertEqual(actual_rate, rate)
        self.assertLess(abs(len(data) - len(signal)), 2)
        f0 = parselmouth.Sound(data, sampling_frequency=rate).to_pitch(pitch_floor=60, pitch_ceiling=500).selected_array['frequency']
        voiced = f0[f0 > 0][5:-5]
        self.assertGreater(len(voiced), 20)
        self.assertLess(np.std(voiced), 5)
        self.assertAlmostEqual(float(np.median(voiced)), 120, delta=5)
        high = mechanical_audio(source.getvalue(), 'Male', 150)
        data, _ = sf.read(BytesIO(high))
        f0 = parselmouth.Sound(data, sampling_frequency=rate).to_pitch(pitch_floor=60, pitch_ceiling=500).selected_array['frequency']
        self.assertAlmostEqual(float(np.median(f0[f0 > 0][5:-5])), 150, delta=5)

    def test_concurrent_workers_unique_stable_bilingual_and_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            registry = VoiceAssignments(Path(root))
            with ThreadPoolExecutor(max_workers=8) as pool:
                assigned = list(pool.map(lambda n: registry.choose(str(n), PREFERRED, CATALOG, now=100), range(8)))
            self.assertEqual(len({v['zh'] for v in assigned}), 8)
            self.assertEqual(len({v['en'] for v in assigned}), 8)
            restarted = VoiceAssignments(Path(root))
            self.assertEqual(restarted.choose('0', {'zh': 'zh-9', 'en': 'en-9'}, CATALOG, now=200), assigned[0])
            for n in range(8):
                registry.release(str(n))
            self.assertEqual(registry.choose('new', PREFERRED, CATALOG, now=201), PREFERRED)
            self.assertEqual(registry.choose('after-crash', PREFERRED, CATALOG, now=86602), PREFERRED)

    def test_exhaustion_never_assigns_duplicate_voice(self):
        with tempfile.TemporaryDirectory() as root:
            registry = VoiceAssignments(Path(root))
            tiny = [v for v in CATALOG if v['ShortName'].endswith('-0')]
            registry.choose('one', PREFERRED, tiny)
            with self.assertRaises(ValueError):
                registry.choose('two', PREFERRED, tiny)

    def test_save_preserves_disabled_hooks_and_existing_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'settings.json').write_text(json.dumps({'enabled': True, 'language': 'auto', 'custom': 123}))
            data = {'source': 'both', 'voices': PREFERRED, 'rate': '+0%', 'per_session_voice': True}
            save_settings(root, data, CATALOG)
            kimi = json.loads((root / 'settings.json').read_text())
            codex = json.loads((root / 'Codex/settings.json').read_text())
            self.assertEqual(kimi['custom'], 123)
            self.assertTrue(kimi['enabled'])
            self.assertFalse(codex['enabled'])
            self.assertTrue(codex['per_session_voice'])
            with self.assertRaises(ValueError):
                save_settings(root, {**data, 'voices': {'zh': 'en-0', 'en': 'en-0'}}, CATALOG)
            self.assertEqual(json.loads((root / 'settings.json').read_text()), kimi)

    def test_loopback_http_requires_token_and_serves_both_sounds(self):
        with tempfile.TemporaryDirectory() as root:
            server = create_server(Path(root), CATALOG)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url + '/') as response:
                    html = response.read().decode()
                self.assertIn('preview-zh', html)
                self.assertNotIn('__TOKEN__', html)
                for kind in ('complete', 'ask'):
                    with urlopen(url + '/sounds/' + kind) as response:
                        self.assertTrue(response.read().startswith(b'OggS'))
                with self.assertRaises(HTTPError) as blocked:
                    urlopen(Request(url + '/api/save', data=b'{}', method='POST'))
                self.assertEqual(blocked.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_explicit_ask_tools_and_final_questions(self):
        self.assertEqual(classify_tool('functions.request_user_input_async', {}), 'ask')
        self.assertTrue(asks_user('Which voice should I use?'))
        self.assertTrue(asks_user('你希望使用哪一种音色？'))
        self.assertFalse(asks_user('```python\nprint("?")\n```'))
        adapter = RolloutAdapter()
        self.assertEqual(adapter.feed({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': 'Which option do you prefer?'}]}}), [])
        result = adapter.feed({'type': 'event_msg', 'payload': {'type': 'task_complete'}})
        self.assertEqual(result[0].action, 'ask')
        self.assertTrue(result[0].terminal)
        wire = WireAdapter()
        wire.feed({'type': 'context.append_loop_event', 'event': {'type': 'content.part', 'part': {'type': 'text', 'text': '选择哪一个？'}}})
        result = wire.feed({'type': 'context.append_loop_event', 'event': {'type': 'step.end', 'finishReason': 'end_turn'}})
        self.assertEqual(result[0].action, 'ask')


class VoiceAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_uses_real_provider_voice_and_selected_mechanical_pitch(self):
        voice = {'ShortName': 'zh-CN-YunjianNeural', 'Locale': 'zh-CN', 'Gender': 'Male'}
        backend = EdgeSpeech({'zh': voice['ShortName'] + '::high'})
        backend.catalog = voice_profiles([voice])
        async def save(path):
            Path(path).write_bytes(b'fake provider mp3')
        communicate = Mock(return_value=SimpleNamespace(save=save))
        backend.edge = SimpleNamespace(Communicate=communicate)
        music = Mock()
        music.get_busy.return_value = False
        backend.mixer = SimpleNamespace(music=music)
        with patch('sound_of_vibe.robotic.mechanical_audio', return_value=b'RIFFmock') as transform:
            await backend.speak(Narration('测试完成。', 'zh', 'commentary'), lambda: False)
        communicate.assert_called_once_with('测试完成。', 'zh-CN-YunjianNeural', rate='+0%')
        transform.assert_called_once_with(b'fake provider mp3', 'Male', 150)

    async def test_stop_before_task_complete_uses_question_chime_once(self):
        output = []
        speaker = Speaker(None, output.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker)
        await narrator.feed({'session': 's', 'kind': 'TurnStarted', 'id': 'turn', 'language': 'en'})
        narrator.streams['s'] = SimpleNamespace(adapter=SimpleNamespace(question=True), poll=lambda: [])
        await narrator.feed({'session': 's', 'kind': 'Stop', 'id': 'stop'})
        await narrator.feed({'session': 's', 'kind': 'Stop', 'id': 'stop2'})
        await speaker.close()
        self.assertEqual([n.action for n in output], ['start', 'ask'])

    async def test_waiting_is_logged_without_audio_and_questions_survive_commentary(self):
        backend = SimpleNamespace(initialize=AsyncMock(), speak=AsyncMock(), close=Mock())
        output = []
        speaker = Speaker(backend, output.append, self.fail, interval=0, continuous=True, preserve_progress=True, prefer_commentary=True)
        speaker.submit(Narration('Still waiting', 'en', 'tool_wait'))
        speaker.submit(Narration('Still working', 'en', 'working'))
        self.assertIsNone(speaker.task)
        speaker.submit(Narration('Please answer', 'en', 'ask'))
        speaker.submit(Narration('I updated the file.', 'en', 'commentary'))
        speaker.submit(Narration('Done', 'en', 'complete', True))
        await speaker.close()
        self.assertEqual([call.args[0].action for call in backend.speak.call_args_list], ['ask', 'commentary', 'complete'])
        self.assertEqual(len(output), 5)

    async def test_two_chimes_play_offline_without_tts(self):
        backend = EdgeSpeech(PREFERRED)
        music = Mock()
        music.get_busy.return_value = False
        backend.mixer = SimpleNamespace(music=music)
        with patch.object(backend, 'initialize_audio'):
            for action in ('complete', 'ask'):
                self.assertTrue(await backend.speak(Narration('', 'en', action), lambda: False))
        paths = [call.args[0] for call in music.load.call_args_list]
        self.assertNotEqual(paths[0], paths[1])
        self.assertTrue(all(Path(path).exists() for path in paths))
        self.assertIsNone(backend.edge)


if __name__ == '__main__':
    unittest.main()
