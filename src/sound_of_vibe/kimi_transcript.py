"""Read only newly appended visible assistant commentary from Kimi's wire log."""

import os
import re
from pathlib import Path

from .adapters import as_text
from .models import Event
from .stream import JsonlDecoder


def find_wire(session: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session):
        return None
    home = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    return next((home / "sessions").glob(f"*/{session}/agents/main/wire.jsonl"), None)


class WireAdapter:
    """Kimi 2.1 wire journal: visible text only, excluding final/reasoning/tools."""

    def __init__(self):
        self.pending: Event | None = None

    def feed(self, record: dict) -> list[Event]:
        if record.get("type") in {"agent.turn.ended", "turn.ended", "turn.prompt"}:
            self.pending = None
            return []
        if record.get("type") != "agent.message.appended":
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
    def __init__(self, path: Path, offset: int):
        self.path = path
        self.offset = offset
        self.decoder = JsonlDecoder()
        self.adapter = WireAdapter()

    def poll(self) -> list[Event]:
        try:
            if self.path.stat().st_size < self.offset:
                # A rewritten/compacted journal is history, not new commentary.
                self.offset = self.path.stat().st_size
                self.decoder = JsonlDecoder()
                self.adapter = WireAdapter()
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
