import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sound_of_vibe.codex_hook_config import install, disable, status, MARKER
from sound_of_vibe.codex_hooks import normalize_hook, receive, HOOK_EVENTS
from sound_of_vibe.codex_transcript import RolloutAdapter, RolloutTail
from sound_of_vibe.kimi_hooks import HookNarrator, HookQueue
from sound_of_vibe.speech import Speaker


def record(text, phase="commentary", role="assistant"):
    return {"type": "response_item", "timestamp": "test", "payload": {
        "type": "message", "role": role, "phase": phase,
        "content": [{"type": "output_text", "text": text}]}}


class CodexHookTests(unittest.TestCase):
    def test_isolated_receiver_ignores_same_named_package_in_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sound_of_vibe").mkdir()
            (root / "sound_of_vibe/__init__.py").write_text("raise RuntimeError('wrong project import')")
            result = subprocess.run([sys.executable, "-I", "-m", "sound_of_vibe.codex_hooks", "--help"],
                                    cwd=root, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--state-dir", result.stdout)

    def test_normalizer_stores_language_and_action_without_raw_inputs(self):
        event = normalize_hook({"hook_event_name": "UserPromptSubmit", "session_id": "thread1",
                                "turn_id": "turn1", "prompt": "查看 PRIVATE_SECRET"})
        self.assertEqual(event["kind"], "TurnStarted")
        self.assertEqual(event["language"], "zh")
        self.assertNotIn("PRIVATE_SECRET", json.dumps(event))
        event = normalize_hook({"hook_event_name": "PreToolUse", "session_id": "thread1",
                                "tool_name": "Bash", "tool_input": {"command": "pytest SECRET"}})
        self.assertEqual(event["action"], "test")
        self.assertNotIn("SECRET", json.dumps(event))
        self.assertIsNone(normalize_hook({"hook_event_name": "SessionStart", "session_id": "../../escape"}))

    def test_receiver_silent_fail_open_single_worker_and_wrapper_suppression(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "thread1"}).encode()
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                    patch("sound_of_vibe.codex_hooks.find_rollout", return_value=None), \
                    patch("sound_of_vibe.codex_hooks.launch_worker") as launch:
                self.assertEqual(receive(root, io.BytesIO(b"invalid")), 0)
                receive(root, io.BytesIO(payload))
                receive(root, io.BytesIO(payload))
                launch.assert_called_once()
                with patch.dict(os.environ, {"SOUND_OF_VIBE_DISABLED": "1"}):
                    receive(root, io.BytesIO(payload))
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "")
            self.assertEqual(len(HookQueue(root).take()), 2)

    def test_rollout_filters_final_reasoning_tool_results_and_duplicate_events(self):
        adapter = RolloutAdapter()
        self.assertEqual(adapter.feed(record("FINAL", "final_answer")), [])
        self.assertEqual(adapter.feed(record("USER", role="user")), [])
        for kind in ("reasoning", "function_call_output", "custom_tool_call_output"):
            self.assertEqual(adapter.feed({"type": "response_item", "payload": {"type": kind, "output": "SECRET"}}), [])
        self.assertEqual(adapter.feed({"type": "event_msg", "payload": {"type": "agent_message", "message": "DUPLICATE"}}), [])
        self.assertEqual(adapter.feed(record("54 tests passed."))[0].action, "commentary")
        self.assertEqual(adapter.feed(record("Checking the results.", phase=None)), [])
        events = adapter.feed({"type": "response_item", "payload": {"type": "function_call"}})
        self.assertEqual(events[0].text, "Checking the results.")

    def test_install_preserves_other_hooks_is_idempotent_and_disable_is_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "hooks.json"
            original = {"description": "keep", "hooks": {"Stop": [{"matcher": "", "hooks": [
                {"type": "command", "command": "echo keep"}]}], "PreCompact": []}}
            path.write_text(json.dumps(original), encoding="utf-8")
            first = install(path, root / "codex-state", text_only=True)
            self.assertEqual(json.loads(Path(first["backup"]).read_text()), original)
            install(path, root / "codex-state", text_only=True)
            current = json.loads(path.read_text())
            self.assertEqual(current["description"], "keep")
            self.assertEqual(current["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
            count = sum(handler.get("statusMessage") == MARKER for groups in current["hooks"].values()
                        for group in groups for handler in group["hooks"])
            self.assertEqual(count, len(HOOK_EVENTS))
            self.assertTrue(status(path, root / "codex-state")["enabled"])
            disable(path, root / "codex-state")
            self.assertEqual(json.loads(path.read_text()), original)
            self.assertFalse(status(path, root / "codex-state")["enabled"])

    def test_bad_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "hooks.json"
            for text in ("broken", '{"hooks": []}', '{"hooks":{"Stop":[{"hooks":[42]}]}}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    install(path, root / "state", text_only=True)
                self.assertEqual(path.read_text(), text)

    def test_cursor_skips_history_and_handles_partial_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rollout.jsonl"
            path.write_text(json.dumps(record("OLD")) + "\n")
            tail = RolloutTail(path, path.stat().st_size)
            payload = (json.dumps(record("修复后，54 项测试通过。"), ensure_ascii=False) + "\n").encode()
            with path.open("ab") as stream:
                stream.write(payload[:-1])
            self.assertEqual(tail.poll(), [])
            with path.open("ab") as stream:
                stream.write(payload[-1:])
            self.assertEqual(tail.poll()[0].text, "修复后，54 项测试通过。")
            self.assertEqual(tail.poll(), [])


class CodexAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_worker_reads_bilingual_prose_and_only_one_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rollout.jsonl"
            path.write_text("")
            output = []
            speaker = Speaker(None, output.append, self.fail, continuous=True)
            narrator = HookNarrator(speaker)
            for number, text in enumerate(("54 tests passed. Next I will verify playback.", "修复后，54 项测试通过。接下来验证播放。")):
                event = {"kind": "TurnStarted", "session": "test", "id": str(number), "turn": str(number),
                         "stream_source": "codex", "wire_path": str(path), "wire_offset": path.stat().st_size}
                await narrator.feed(event)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record(text), ensure_ascii=False) + "\n")
                    stream.write(json.dumps(record("FINAL", "final_answer")) + "\n")
                    stream.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
                narrator.poll_text()
                await narrator.feed({"kind": "Stop", "session": "test", "id": "stop" + str(number)})
            await speaker.close()
            self.assertEqual(sum(item.action == "complete" for item in output), 2)
            self.assertTrue(any(item.action == "commentary" and item.language == "en" for item in output))
            self.assertTrue(any(item.action == "commentary" and item.language == "zh" for item in output))
            self.assertFalse(any("FINAL" in item.text for item in output))
