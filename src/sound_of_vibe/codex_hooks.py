"""Silent Codex lifecycle hooks using the shared background narration worker."""

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

from .adapters import as_arguments
from .codex_transcript import find_rollout
from .kimi_hooks import HookQueue, launch_worker, load_settings, log, state_directory
from .rules import classify_tool, detect_language

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
               "PermissionRequest", "Stop", "Interrupt", "SessionEnd")


def codex_state() -> Path:
    return state_directory() / "Codex"


def normalize_hook(payload: dict) -> dict | None:
    kind, session = payload.get("hook_event_name"), payload.get("session_id")
    if kind not in HOOK_EVENTS or not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session):
        return None
    event = {"kind": "TurnStarted" if kind == "UserPromptSubmit" else kind,
             "session": session, "id": uuid.uuid4().hex, "stream_source": "codex"}
    if kind == "UserPromptSubmit":
        prompt = payload.get("prompt", "")
        event["language"] = detect_language(prompt) if isinstance(prompt, str) else None
        if isinstance(payload.get("turn_id"), (str, int)):
            event["turn"] = str(payload["turn_id"])[:256]
    if kind in {"PreToolUse", "PostToolUse"}:
        event["action"] = classify_tool(str(payload.get("tool_name", "")), as_arguments(payload.get("tool_input")))
        if isinstance(payload.get("tool_use_id"), str):
            event["id"] = payload["tool_use_id"][:256]
    return event


def receive(directory: Path, stream=None) -> int:
    if os.environ.get("SOUND_OF_VIBE_DISABLED", "").lower() in {"1", "true", "yes"}:
        return 0
    try:
        if not load_settings(directory)["enabled"]:
            return 0
        raw = (stream if stream is not None else sys.stdin.buffer).read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return 0
        payload = json.loads(raw)
        event = normalize_hook(payload) if isinstance(payload, dict) else None
        if event is None:
            return 0
        if event["kind"] in {"SessionStart", "TurnStarted"}:
            path = find_rollout(event["session"])
            if path is not None:
                event["wire_path"] = str(path)
                event["wire_offset"] = path.stat().st_size
            else:
                event["await_rollout"] = True
        queue = HookQueue(directory)
        queue.publish(event)
        token = queue.reserve()
        if token:
            launch_worker(queue, token)
    except Exception as error:
        try:
            log(directory, f"Codex hook skipped ({type(error).__name__}).")
        except Exception:
            pass
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("receive",), nargs="?", default="receive")
    parser.add_argument("--state-dir", type=Path, default=codex_state())
    args = parser.parse_args(argv)
    return receive(args.state_dir)


if __name__ == "__main__":
    raise SystemExit(main())
