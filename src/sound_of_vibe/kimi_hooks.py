"""Silent Kimi hook receiver and a shared background audio worker.

The receiver stores only event types, IDs, language and classified actions.
Prompts, commands, tool inputs and outputs are never written to the queue.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from .adapters import as_arguments
from .models import Event, Narration
from .rules import Narrator, classify_tool, detect_language
from .speech import DEFAULT_VOICES, EdgeSpeech, Speaker

HOOK_EVENTS = ("SessionStart", "TurnStarted", "PreToolUse", "Stop", "StopFailure", "Interrupt", "SessionEnd")
QUEUE_LIMIT = 512
LEASE_SECONDS = 5
IDLE_SECONDS = 120


def state_directory() -> Path:
    override = os.environ.get("SOUND_OF_VIBE_STATE_DIR")
    if override:
        return Path(override)
    base = Path(os.environ["LOCALAPPDATA"]) if os.name == "nt" and "LOCALAPPDATA" in os.environ else Path.home() / ".local" / "state"
    return base / "SoundOfVibe"


def load_settings(directory: Path) -> dict:
    defaults = {"enabled": True, "language": "auto", "voices": DEFAULT_VOICES, "rate": "+10%"}
    path = directory / "settings.json"
    if path.exists():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    return defaults


def log(directory: Path, message: str):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "worker.log"
    # Bound logs without affecting the queue/lease transaction.
    if path.exists() and path.stat().st_size > 1024 * 1024:
        path.replace(directory / "worker.previous.log")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def normalize_hook(payload: dict) -> dict | None:
    kind = payload.get("hook_event_name")
    session = payload.get("session_id")
    if kind not in HOOK_EVENTS or not isinstance(session, str) or not session or len(session) > 256:
        return None
    # These are global CLI hooks, not a modification to web/desktop clients.
    if payload.get("client_type") not in {None, "kimi_code_cli", "cli"}:
        return None
    event = {"kind": kind, "session": session, "id": uuid.uuid4().hex}
    if kind == "TurnStarted":
        prompt = payload.get("prompt", "")
        event["language"] = detect_language(prompt) if isinstance(prompt, str) else None
        turn = payload.get("turn_id")
        if isinstance(turn, str):
            event["turn"] = turn[:256]
    elif kind == "PreToolUse":
        event["action"] = classify_tool(str(payload.get("tool_name", "")), as_arguments(payload.get("tool_input")))
        tool_id = payload.get("tool_call_id") or payload.get("tool_use_id")
        if isinstance(tool_id, str):
            event["id"] = tool_id[:256]
    return event


class HookQueue:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "events.sqlite3"
        with self.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL, expires REAL NOT NULL, pid INTEGER NOT NULL)")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=0.3)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def publish(self, event: dict):
        with self.connect() as connection:
            connection.execute("INSERT INTO events(payload) VALUES (?)", (json.dumps(event, ensure_ascii=False),))
            connection.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)", (QUEUE_LIMIT,))

    def reserve(self, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT expires FROM lease WHERE id=1").fetchone()
            if row and row[0] > now:
                return None
            token = uuid.uuid4().hex
            connection.execute("INSERT OR REPLACE INTO lease VALUES (1, ?, ?, 0)", (token, now + 10))
            return token

    def renew(self, token: str) -> bool:
        with self.connect() as connection:
            result = connection.execute("UPDATE lease SET expires=?, pid=? WHERE id=1 AND token=?",
                                        (time.time() + LEASE_SECONDS, os.getpid(), token))
            return result.rowcount == 1

    def release(self, token: str):
        with self.connect() as connection:
            connection.execute("DELETE FROM lease WHERE token=?", (token,))

    def take(self) -> list[dict]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT id,payload FROM events ORDER BY id LIMIT 128").fetchall()
            if rows:
                connection.execute("DELETE FROM events WHERE id <= ?", (rows[-1][0],))
            return [json.loads(payload) for _, payload in rows]


def launch_worker(queue: HookQueue, token: str):
    python = Path(sys.executable)
    if os.name == "nt" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        with (queue.directory / "launcher.log").open("ab") as errors:
            subprocess.Popen([str(python), "-m", "sound_of_vibe.kimi_hooks", "worker",
                              "--state-dir", str(queue.directory), "--token", token],
                             stdin=subprocess.DEVNULL, stdout=errors, stderr=errors,
                             cwd=queue.directory, **options)
    except Exception:
        queue.release(token)
        raise


def receive(directory: Path, stream=None) -> int:
    """Always silent and fail-open: never alter Kimi context or approval decisions."""
    if os.environ.get("SOUND_OF_VIBE_DISABLED", "").lower() in {"1", "true", "yes"}:
        return 0
    try:
        if not load_settings(directory)["enabled"]:
            return 0
        stream = stream if stream is not None else sys.stdin.buffer
        raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return 0
        payload = json.loads(raw)
        event = normalize_hook(payload) if isinstance(payload, dict) else None
        if event is None:
            return 0
        queue = HookQueue(directory)
        queue.publish(event)
        token = queue.reserve()
        if token:
            launch_worker(queue, token)
    except Exception as error:
        try:
            log(directory, f"Hook skipped ({type(error).__name__}).")
        except Exception:
            pass
    return 0


class LoggedSpeech(EdgeSpeech):
    def __init__(self, directory: Path, settings: dict):
        super().__init__(settings["voices"], settings["rate"])
        self.directory = directory

    async def speak(self, narration, stale):
        played = await super().speak(narration, stale)
        if played:
            log(self.directory, f"[audio/{narration.language}] Playback completed.")
        return played


class HookNarrator:
    def __init__(self, speaker: Speaker, language: str = "auto"):
        self.speaker = speaker
        self.language = language
        self.sessions: OrderedDict[str, Narrator] = OrderedDict()
        self.turns: dict[str, str] = {}
        self.current_session: str | None = None

    def new_narrator(self, language: str | None = None) -> Narrator:
        narrator = Narrator(language=self.language)
        if self.language == "auto" and language in {"zh", "en"}:
            narrator.language = language
        return narrator

    async def feed(self, event: dict):
        session, kind = event["session"], event["kind"]
        if kind == "SessionEnd":
            self.sessions.pop(session, None)
            self.turns.pop(session, None)
            return
        if kind == "Interrupt":
            if session == self.current_session:
                await self.speaker.interrupt()
            narrator = self.sessions.get(session)
            if narrator:
                narrator.ended = True
            return
        narrator = self.sessions.get(session)
        if kind == "TurnStarted":
            turn = event.get("turn", event["id"])
            if self.turns.get(session) == turn:
                return
            self.turns[session] = turn
            fallback = narrator.language if narrator else None
            narrator = self.new_narrator(event.get("language") or fallback)
            self.sessions[session] = narrator
        elif narrator is None:
            narrator = self.new_narrator()
            self.sessions[session] = narrator
            if kind == "SessionStart":
                self.current_session = session
                text = "旁白已开启" if narrator.language == "zh" else "Voice narration enabled"
                self.speaker.submit(Narration(text, narrator.language, "start"))
        if len(self.sessions) > 256:
            oldest, _ = self.sessions.popitem(last=False)
            self.turns.pop(oldest, None)
        if kind == "PreToolUse":
            source = Event("kimi-hook", event["id"], event["action"])
        elif kind in {"Stop", "StopFailure"}:
            source = Event("kimi-hook", event["id"], "failed" if kind == "StopFailure" else "complete", terminal=True)
        else:
            return
        for narration in narrator.consume(source):
            self.current_session = session
            self.speaker.submit(narration)


async def worker(directory: Path, token: str, idle_seconds: float = IDLE_SECONDS):
    queue = HookQueue(directory)
    if not queue.renew(token):
        return
    settings = load_settings(directory)
    backend = None if settings.get("text_only", False) else LoggedSpeech(directory, settings)
    speaker = Speaker(backend,
                      lambda item: log(directory, f"[voice/{item.language}] {item.text}"),
                      lambda message: log(directory, message), continuous=True)
    narrator = HookNarrator(speaker, settings["language"])
    last_event = time.monotonic()
    cancel = False
    log(directory, f"Worker started (pid={os.getpid()}).")
    try:
        while True:
            if not queue.renew(token) or not load_settings(directory)["enabled"]:
                cancel = True
                break
            events = queue.take()
            for event in events:
                last_event = time.monotonic()
                log(directory, f"[hook/{event['kind']}] session={event['session']}")
                await narrator.feed(event)
            if not events and time.monotonic() - last_event > idle_seconds:
                # Audio is normally already finished before the idle timeout.
                break
            await asyncio.sleep(0.15)
        await speaker.close(cancel=cancel)
    except BaseException:
        await speaker.close(cancel=True)
        raise
    finally:
        queue.release(token)
        log(directory, "Worker stopped.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("receive", "worker"))
    parser.add_argument("--state-dir", type=Path, default=state_directory())
    parser.add_argument("--token")
    args = parser.parse_args(argv)
    if args.mode == "receive":
        return receive(args.state_dir)
    if not args.token:
        parser.error("worker requires --token")
    try:
        asyncio.run(worker(args.state_dir, args.token))
    except Exception as error:
        log(args.state_dir, f"Worker failed ({type(error).__name__}).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
