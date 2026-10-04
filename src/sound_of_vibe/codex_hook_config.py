"""Install only owned Codex hooks, preserving other hooks and CLI preferences."""

import json
import importlib.util
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

from .codex_hooks import HOOK_EVENTS, codex_state
from .codex_transcript import codex_home
from .hook_config import atomic_write, backup_config, hook_command, daemon_status
from .speech import DEFAULT_VOICES

MARKER = "Sound of Vibe: voice narration"


def config_path() -> Path:
    return codex_home() / "hooks.json"


def owned(handler: dict) -> bool:
    return (handler.get("type") == "command" and handler.get("statusMessage") == MARKER
            and "-m sound_of_vibe.codex_hooks receive" in handler.get("command", ""))


def read_config(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"hooks": {}}
    if not isinstance(result, dict) or not isinstance(result.get("hooks", {}), dict):
        raise ValueError("Invalid Codex hooks.json; refusing to overwrite")
    for groups in result.get("hooks", {}).values():
        if not isinstance(groups, list):
            raise ValueError("Invalid Codex hook groups")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("Invalid Codex hook handlers")
            if not all(isinstance(handler, dict) for handler in group["hooks"]):
                raise ValueError("Invalid Codex hook handler")
            if any(handler.get("type") == "command" and not isinstance(handler.get("command"), str)
                   for handler in group["hooks"]):
                raise ValueError("Invalid Codex command hook")
    return result


def remove_owned(original: dict) -> dict:
    result = deepcopy(original)
    events = result.setdefault("hooks", {})
    for event, groups in list(events.items()):
        if not groups:
            continue
        kept = []
        for group in groups:
            handlers = [handler for handler in group["hooks"] if not owned(handler)]
            if handlers or not group["hooks"]:
                kept.append({**group, "hooks": handlers})
        if kept:
            events[event] = kept
        else:
            del events[event]
    return result


def install(config: Path | None = None, state: Path | None = None,
            language: str = "auto", voices: dict | None = None, rate: str = "+0%",
            python: Path | None = None, text_only: bool = False) -> dict:
    config = config or config_path()
    state = (state or codex_state()).resolve()
    for module in ("edge_tts", "pygame"):
        if not text_only and importlib.util.find_spec(module) is None:
            raise ValueError(f"Missing {module}; install sound-of-vibe dependencies first")
    preferences = config.parent / "config.toml"
    if preferences.exists():
        features = tomllib.loads(preferences.read_text(encoding="utf-8")).get("features", {})
        if features.get("hooks", features.get("codex_hooks", True)) is False:
            raise ValueError("Codex features.hooks is disabled in config.toml; enable it before installing")
    updated = remove_owned(read_config(config))
    command = hook_command(python or Path(sys.executable), state, "sound_of_vibe.codex_hooks", isolated=True)
    for event in HOOK_EVENTS:
        updated["hooks"].setdefault(event, []).append({"hooks": [{"type": "command", "command": command,
                                               "timeout": 3, "statusMessage": MARKER}]})
    previous = json.loads((state / "settings.json").read_text(encoding="utf-8")) if (state / "settings.json").exists() else {}
    settings = {**previous, "enabled": True, "language": language, "voices": voices or DEFAULT_VOICES,
                "rate": rate, "text_only": text_only, "active_idle_seconds": 600,
                "per_session_voice": previous.get("per_session_voice", True)}
    backup = backup_config(config)
    atomic_write(state / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    atomic_write(config, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    return {"enabled": True, "source": "codex", "config": str(config), "state": str(state),
            "backup": str(backup) if backup else None, "events": list(HOOK_EVENTS),
            "review": "Review and trust these voice hooks in Codex /hooks once."}


def disable(config: Path | None = None, state: Path | None = None) -> dict:
    config = config or config_path()
    state = state or codex_state()
    original = read_config(config)
    updated = remove_owned(original)
    backup = None
    if updated != original:
        backup = backup_config(config)
        atomic_write(config, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    path = state / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    settings["enabled"] = False
    atomic_write(path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    return {"enabled": False, "source": "codex", "config": str(config), "backup": str(backup) if backup else None}


def status(config: Path | None = None, state: Path | None = None) -> dict:
    config = config or config_path()
    state = state or codex_state()
    result = daemon_status(state)
    settings_path = state / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    handlers = [handler for groups in read_config(config).get("hooks", {}).values()
                for group in groups for handler in group["hooks"]]
    result.update({"source": "codex", "config": str(config),
                   "enabled": settings.get("enabled", False) and any(owned(handler) for handler in handlers)})
    return result
