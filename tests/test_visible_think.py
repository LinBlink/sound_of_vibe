import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sound_of_vibe.adapters import CodexAdapter, KimiAdapter
from sound_of_vibe.codex_transcript import RolloutAdapter
from sound_of_vibe.kimi_transcript import WireAdapter
from sound_of_vibe.rules import Narrator
from sound_of_vibe.speech import Speaker

EXAMPLE = 'Good. FastAPI, httpx, uvicorn, pydantic all available. Python 3.14.3. Backend folder is empty, test folder is empty.'


class VisibleThinkTests(unittest.TestCase):
    def test_kimi_live_split_think_is_released_before_tools_without_snapshot_repeat(self):
        adapter = WireAdapter()
        def feed(event):
            return adapter.feed({'type': 'context.append_loop_event', 'event': event})
        feed({'type': 'step.begin', 'uuid': 'step'})
        feed({'type': 'content.part', 'part': {'type': 'think', 'think': EXAMPLE[:40]}})
        feed({'type': 'content.part', 'part': {'type': 'think', 'think': EXAMPLE[40:]}})
        events = feed({'type': 'tool.call'})
        self.assertEqual([event.text for event in events], [EXAMPLE])
        self.assertEqual(feed({'type': 'step.end', 'finishReason': 'tool_use'}), [])
        self.assertEqual(adapter.feed({'type': 'agent.message.appended', 'message': {'message': {
            'role': 'assistant', 'content': [{'type': 'think', 'think': EXAMPLE}]},
            'meta': {'source': 'llm', 'finish': {'finishReason': 'tool_calls'}}}}), [])

    def test_kimi_final_step_think_is_spoken_but_final_answer_is_not(self):
        adapter = WireAdapter()
        adapter.feed({'type': 'context.append_loop_event', 'event': {'type': 'step.begin', 'uuid': 's'}})
        adapter.feed({'type': 'context.append_loop_event', 'event': {'type': 'content.part', 'part': {'type': 'think', 'think': EXAMPLE}}})
        events = adapter.feed({'type': 'context.append_loop_event', 'event': {'type': 'step.end', 'finishReason': 'end_turn'}})
        self.assertEqual(events[0].text, EXAMPLE)
        self.assertFalse(events[0].terminal)

    def test_kimi_wrapper_and_persisted_visible_think(self):
        item = {'role': 'assistant', 'content': [{'type': 'think', 'think': EXAMPLE}, {'type': 'text', 'text': 'FINAL'}]}
        self.assertEqual(KimiAdapter().feed(item)[0].text, EXAMPLE)
        record = {'type': 'agent.message.appended', 'message': {'message': item, 'meta': {
            'source': 'llm', 'messageId': 'm', 'finish': {'finishReason': 'completed'}}}}
        self.assertEqual([event.text for event in WireAdapter().feed(record)], [EXAMPLE])
        self.assertEqual(KimiAdapter().feed({**item, 'role': 'user'}), [])

    def test_codex_public_summary_deduplicates_alternate_records_and_excludes_opaque_data(self):
        adapter = RolloutAdapter()
        record = {'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': [
            {'type': 'summary_text', 'text': EXAMPLE}, {'type': 'summary_text', 'text': 'Next I will check the frontend.'}], 'encrypted_content': 'OPAQUE',
            'content': [{'type': 'reasoning_text', 'text': 'UNPUBLISHED_RAW'}]}}
        events = adapter.feed(record)
        self.assertEqual([event.text for event in events], [EXAMPLE, 'Next I will check the frontend.'])
        self.assertEqual(adapter.feed({'type': 'event_msg', 'payload': {'type': 'agent_reasoning', 'text': EXAMPLE}}), [])
        self.assertEqual(adapter.feed({'type': 'event_msg', 'payload': {'type': 'agent_reasoning', 'text': 'Next I will check the frontend.'}}), [])
        self.assertEqual(adapter.feed({'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
            'type': 'reasoning', 'summary_text': [EXAMPLE], 'raw_content': ['UNPUBLISHED_RAW']}}}), [])
        self.assertEqual(adapter.feed({'type': 'event_msg', 'payload': {'type': 'agent_reasoning_raw_content', 'text': 'UNPUBLISHED_RAW'}}), [])
        adapter.feed({'type': 'event_msg', 'payload': {'type': 'task_started'}})
        self.assertEqual(len(adapter.feed(record)), 2)
        record['payload']['summary'] = []
        self.assertEqual(adapter.feed(record), [])

    def test_codex_completed_summary_only_not_partial_cumulative_updates(self):
        adapter = CodexAdapter()
        item = {'id': 'think', 'type': 'reasoning', 'text': EXAMPLE}
        self.assertEqual(adapter.feed({'type': 'item.updated', 'item': item}), [])
        self.assertEqual(adapter.feed({'type': 'item.completed', 'item': item})[0].text, EXAMPLE)


class VisibleThinkAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_visible_think_keeps_all_chunks_ahead_of_completion(self):
        from sound_of_vibe.models import Narration
        started, release = asyncio.Event(), asyncio.Event()
        spoken = []
        async def speak(item, stale):
            spoken.append(item.text)
            if len(spoken) == 1:
                started.set()
                await release.wait()
        backend = SimpleNamespace(initialize=AsyncMock(), speak=speak, close=Mock())
        speaker = Speaker(backend, lambda _: None, self.fail, interval=0, continuous=True,
                          preserve_progress=True, prefer_commentary=True, max_pending=6)
        speaker.submit(Narration('First thought.', 'en', 'commentary'))
        await started.wait()
        for number in range(20):
            speaker.submit(Narration(f'Finding {number}.', 'en', 'commentary'))
        speaker.submit(Narration('Tool finished.', 'en', 'tool_result'))
        speaker.submit(Narration('Done.', 'en', 'complete', True))
        release.set()
        await speaker.close()
        self.assertEqual(spoken, ['First thought.'] + [f'Finding {n}.' for n in range(20)] + ['Done.'])

    async def test_reported_findings_all_reach_audio_before_completion(self):
        backend = SimpleNamespace(initialize=AsyncMock(), speak=AsyncMock(), close=Mock())
        speaker = Speaker(backend, lambda _: None, self.fail, interval=0, continuous=True,
                          preserve_progress=True, prefer_commentary=True, max_pending=6)
        adapter = CodexAdapter()
        events = adapter.feed({'type': 'item.completed', 'item': {'id': 'think', 'type': 'reasoning', 'text': EXAMPLE}})
        narrator = Narrator()
        for event in events:
            for sentence in narrator.consume(event):
                speaker.submit(sentence)
        from sound_of_vibe.models import Event
        for sentence in narrator.consume(Event('codex', 'done', 'complete', terminal=True)):
            speaker.submit(sentence)
        await speaker.close()
        utterances = [call.args[0] for call in backend.speak.call_args_list]
        self.assertEqual(' '.join(item.text for item in utterances[:-1]), EXAMPLE)
        self.assertEqual(utterances[-1].action, 'complete')


if __name__ == '__main__':
    unittest.main()
