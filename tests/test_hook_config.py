import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from sound_of_vibe.hook_config import BEGIN, END, disable, install, remove_block, status
from sound_of_vibe.kimi_hooks import HOOK_EVENTS


class ConfigTests(unittest.TestCase):
    def test_install_is_idempotent_preserves_settings_and_existing_hooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            original = '# user comment\ndefault_model = "test"\n[[hooks]]\nevent = "Stop"\ncommand = "echo existing"\n'
            config.write_text(original, encoding="utf-8")
            first = install(config, root / "state", text_only=True)
            self.assertEqual(Path(first["backup"]).read_text(encoding="utf-8"), original)
            install(config, root / "state", text_only=True)
            result = config.read_text(encoding="utf-8")
            parsed = tomllib.loads(result)
            self.assertEqual(parsed["default_model"], "test")
            self.assertEqual(parsed["hooks"][0]["command"], "echo existing")
            self.assertEqual(len(parsed["hooks"]), len(HOOK_EVENTS) + 1)
            self.assertEqual(result.count(BEGIN), 1)
            self.assertIn("# user comment", result)
            self.assertTrue(status(config, root / "state")["enabled"])

    def test_disable_removes_only_our_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            config.write_text('[providers.test]\nsecret="do-not-print"\n')
            install(config, root / "state", text_only=True)
            disable(config, root / "state")
            self.assertEqual(tomllib.loads(config.read_text())["providers"]["test"]["secret"], "do-not-print")
            self.assertNotIn(BEGIN, config.read_text())
            self.assertFalse(status(config, root / "state")["enabled"])
            self.assertFalse(json.loads((root / "state/settings.json").read_text())["enabled"])

    def test_bad_config_and_markers_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            for original in ('default_model = [broken', BEGIN + '\ndefault_model="test"\n'):
                config.write_text(original)
                with self.assertRaises(ValueError):
                    install(config, root / "state", text_only=True)
                self.assertEqual(config.read_text(), original)

    def test_new_config_generated_command_handles_spaces(self):
        with tempfile.TemporaryDirectory(prefix="sound of vibe ") as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            result = install(config, root / "state", language="en", text_only=True)
            parsed = tomllib.loads(config.read_text())
            self.assertEqual(len(parsed["hooks"]), len(HOOK_EVENTS))
            self.assertIn('"', parsed["hooks"][0]["command"])
            self.assertIsNone(result["backup"])
            self.assertEqual(status(config, root / "state")["language"], "en")


if __name__ == "__main__":
    unittest.main()
