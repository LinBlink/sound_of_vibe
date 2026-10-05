import asyncio
from io import BytesIO
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from sound_of_vibe.local_tts import (LOCAL_VOICES, LocalEngine, LocalSpeech, install_model,
                                     language_segments, local_catalog, local_voices, model_ready)
from sound_of_vibe.models import Narration
from sound_of_vibe.voice_assignment import VoiceAssignments
from sound_of_vibe.voice_picker import preview


class LocalTtsTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_voice_pools_are_distinct_and_old_preferences_migrate(self):
        catalog = local_catalog()
        self.assertEqual(len({v['BaseVoice'] for v in catalog if v['Locale'].startswith('zh-')}), 174)
        self.assertEqual(len({v['BaseVoice'] for v in catalog if v['Locale'].startswith('en-')}), 109)
        self.assertEqual(local_voices({'zh': 'zh-CN-YunyangNeural', 'en': 'en-US-EricNeural'}), LOCAL_VOICES)
        with tempfile.TemporaryDirectory() as temporary:
            registry = VoiceAssignments(Path(temporary))
            pairs = [registry.choose(str(i), LOCAL_VOICES, catalog) for i in range(12)]
            self.assertEqual(len({p['zh'] for p in pairs}), 12)
            self.assertEqual(len({p['en'] for p in pairs}), 12)

    async def test_initialize_and_synthesize_do_not_connect_to_network(self):
        backend = LocalSpeech()
        engine = Mock()
        engine.synthesize.return_value = b'RIFFlocal-wav'
        narration = Narration('文件修改完成。', 'zh', 'commentary')
        with patch('socket.socket.connect', side_effect=AssertionError('Network prohibited')):
            with patch.object(backend, 'initialize_audio'), patch('sound_of_vibe.local_tts.local_engine', return_value=engine):
                await backend.initialize()
                await backend.synthesize(narration, backend.synthesis_settings(narration), lambda: False)
        args = engine.synthesize.call_args.args
        self.assertEqual(args[:3], (narration.text, 66, 1.0))
        self.assertEqual(args[-3:], ('zh', 0, 1.0))

    async def test_voice_preview_uses_the_same_local_engine_and_selected_pitch(self):
        catalog = local_catalog()
        engine = Mock()
        engine.synthesize.return_value = b'RIFFpreview'
        with patch('sound_of_vibe.voice_picker.local_engine', return_value=engine):
            result = await preview('local:zh:066::high', '+50%', catalog)
        self.assertEqual(result, b'RIFFpreview')
        args = engine.synthesize.call_args.args
        self.assertEqual(args[1:3], (66, 1.5))
        self.assertIsNone(args[4])
        self.assertEqual(engine.synthesize.call_args.kwargs['pitch_scale'], 1.1)
        self.assertEqual(engine.synthesize.call_args.kwargs['language'], 'zh')

    async def test_mixed_text_and_cancellation_never_drop_english_identifiers(self):
        import numpy as np
        import soundfile as sf
        self.assertEqual(language_segments('检查 FastAPI 和 httpx。', 'zh'),
                         [('zh', '检查 '), ('en', 'FastAPI'), ('zh', ' 和 '), ('en', 'httpx')])
        engine = LocalEngine.__new__(LocalEngine)
        generated = SimpleNamespace(samples=np.ones(2400) * .1, sample_rate=24000)
        engine.tts = {lang: SimpleNamespace(generate=Mock(return_value=generated)) for lang in ('zh', 'en')}
        engine.lock = Lock()
        data = engine.synthesize('检查 FastAPI。', 66, 1, 'Unknown', 190,
                                 robotic=False, secondary_sid=7)
        self.assertEqual(sf.info(BytesIO(data)).frames, 4800)
        self.assertEqual(engine.tts['en'].generate.call_args.kwargs['text'], 'FastAPI')
        self.assertEqual(engine.tts['en'].generate.call_args.kwargs['sid'], 7)
        self.assertEqual(engine.synthesize('测试', 66, 1, 'Unknown', 190, cancelled=lambda: True), b'')

    async def test_missing_model_stays_offline_and_download_checksum_is_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertFalse(model_ready(directory))
            with patch('socket.socket.connect', side_effect=AssertionError('Network prohibited')):
                with self.assertRaisesRegex(ValueError, 'tts install'):
                    LocalEngine(directory)
            with patch('sound_of_vibe.local_tts.model_directory', return_value=directory), \
                    patch('sound_of_vibe.local_tts.urlopen', return_value=BytesIO(b'corrupted model')):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    install_model(lambda _: None)
            self.assertEqual(list(directory.iterdir()), [])
