import asyncio
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sound_of_vibe.kimi_hooks import HookNarrator, HookQueue, normalize_hook, receive, worker
from sound_of_vibe.models import Narration
from sound_of_vibe.speech import Speaker


def hook(kind, session="session1", **extra):
    return {"hook_event_name": kind, "session_id": session, "client_type": "kimi_code_cli", **extra}


class HookTests(unittest.TestCase):
    def test_native_synthesis_stall_keeps_worker_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            queue = HookQueue(Path(temporary))
            token = queue.reserve(now=100)
            with patch('sound_of_vibe.kimi_hooks.time.time', return_value=101):
                self.assertTrue(queue.renew(token))
            self.assertIsNone(queue.reserve(now=120))
            self.assertIsNotNone(queue.reserve(now=162))

    def test_normalized_events_do_not_store_prompt_or_tool_data(self):
        prompt = "Check API_SECRET_DO_NOT_STORE in README.md"
        event = normalize_hook(hook("TurnStarted", prompt=prompt, turn_id="turn1"))
        self.assertEqual(event["language"], "en")
        self.assertNotIn("API_SECRET", json.dumps(event))
        event = normalize_hook(hook("PreToolUse", tool_name="Bash", tool_call_id="tool1",
                                    tool_input={"command": "python -m pytest", "secret": "DO_NOT_STORE"}))
        self.assertEqual(event["action"], "test")
        self.assertEqual(event["id"], "tool1")
        self.assertNotIn("DO_NOT_STORE", json.dumps(event))
        self.assertNotIn("pytest", json.dumps(event))

    def test_invalid_or_non_cli_events_are_ignored(self):
        self.assertIsNone(normalize_hook(hook("Unrecognized")))
        self.assertIsNone(normalize_hook(hook("PreToolUse", session="")))
        self.assertIsNone(normalize_hook(hook("SessionStart", client_type="web")))

    def test_receivers_are_silent_fail_open_and_launch_only_one_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("sound_of_vibe.kimi_hooks.launch_worker") as launch, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(receive(directory, io.BytesIO(b"invalid")), 0)
                payload = json.dumps(hook("SessionStart")).encode()
                self.assertEqual(receive(directory, io.BytesIO(payload)), 0)
                self.assertEqual(receive(directory, io.BytesIO(payload)), 0)
                launch.assert_called_once()
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(len(HookQueue(directory).take()), 2)

    def test_environment_and_settings_disable_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.dict(os.environ, {"SOUND_OF_VIBE_DISABLED": "1"}), patch("sound_of_vibe.kimi_hooks.launch_worker") as launch:
                self.assertEqual(receive(directory, io.BytesIO(json.dumps(hook("SessionStart")).encode())), 0)
                launch.assert_not_called()
            (directory / "settings.json").write_text('{"enabled":false}')
            self.assertEqual(receive(directory, io.BytesIO(b"{}")), 0)
            self.assertFalse((directory / "events.sqlite3").exists())

    def test_queue_is_bounded_and_consumed_in_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            queue = HookQueue(Path(temporary))
            for number in range(530):
                queue.publish({"number": number})
            events = []
            while batch := queue.take():
                events.extend(batch)
            self.assertEqual(len(events), 512)
            self.assertEqual([e["number"] for e in events], list(range(18, 530)))

    def test_concurrent_launch_reservation_and_expired_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            queue = HookQueue(Path(temporary))
            with ThreadPoolExecutor(max_workers=8) as executor:
                tokens = list(executor.map(lambda _: queue.reserve(now=100), range(8)))
            self.assertEqual(sum(token is not None for token in tokens), 1)
            old = next(token for token in tokens if token)
            new = queue.reserve(now=111)
            self.assertIsNotNone(new)
            self.assertFalse(queue.renew(old))
            self.assertTrue(queue.renew(new))
            queue.release(old)
            self.assertIsNone(queue.reserve())
            queue.release(new)
            self.assertIsNotNone(queue.reserve())


class HookAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_turns_bilingual_and_duplicate_completion(self):
        spoken = []
        speaker = Speaker(None, spoken.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker)
        events = [hook("TurnStarted", prompt="Inspect README", turn_id="1"),
                  hook("PreToolUse", tool_name="Read", tool_input={"path": "README.md"}),
                  hook("Stop"), hook("Stop"),
                  hook("TurnStarted", prompt="搜索代码", turn_id="2"),
                  hook("PreToolUse", tool_name="Grep", tool_input={}), hook("Stop")]
        for payload in events:
            await narrator.feed(normalize_hook(payload))
        await speaker.close()
        self.assertEqual([(n.language, n.action) for n in spoken],
                         [("en", "start"), ("en", "read"), ("en", "complete"),
                          ("zh", "start"), ("zh", "search"), ("zh", "complete")])

    async def test_interrupt_stops_current_and_next_turn_can_continue(self):
        output = []
        speaker = Speaker(None, output.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker)
        for event in [hook("TurnStarted", prompt="Check files", turn_id="1"),
                      hook("PreToolUse", tool_name="Read"), hook("Interrupt"),
                      hook("Stop"), hook("TurnStarted", prompt="Check files", turn_id="2"),
                      hook("PreToolUse", tool_name="Read"), hook("Stop")]:
            await narrator.feed(normalize_hook(event))
        await speaker.close()
        self.assertEqual([n.action for n in output], ["start", "read", "start", "read", "complete"])

    async def test_sessions_are_isolated_and_session_end_keeps_last_completion(self):
        output = []
        speaker = Speaker(None, output.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker)
        for payload in [hook("TurnStarted", "A", prompt="查看文件", turn_id="A1"),
                        hook("TurnStarted", "B", prompt="Inspect files", turn_id="B1"),
                        hook("PreToolUse", "A", tool_name="Read"),
                        hook("PreToolUse", "B", tool_name="Read"), hook("Stop", "B"), hook("SessionEnd", "B")]:
            await narrator.feed(normalize_hook(payload))
        await speaker.close()
        self.assertEqual([(n.language, n.action) for n in output],
                         [("zh", "start"), ("en", "start"), ("zh", "read"), ("en", "read"), ("en", "complete")])
        self.assertNotIn("B", narrator.sessions)

    async def test_repeated_tools_resume_after_five_seconds(self):
        now = [0]
        output = []
        speaker = Speaker(None, output.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker, clock=lambda: now[0])
        await narrator.feed(normalize_hook(hook("TurnStarted", prompt="查看文件", turn_id="1")))
        for timestamp in (0, 3, 6):
            now[0] = timestamp
            await narrator.feed(normalize_hook(hook("PreToolUse", tool_name="Read")))
        await speaker.close()
        self.assertEqual([n.action for n in output], ["start", "read", "read"])

    async def test_heartbeat_reports_work_and_approval_only_during_active_turn(self):
        now = [0]
        output = []
        speaker = Speaker(None, output.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker, clock=lambda: now[0])
        await narrator.feed(normalize_hook(hook("TurnStarted", prompt="Inspect files", turn_id="1")))
        now[0] = 12
        narrator.tick()
        await narrator.feed(normalize_hook(hook("PreToolUse", tool_name="Read")))
        now[0] = 24
        narrator.tick()
        await narrator.feed(normalize_hook(hook("PermissionRequest", tool_name="Read")))
        now[0] = 36
        narrator.tick()
        await narrator.feed(normalize_hook(hook("PermissionResult", tool_name="Read")))
        await narrator.feed(normalize_hook(hook("PostToolUse", tool_name="Read")))
        await narrator.feed(normalize_hook(hook("Stop")))
        now[0] = 100
        narrator.tick()
        await speaker.close()
        self.assertEqual([n.action for n in output], ["start", "working", "read", "tool_wait",
                         "permission", "permission_result", "read_result", "complete"])
        self.assertEqual(narrator.active, {})

    async def test_failed_tool_does_not_claim_success_and_interrupt_stops_reminders(self):
        now = [0]
        output = []
        speaker = Speaker(None, output.append, self.fail, continuous=True)
        narrator = HookNarrator(speaker, clock=lambda: now[0])
        for payload in [hook("TurnStarted", prompt="运行测试", turn_id="1"),
                        hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "pytest"}),
                        hook("Interrupt")]:
            await narrator.feed(normalize_hook(payload))
        now[0] = 100
        narrator.tick()
        await speaker.close()
        self.assertEqual([n.action for n in output], ["start", "tool_failed"])

    def test_result_normalization_ignores_tool_output(self):
        event = normalize_hook(hook("PostToolUse", tool_name="Read", tool_output="PRIVATE_RESULT"))
        self.assertEqual(event["action"], "read")
        self.assertNotIn("PRIVATE_RESULT", json.dumps(event))

    async def test_worker_drains_queue_releases_lease_and_logs_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "settings.json").write_text('{"enabled":true,"text_only":true}')
            queue = HookQueue(directory)
            for payload in [hook("TurnStarted", prompt="Inspect files", turn_id="1"),
                            hook("PreToolUse", tool_name="Read"), hook("Stop"), hook("SessionEnd")]:
                queue.publish(normalize_hook(payload))
            token = queue.reserve()
            await worker(directory, token, idle_seconds=0.01)
            output = (directory / "worker.log").read_text(encoding="utf-8")
            self.assertIn("[voice/en] Reading files", output)
            self.assertIn("[voice/en] Task completed", output)
            self.assertIn("Worker stopped", output)
            self.assertEqual(queue.take(), [])
            self.assertIsNotNone(queue.reserve())


if __name__ == "__main__":
    unittest.main()
