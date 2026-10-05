import json
import tempfile
import unittest
from pathlib import Path

from sound_of_vibe.kimi_transcript import WireAdapter, WireTail
from sound_of_vibe.models import Event
from sound_of_vibe.rules import Narrator, commentary_sentences


EXPLANATION = "修复后，执行旁白不会再被结束提示覆盖，同类动作的去重时间降到了 5 秒。54 项测试通过；真实联调又发现了完成提示抢在工具结果前播放的问题，已修正并补上回归测试，现在共 55 项通过。接下来验证中英文按顺序播放，以及长任务期间的提示。"


def record(text, final=False, role="assistant", identifier="one"):
    return {"type": "agent.message.appended", "message": {
        "message": {"role": role, "content": [{"type": "text", "text": text}], "toolCalls": [] if final else [{"name": "Read"}]},
        "meta": {"source": "llm", "messageId": identifier,
                 "finish": {"finishReason": "completed" if final else "tool_calls"}}}}


class TranscriptTests(unittest.TestCase):
    def test_inline_file_names_remain_readable_in_actual_explanation(self):
        result = commentary_sentences("我会读取 `README.md`，然后检查版本号。")
        self.assertEqual(result[0].text, "我会读取 README.md，然后检查版本号。")

    def test_live_prose_is_released_at_tool_call_before_turn_end(self):
        adapter = WireAdapter()
        def live(event):
            return adapter.feed({"type": "context.append_loop_event", "event": event})
        self.assertEqual(live({"type": "step.begin", "uuid": "step1"}), [])
        self.assertEqual(live({"type": "content.part", "part": {"type": "think", "think": "Visible CLI think."}}), [])
        self.assertEqual(live({"type": "content.part", "part": {"type": "text", "text": EXPLANATION}})[0].text, "Visible CLI think.")
        self.assertEqual([event.text for event in live({"type": "tool.call"})], [EXPLANATION])
        self.assertEqual(live({"type": "step.end", "finishReason": "tool_use"}), [])
        self.assertEqual(adapter.feed(record(EXPLANATION)), [])
        live({"type": "step.begin", "uuid": "step2"})
        live({"type": "content.part", "part": {"type": "text", "text": "FINAL_ANSWER"}})
        self.assertEqual(live({"type": "step.end", "finishReason": "end_turn"}), [])

    def test_user_example_is_spoken_in_full_including_completed_findings(self):
        narrator = Narrator()
        spoken = narrator.consume(Event("kimi-wire", "one", "commentary", EXPLANATION))
        self.assertEqual("".join(item.text for item in spoken), EXPLANATION)
        self.assertTrue(all(item.action == "commentary" and item.language == "zh" for item in spoken))

    def test_distinct_findings_are_not_deduplicated_by_action(self):
        narrator = Narrator()
        first = narrator.consume(Event("codex", "1", "commentary", "54 tests passed. Next, I will inspect playback."))
        second = narrator.consume(Event("codex", "2", "commentary", "55 tests passed. The playback order is fixed."))
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertTrue(all(item.language == "en" for item in first + second))

    def test_filters_diff_traceback_fences_and_inline_code(self):
        text = '• Edited tools/test.py (+21 -3)\n+import argparse\n```python\nimport argparse\n```\n' + EXPLANATION + '\nTraceback (most recent call last):\n    raise AssertionError("failed")\n'
        self.assertEqual("".join(item.text for item in commentary_sentences(text)), EXPLANATION)

    def test_wire_ignores_tools_users_and_final_answer(self):
        adapter = WireAdapter()
        self.assertEqual(adapter.feed(record("TOOL_OUTPUT", role="tool")), [])
        self.assertEqual(adapter.feed(record("USER_PROMPT", role="user")), [])
        self.assertEqual(adapter.feed(record("FINAL_TEXT", final=True)), [])
        events = adapter.feed(record(EXPLANATION))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].text, EXPLANATION)
        self.assertNotIn("DO_NOT_SPEAK", events[0].text)

    def test_tail_starts_at_cursor_and_waits_for_complete_utf8_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wire.jsonl"
            path.write_text(json.dumps(record("OLD_HISTORY")) + "\n", encoding="utf-8")
            tail = WireTail(path, path.stat().st_size)
            self.assertEqual(tail.poll(), [])
            payload = (json.dumps(record(EXPLANATION), ensure_ascii=False) + "\n").encode()
            with path.open("ab") as stream:
                stream.write(payload[:-2])
            self.assertEqual(tail.poll(), [])
            with path.open("ab") as stream:
                stream.write(payload[-2:])
            self.assertEqual([event.text for event in tail.poll()], [EXPLANATION])
            self.assertEqual(tail.poll(), [])
            path.write_text("", encoding="utf-8")
            self.assertEqual(tail.poll(), [])


if __name__ == "__main__":
    unittest.main()
