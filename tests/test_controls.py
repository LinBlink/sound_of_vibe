import asyncio
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sound_of_vibe.control import ServiceController
from sound_of_vibe.kimi_hooks import HookQueue, load_settings
from sound_of_vibe.models import Narration
from sound_of_vibe.robotic import mechanical_audio
from sound_of_vibe.speech import EdgeSpeech
from sound_of_vibe.tray import SingleInstance, TrayApp


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_revokes_lease_and_clears_old_events_without_touching_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'settings.json'
            path.write_text(json.dumps({'enabled': True, 'custom': 'kept'}))
            queue = HookQueue(root)
            token = queue.reserve()
            queue.renew(token)
            queue.publish({'kind': 'Stop', 'session': 'old'})
            controller = ServiceController(root)
            controller.set_enabled(False)
            self.assertFalse(load_settings(root)['enabled'])
            self.assertFalse(queue.renew(token))
            self.assertEqual(queue.take(), [])
            with patch('sound_of_vibe.control.launch_worker') as launch:
                controller.set_enabled(True)
            launch.assert_called_once()
            self.assertEqual(load_settings(root)['custom'], 'kept')
            self.assertFalse((root / 'Codex/settings.json').exists())

    async def test_live_controls_preserve_preferences_and_disabled_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'settings.json').write_text(json.dumps({'enabled': False, 'voices': {'zh': 'choice'}, 'custom': 9}))
            controller = ServiceController(root)
            controller.patch({'source': 'kimi', 'volume': 37, 'rate': '+60%', 'muted': True, 'max_rate': 20})
            settings = load_settings(root)
            self.assertEqual((settings['volume'], settings['rate'], settings['max_rate']), (37, '+60%', 60))
            self.assertTrue(settings['muted'])
            self.assertFalse(settings['enabled'])
            self.assertEqual(settings['voices'], {'zh': 'choice'})
            for invalid in ({'volume': True}, {'volume': -1}, {'muted': 'yes'}, {'rate': '+101%'}, {'enabled': True}):
                with self.assertRaises(ValueError):
                    controller.patch(invalid)
            self.assertEqual(load_settings(root)['custom'], 9)

    async def test_playing_chime_volume_and_stop_apply_without_restart(self):
        backend = EdgeSpeech({})
        music = Mock()
        music.get_busy.return_value = True
        backend.mixer = SimpleNamespace(music=music)
        backend.initialize_audio = lambda: None
        controls = {'enabled': True, 'volume': 30, 'muted': False}
        backend.playback_controls = lambda: controls
        task = asyncio.create_task(backend.speak(Narration('Done', 'en', 'complete'), lambda: False))
        await asyncio.sleep(.01)
        music.set_volume.assert_any_call(.3)
        controls['muted'] = True
        await asyncio.sleep(.06)
        self.assertEqual(music.set_volume.call_args.args, (0,))
        controls['enabled'] = False
        self.assertFalse(await asyncio.wait_for(task, .2))
        music.stop.assert_called()

    async def test_native_pitch_flattening_preserves_center_without_forced_190_hz(self):
        import numpy as np
        import parselmouth
        import soundfile as sf
        rate = 24000
        t = np.arange(rate) / rate
        source = BytesIO()
        sf.write(source, .3 * np.sin(2 * np.pi * (240 * t + 20 * t * t)), rate, format='WAV')
        audio = mechanical_audio(source.getvalue(), 'Unknown')
        samples, _ = sf.read(BytesIO(audio))
        frequencies = parselmouth.Sound(samples, sampling_frequency=rate).to_pitch(pitch_floor=60, pitch_ceiling=500).selected_array['frequency']
        voiced = frequencies[frequencies > 0][5:-5]
        self.assertAlmostEqual(float(np.median(voiced)), 260, delta=5)
        self.assertLess(float(np.std(voiced)), 5)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows singleton')
    async def test_tray_single_instance_and_bilingual_menu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = SingleInstance(root), SingleInstance(root)
            try:
                self.assertTrue(first.acquired)
                self.assertFalse(second.acquired)
                app = TrayApp(root)
                try:
                    labels = [item.text for item in app.menu().items]
                    self.assertIn('停止播报服务 / Stop narration', labels)
                    self.assertIn('音量 / Volume', labels)
                finally:
                    app.server.server_close()
            finally:
                first.close()
                second.close()
