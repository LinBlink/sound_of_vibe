"""Read only newly appended visible assistant commentary from Kimi's wire log."""

import os
import re
from pathlib import Path

from .adapters import as_text
from .models import Event
from .stream import JsonlDecoder
from .rules import asks_user


def find_wire(session: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session):
        return None
    home = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    return next((home / "sessions").glob(f"*/{session}/agents/main/wire.jsonl"), None)


class WireAdapter:
    """Kimi 2.1 wire journal: visible text only, excluding final/reasoning/tools."""

    def __init__(self):
        self.pending: Event | None = None
        self.live = False
        self.parts: list[str] = []
        self.step_id = ""

    def flush_parts(self) -> list[Event]:
        text = "".join(self.parts)
        self.parts.clear()
        return [Event("kimi-wire", self.step_id, "commentary", text)] if text else []

    def feed(self, record: dict) -> list[Event]:
        if record.get("type") in {"agent.turn.ended", "turn.ended", "turn.prompt"}:
            self.pending = None
            self.parts.clear()
            if record.get("type") == "turn.prompt":
                self.live = False
            return []
        if record.get("type") == "context.append_loop_event":
            event = record.get("event", {})
            if not isinstance(event, dict):
                return []
            self.live = True
            kind = event.get("type")
            if kind == "step.begin":
                self.parts.clear()
                self.step_id = str(event.get("uuid"))
            elif kind == "content.part":
                self.step_id = str(event.get("stepUuid") or self.step_id)
                part = event.get("part", {})
                if isinstance(part, dict) and part.get("type") in {"text", "output_text"}:
                    text = part.get("text")
                    if isinstance(text, str):
                        self.parts.append(text)
            elif kind == "tool.call":
                return self.flush_parts()
            elif kind == "step.end":
                if event.get("finishReason") in {"tool_calls", "tool_use"}:
                    return self.flush_parts()
                question = asks_user("".join(self.parts))
                self.parts.clear()  # Never narrate the final answer itself.
                if question:
                    return [Event("kimi-wire", self.step_id + ":ask", "ask", terminal=True)]
            return []
        if record.get("type") != "agent.message.appended":
            return []
        if self.live:
            # Persisted message snapshots repeat the live events at turn end.
            return []
        envelope = record.get("message", {})
        message, meta = envelope.get("message", {}), envelope.get("meta", {})
        role = message.get("role")
        if role == "tool":
            result = [self.pending] if self.pending else []
            self.pending = None
            return result
        if role != "assistant" or meta.get("source") != "llm":
            return []
        finish = meta.get("finish", {}).get("finishReason")
        if finish in {"completed", "stop", "end_turn"}:
            self.pending = None
            if asks_user(as_text(message.get("content"))):
                return [Event("kimi-wire", str(meta.get("messageId")) + ":ask", "ask", terminal=True)]
            return []
        # as_text accepts only text/output_text parts, never think or reasoning.
        text = as_text(message.get("content"))
        event = Event("kimi-wire", str(meta.get("messageId") or record.get("time")), "commentary", text)
        if message.get("toolCalls") or finish == "tool_calls":
            result = [self.pending] if self.pending else []
            self.pending = None
            if text:
                result.append(event)
            return result
        if text:
            self.pending = event
        return []


class WireTail:
    def __init__(self, path: Path, offset: int, adapter_factory=WireAdapter):
        self.path = path
        self.offset = offset
        self.decoder = JsonlDecoder()
        self.adapter_factory = adapter_factory
        self.adapter = adapter_factory()

    def poll(self) -> list[Event]:
        try:
            if self.path.stat().st_size < self.offset:
                # A rewritten/compacted journal is history, not new commentary.
                self.offset = self.path.stat().st_size
                self.decoder = JsonlDecoder()
                self.adapter = self.adapter_factory()
                return []
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read(256 * 1024)
                self.offset += len(chunk)
            events = []
            for record in self.decoder.feed(chunk):
                events.extend(self.adapter.feed(record))
            return events
        except (OSError, ValueError, TypeError, AttributeError):
            return []
