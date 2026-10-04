"""Install/remove only the managed Kimi hook block, preserving other settings."""

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

from .kimi_hooks import HOOK_EVENTS, state_directory
from .speech import DEFAULT_VOICES

BEGIN = "# BEGIN sound-of-vibe managed hooks"
END = "# END sound-of-vibe managed hooks"


def config_path() -> Path:
    return Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code"))) / "config.toml"


def remove_block(content: str) -> str:
    begin_count, end_count = content.count(BEGIN), content.count(END)
    if not begin_count and not end_count:
        return content
    if begin_count != 1 or end_count != 1:
        raise ValueError("Malformed managed hook markers; refusing to change the config")
    start = content.index(BEGIN)
    end = content.index(END)
    if end < start:
        raise ValueError("Malformed managed hook block")
    end += len(END)
    if content[end:end + 2] == "\r\n":
        end += 2
    elif content[end:end + 1] == "\n":
        end += 1
    return content[:start] + content[end:]


def hook_command(python: Path, state: Path) -> str:
    arguments = [str(python.resolve()), "-m", "sound_of_vibe.kimi_hooks", "receive",
                 "--state-dir", str(state.resolve())]
    if os.name == "nt":
        # Hooks use cmd.exe on Windows. Reject cmd expansion characters rather
        # than trying to repair arbitrary executable/state paths.
        if any(re.search(r'[\r\n%!*&|<>^]', argument) for argument in arguments):
            raise ValueError("Hook paths contain unsupported Windows shell characters")
        return subprocess.list2cmdline(arguments)
    import shlex
    return shlex.join(arguments)


def managed_block(python: Path, state: Path) -> str:
    command = hook_command(python, state)
    lines = [BEGIN]
    for event in HOOK_EVENTS:
        lines.extend(["[[hooks]]", f"event = {json.dumps(event)}",
                      f"command = {json.dumps(command, ensure_ascii=False)}", "timeout = 3", ""])
    lines.append(END)
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        if path.exists():
            shutil.copymode(path, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(path.name + ".sound-of-vibe-" + time.strftime("%Y%m%d-%H%M%S")
                            + "-" + uuid.uuid4().hex[:6] + ".bak")
    shutil.copy2(path, backup)
    return backup


def install(config: Path | None = None, state: Path | None = None,
            language: str = "auto", voices: dict | None = None,
            rate: str = "+10%", python: Path | None = None, text_only: bool = False) -> dict:
    config = config or config_path()
    state = (state or state_directory()).resolve()
    python = python or Path(sys.executable)
    for module in ("edge_tts", "pygame"):
        if not text_only and importlib.util.find_spec(module) is None:
            raise ValueError(f"Missing {module}; install sound-of-vibe dependencies first")
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    if original:
        tomllib.loads(original)
    clean = remove_block(original)
    updated = clean.rstrip("\r\n") + "\n\n" + managed_block(python, state)
    parsed = tomllib.loads(updated)
    if len(parsed.get("hooks", [])) < len(HOOK_EVENTS):
        raise ValueError("Generated hook configuration is invalid")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = {"enabled": True, "language": language, "voices": voices or DEFAULT_VOICES,
                "rate": rate, "text_only": text_only}
    backup = backup_config(config)
    atomic_write(state / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    atomic_write(config, updated)
    return {"enabled": True, "config": str(config), "state": str(state),
            "backup": str(backup) if backup else None, "events": list(HOOK_EVENTS)}


def disable(config: Path | None = None, state: Path | None = None) -> dict:
    config = config or config_path()
    state = state or state_directory()
    backup = None
    if config.exists():
        original = config.read_text(encoding="utf-8")
        clean = remove_block(original)
        tomllib.loads(clean)
        if clean != original:
            backup = backup_config(config)
            atomic_write(config, clean)
    settings_path = state / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    settings["enabled"] = False
    atomic_write(settings_path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    return {"enabled": False, "config": str(config), "backup": str(backup) if backup else None}


def status(config: Path | None = None, state: Path | None = None) -> dict:
    config = config or config_path()
    state = state or state_directory()
    content = config.read_text(encoding="utf-8") if config.exists() else ""
    enabled = BEGIN in content and END in content
    settings_path = state / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    result = {"enabled": enabled and settings.get("enabled", False), "config": str(config),
              "state": str(state), "log": str(state / "worker.log"),
              "language": settings.get("language", "auto"), "worker_active": False}
    database = state / "events.sqlite3"
    if database.exists():
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.3)
        try:
            row = connection.execute("SELECT pid,expires FROM lease WHERE id=1").fetchone()
            if row:
                result["worker_pid"] = row[0]
                result["worker_active"] = row[1] > time.time()
        finally:
            connection.close()
    return result
