import unittest

from sound_of_vibe.adapters import CodexAdapter, KimiAdapter


class AdapterTests(unittest.TestCase):
    def test_codex_findings_between_tools_are_commentary_with_or_without_phase(self):
        for phase in (None, "commentary"):
            adapter = CodexAdapter()
            message = {"type": "item.completed", "item": {"id": "comment",
                       "type": "agent_message", "text": "54 tests passed. The playback order is fixed."}}
            if phase:
                message["item"]["phase"] = phase
            events = adapter.feed(message)
            if phase is None:
                events = adapter.feed({"type": "item.started", "item": {"id": "tool",
                                      "type": "command_execution", "command": "pytest"}})
            self.assertEqual(events[0].action, "commentary")
            self.assertIn("54 tests passed", events[0].text)

    def test_kimi_tool_messages_and_final_answer(self):
        adapter = KimiAdapter()
        events = adapter.feed({"role": "assistant", "content": "我先查看文件。", "tool_calls": [
            {"id": "call1", "function": {"name": "ReadFile", "arguments": '{"path":"x.py"}'}}]})
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].action, "read")
        self.assertEqual(adapter.feed({"role": "tool", "content": "I'm running tests."}), [])
        self.assertEqual(adapter.feed({"role": "assistant", "content": "I'll inspect it tomorrow."}), [])
        self.assertEqual(adapter.feed({"role": "meta", "type": "session.resume_hint"}), [])

    def test_kimi_intermediate_plain_message_is_released_on_tools(self):
        adapter = KimiAdapter()
        adapter.feed({"role": "assistant", "content": "I'll search the code."})
        events = adapter.feed({"role": "assistant", "tool_calls": [
            {"id": "call2", "function": {"name": "Bash", "arguments": '{"command":"rg TODO"}'}}]})
        self.assertEqual(events[0].text, "I'll search the code.")
        self.assertEqual(events[1].action, "search")

    def test_codex_phases_and_reasoning(self):
        adapter = CodexAdapter()
        self.assertEqual(adapter.feed({"type": "item.completed", "item": {
            "id": "1", "type": "reasoning", "text": "I'll inspect the code."}}), [])
        self.assertEqual(len(adapter.feed({"type": "item.completed", "item": {
            "id": "2", "type": "agent_message", "phase": "commentary", "text": "我先查看文件。"}})), 1)
        self.assertEqual(adapter.feed({"type": "item.completed", "item": {
            "id": "3", "type": "agent_message", "phase": "final_answer", "text": "I'll inspect later."}}), [])

    def test_codex_legacy_progress_and_final(self):
        adapter = CodexAdapter()
        adapter.feed({"type": "item.completed", "item": {
            "id": "1", "type": "agent_message", "text": "I'll run tests."}})
        events = adapter.feed({"type": "item.started", "item": {
            "id": "2", "type": "command_execution", "command": "pytest"}})
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].action, "test")
        adapter.feed({"type": "item.completed", "item": {
            "id": "3", "type": "agent_message", "text": "I'll run tests next time."}})
        adapter.feed({"type": "turn.completed"})
        self.assertIsNone(adapter.pending)

    def test_unknown_and_malformed_shapes(self):
        for adapter in (KimiAdapter(), CodexAdapter()):
            self.assertEqual(adapter.feed({"type": "future.event"}), [])
            self.assertEqual(adapter.feed({"type": "item.started", "item": []}), [])
        events = KimiAdapter().feed({"role": "assistant", "tool_calls": [
            None, {"id": "x", "function": {"name": "Bash", "arguments": "broken"}}]})
        self.assertEqual(events[0].action, "command")


if __name__ == "__main__":
    unittest.main()
