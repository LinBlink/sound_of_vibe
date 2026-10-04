"""Codex rollout reader: intermediate assistant prose, never reasoning/results."""

import os
import re
from pathlib import Path

from .adapters import as_text
from .kimi_transcript import WireTail
from .models import Event
from .rules import asks_user


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def find_rollout(session: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session):
        return None
    return next((codex_home() / "sessions").glob(f"**/rollout-*{session}.jsonl"), None)


class RolloutAdapter:
    def __init__(self):
        self.pending: Event | None = None
        self.sequence = 0
        self.question = False

    def feed(self, record: dict) -> list[Event]:
        self.sequence += 1
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return []
        kind = payload.get("type")
        identity = str(payload.get("id") or f"{record.get('timestamp')}:{self.sequence}")
        if record.get("type") == "event_msg":
            if kind in {"task_started", "turn_aborted"}:
                self.pending = None
                self.question = False
            if kind == "task_complete":
                self.pending = None
                return [Event("codex-rollout", identity, "ask" if self.question else "complete", terminal=True)]
            return []
        if record.get("type") != "response_item":
            return []
        if kind == "message" and payload.get("role") == "assistant":
            phase = payload.get("phase")
            if phase == "final_answer":
                self.pending = None
                self.question = asks_user(as_text(payload.get("content")))
                return []
            event = Event("codex-rollout", identity, "commentary", as_text(payload.get("content")))
            if phase == "commentary":
                return [event] if event.text else []
            self.pending = event if event.text else None
        elif kind in {"function_call", "custom_tool_call"}:
            result = [self.pending] if self.pending else []
            self.pending = None
            return result
        return []


class RolloutTail(WireTail):
    def __init__(self, path: Path, offset: int):
        super().__init__(path, offset, RolloutAdapter)
