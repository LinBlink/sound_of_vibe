import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from sound_of_vibe.hook_config import BEGIN, END, atomic_write, disable, install, remove_block, status
from sound_of_vibe.kimi_hooks import HOOK_EVENTS


class ConfigTests(unittest.TestCase):
    def test_atomic_write_retries_windows_sharing_error_without_losing_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "config.toml"
            target.write_text("original")
            replace = Path.replace
            calls = []

            def briefly_locked(source, destination):
                calls.append(source)
                if len(calls) == 1:
                    self.assertEqual(target.read_text(), "original")
                    error = PermissionError("Sharing violation")
                    error.winerror = 5
                    raise error
                return replace(source, destination)

            with patch.object(Path, "replace", briefly_locked), patch("sound_of_vibe.hook_config.time.sleep"):
                atomic_write(target, "updated")
            self.assertEqual(target.read_text(), "updated")
            self.assertEqual(len(calls), 2)
            self.assertEqual(list(target.parent.glob("*.tmp-*")), [])

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
