"""Agent process lifecycle and concurrent event/log readers."""

import asyncio
import codecs
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .adapters import adapter_for
from .models import Event
from .rules import Narrator
from .speech import Speaker
from .stream import JsonlDecoder


def build_command(source: str, executable: str, prompt: str, extra: list[str]) -> list[str]:
    reserved = {"--json", "--output-format", "--prompt", "-p", "--help", "-h", "--version", "-V"}
    if source == "codex":
        reserved |= {"--output-schema", "--output-last-message", "-o"}
    for token in extra:
        flag = token.split("=", 1)[0]
        if flag in reserved or (token.startswith("-p") and not token.startswith("--")):
            raise ValueError(f"{flag} is managed by sound-of-vibe and cannot be passed through")
    if source == "codex":
        # The task is passed via stdin, so a prompt beginning with '-' stays data.
        return [executable, "exec", "--json", *extra, "-"]
    return [executable, "--prompt", prompt, "--output-format", "stream-json", *extra]


def resolve_executable(source: str, explicit: str | None = None) -> str:
    executable = shutil.which(explicit or source)
    if not executable:
        raise FileNotFoundError(f"{explicit or source} was not found. Install/login to the CLI first or use --executable.")
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        # npm's Codex shim is a batch script. Run its JS entry directly through
        # Node so prompts and passthrough arguments never pass through cmd.exe.
        if source == "codex":
            script = Path(executable).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if script.is_file() and shutil.which("node"):
                return executable
        raise ValueError("A native executable is required; unsupported Windows batch shim.")
    return executable


def expand_windows_shim(command: list[str], source: str) -> list[str]:
    executable = command[0]
    if os.name == "nt" and source == "codex" and executable.lower().endswith((".cmd", ".bat")):
        script = Path(executable).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        return [shutil.which("node"), str(script), *command[1:]]
    return command


async def stop_process(process: asyncio.subprocess.Process):
    """Terminate the owned process tree, never unrelated agent sessions."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        await process.wait()


async def consume_process(command: list[str], source: str, prompt: str,
                          narrator: Narrator, speaker: Speaker, cwd: Path | None = None,
                          stdout=None, stderr=None, diagnostic=None) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    diagnostic = diagnostic or (lambda message: print(message, file=stderr, flush=True))
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, stdin=asyncio.subprocess.PIPE if source == "codex" else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options,
    )
    adapter = adapter_for(source)
    decoder = JsonlDecoder()

    def consume(messages):
        for message in messages:
            for event in adapter.feed(message):
                for narration in narrator.consume(event):
                    speaker.submit(narration)

    async def read_output():
        echo = codecs.getincrementaldecoder("utf-8")("replace")
        while chunk := await process.stdout.read(8192):
            stdout.write(echo.decode(chunk))
            stdout.flush()
            consume(decoder.feed(chunk))
        stdout.write(echo.decode(b"", final=True))
        stdout.flush()
        consume(decoder.finish())

    async def read_logs():
        echo = codecs.getincrementaldecoder("utf-8")("replace")
        while chunk := await process.stderr.read(8192):
            stderr.write(echo.decode(chunk))
            stderr.flush()
        stderr.write(echo.decode(b"", final=True))
        stderr.flush()

    async def write_prompt():
        if process.stdin is not None:
            try:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

    readers = [asyncio.create_task(read_output()), asyncio.create_task(read_logs()),
               asyncio.create_task(write_prompt())]
    try:
        await asyncio.gather(*readers)
        returncode = await process.wait()
        if decoder.invalid:
            diagnostic(f"Skipped {decoder.invalid} malformed/oversized JSONL line(s).")
        failed = returncode != 0 or getattr(adapter, "failed", False)
        for narration in narrator.consume(Event(source, "process:end", "failed" if failed else "complete", terminal=True)):
            speaker.submit(narration)
        await speaker.close()
        return returncode
    except BaseException:
        # Reader exceptions and Ctrl+C both need to reap the child and stop audio.
        await stop_process(process)
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await speaker.close(cancel=True)
        raise


async def replay(path: Path, source: str, narrator: Narrator, speaker: Speaker,
                 failed: bool = False) -> int:
    """Run captured JSONL through the real pipeline without launching an agent."""
    adapter = adapter_for(source)
    decoder = JsonlDecoder()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(8192):
                for message in decoder.feed(chunk):
                    for event in adapter.feed(message):
                        for narration in narrator.consume(event):
                            speaker.submit(narration)
                await asyncio.sleep(0)
        for message in decoder.finish():
            for event in adapter.feed(message):
                for narration in narrator.consume(event):
                    speaker.submit(narration)
        if decoder.invalid:
            speaker.diagnostic(f"Skipped {decoder.invalid} malformed/oversized JSONL line(s).")
        failed = failed or getattr(adapter, "failed", False)
        for narration in narrator.consume(Event(source, "process:end", "failed" if failed else "complete", terminal=True)):
            speaker.submit(narration)
        await speaker.close()
        return 1 if failed else 0
    except BaseException:
        await speaker.close(cancel=True)
        raise
