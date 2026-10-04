"""Adapters for Codex exec JSONL and Kimi Code 2.x stream-json."""

import hashlib
import json

from .models import Event
from .rules import classify_command, classify_tool


def as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(part.get("text", "") for part in value
                         if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
                         and isinstance(part.get("text"), str))
    return ""


def as_arguments(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, str) else {}
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


class KimiAdapter:
    source = "kimi"

    def __init__(self):
        self.pending: Event | None = None

    def feed(self, message: dict) -> list[Event]:
        role = message.get("role")
        calls = message.get("tool_calls")
        if role == "tool":
            # A tool result proves the preceding message was intermediate, but its
            # potentially huge content must never enter narration.
            result = [self.pending] if self.pending else []
            self.pending = None
            return result
        if role != "assistant":
            return []
        event_id = str(message.get("id") or hashlib.sha256(
            json.dumps(message, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        text = as_text(message.get("content"))
        result = []
        if isinstance(calls, list) and calls:
            if self.pending:
                result.append(self.pending)
                self.pending = None
            if text:
                result.append(Event(self.source, event_id + ":text", text=text))
            for index, call in enumerate(calls):
                if not isinstance(call, dict):
                    continue
                function = call.get("function", {})
                if not isinstance(function, dict):
                    continue
                action = classify_tool(str(function.get("name", "")), as_arguments(function.get("arguments")))
                result.append(Event(self.source, str(call.get("id") or event_id + f":tool:{index}"), action))
        elif text:
            # No phase/final marker in this protocol. Hold prose until later tool
            # activity proves it is progress; discard the last prose message at EOF.
            self.pending = Event(self.source, event_id + ":text", text=text)
        return result


class CodexAdapter:
    source = "codex"

    def __init__(self):
        self.pending: Event | None = None
        self.failed = False

    def feed(self, message: dict) -> list[Event]:
        kind = message.get("type")
        if kind in {"turn.failed", "error"}:
            self.failed = True
            self.pending = None
            return []
        if kind == "turn.completed":
            self.pending = None
            return []
        if kind not in {"item.started", "item.updated", "item.completed"}:
            return []
        item = message.get("item")
        if not isinstance(item, dict):
            return []
        item_id = str(item.get("id") or hashlib.sha256(
            json.dumps(item, sort_keys=True).encode()).hexdigest())
        item_type = item.get("type")
        if item_type == "agent_message" and kind == "item.completed":
            phase = item.get("phase")
            event = Event(self.source, item_id + ":text", text=as_text(item.get("text")))
            if phase == "final_answer":
                self.pending = None
                return []
            if phase == "commentary":
                return [event]
            self.pending = event
            return []
        actions = {"file_change": "edit", "web_search": "search", "mcp_tool_call": "tool"}
        action = classify_command(str(item.get("command", ""))) if item_type == "command_execution" else actions.get(item_type)
        if not action:
            return []
        result = [self.pending] if self.pending else []
        self.pending = None
        result.append(Event(self.source, item_id + ":action", action))
        return result


def adapter_for(source: str) -> KimiAdapter | CodexAdapter:
    return KimiAdapter() if source == "kimi" else CodexAdapter()
