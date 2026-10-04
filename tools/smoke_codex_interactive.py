"""Windows smoke test: plain Codex, trusted global hooks, bilingual real prose.

Requires optional pywinpty, Codex login, installed/trusted voice hooks. This tool
never trusts hooks; review hooks through the original Codex /hooks UI first.
"""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from sound_of_vibe.codex_hooks import codex_state
from sound_of_vibe.codex_transcript import find_rollout, RolloutTail
from sound_of_vibe.rules import commentary_sentences


def main():
    if os.name != "nt":
        raise SystemExit("Windows is required")
    from winpty import PtyProcess
    executable = shutil.which("codex")
    if not executable or not executable.lower().endswith(".exe"):
        raise SystemExit("Native Codex executable is required")
    logfile = codex_state() / "worker.log"
    baseline = logfile.stat().st_size if logfile.exists() else 0
    chunks = deque(maxlen=1024)

    def transcript():
        text = "".join(chunks)
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        return re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)

    def new_log():
        if not logfile.exists():
            return ""
        with logfile.open("rb") as stream:
            stream.seek(baseline)
            return stream.read().decode("utf-8", errors="replace")

    def wait_for(predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        raise TimeoutError("Codex interactive condition was not reached")

    with tempfile.TemporaryDirectory(prefix="sound-of-vibe-codex-") as temporary:
        root = Path(temporary).resolve()
        if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError("Temporary test project is outside the expected directory")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for language in ("en", "zh"):
            (root / f"README-{language}.md").write_text(f"{language} smoke test. Version: 0.1.0.\n", encoding="utf-8")
        process = PtyProcess.spawn([executable], cwd=str(root), dimensions=(35, 120))

        def reader():
            try:
                while process.isalive():
                    chunk = process.read()
                    chunks.append(chunk)
                    if "\x1b[c" in chunk:
                        process.write("\x1b[?1;2c")
                    if "\x1b[6n" in chunk:
                        process.write("\x1b[1;1R")
                    if "\x1b]11;?" in chunk:
                        process.write("\x1b]11;rgb:0000/0000/0000\x1b\\")
            except (EOFError, OSError):
                pass

        threading.Thread(target=reader, daemon=True).start()
        try:
            wait_for(lambda: "Trust and continue" in transcript()
                     or re.search(r"Context\s*\d+% used", transcript()), timeout=20)
            # The initial frame can render the composer before the folder trust
            # dialog. Let startup finish before deciding which UI is active.
            time.sleep(1)
            if "Trust and continue" in transcript():
                # This directory only contains the test files we just created.
                chunks.clear()
                process.write("\r")
                wait_for(lambda: re.search(r"Context\s*\d+% used", transcript()), timeout=20)
            time.sleep(1)
            if "hooks need review" in transcript().lower() or "trust all and continue" in transcript().lower():
                raise RuntimeError("Review and trust the voice hooks through Codex /hooks first")
            print("Plain codex launched in a separate project, using persisted hook trust.", flush=True)
            session = None
            prompts = [
                ("en", "Read only README-en.md with a read-only shell command. Before the tool call, explain your plan in English visible commentary, including how you will verify the version. Do not modify files. The final answer must only be the version number."),
                ("zh", "只用只读命令读取尚未查看过的 README-zh.md。调用工具前，用中文在可见进展说明中解释你将如何读取和验证版本。不要修改任何文件。最后只回答版本号。"),
            ]
            for language, prompt in prompts:
                log_offset = len(new_log())
                wire = find_rollout(session) if session else None
                offset = wire.stat().st_size if wire else 0
                process.write("\x1b[200~" + prompt + "\x1b[201~")
                # Native Codex protects against Enter in the paste input burst.
                time.sleep(.4)
                process.write("\r")
                wait_for(lambda: "[hook/TurnStarted]" in new_log()[log_offset:])
                match = re.search(r"\[hook/TurnStarted\] session=(\S+)", new_log()[log_offset:])
                identity = match.group(1)
                if session is not None and identity != session:
                    raise AssertionError("Turns did not use the same session")
                session = identity
                marker = f"[audio/{language}] Playback completed. action=complete session={session}"
                wait_for(lambda: marker in new_log()[log_offset:], timeout=90)
                wire = find_rollout(session)
                expected = [sentence for event in RolloutTail(wire, offset).poll() if not event.terminal
                            for sentence in commentary_sentences(event.text)]
                if not expected:
                    raise AssertionError("No visible intermediate assistant text")
                output = new_log()[log_offset:]
                for sentence in expected:
                    if f"[voice/{language}] {sentence.text} session={session}" not in output:
                        raise AssertionError("Actual assistant explanation was lost")
                audio_marker = f"[audio/{language}] Playback completed. action=commentary session={session}"
                if output.count(audio_marker) != len(expected):
                    raise AssertionError("Not all explanation sentences completed playback")
                if output.rfind(audio_marker) > output.index(marker):
                    raise AssertionError("Completion overtook assistant commentary")
                print(f"Passed: {language} actual commentary + audio, session={session}", flush=True)
            log_offset = len(new_log())
            question_prompt = "不要执行工具。只问我一句中文问题，询问应选择中文音色还是英文音色，并以问号结束。等待我的回答。"
            process.write("\x1b[200~" + question_prompt + "\x1b[201~")
            time.sleep(.4)
            process.write("\r")
            question_marker = f"[audio/zh] Playback completed. action=ask session={session}"
            wait_for(lambda: question_marker in new_log()[log_offset:], timeout=90)
            output = new_log()[log_offset:]
            if output.count(question_marker) != 1 or "Playback completed. action=complete" in output:
                raise AssertionError("Question must play one question chime without a completion chime")
            print("Passed: visible question played the distinct answer-needed chime.", flush=True)
            process.write("/exit")
            time.sleep(.4)
            process.write("\r")
            wait_for(lambda: not process.isalive(), timeout=10)
            print("Codex exited normally.", flush=True)
        except BaseException:
            print(repr(transcript()[-1600:]), flush=True)
            raise
        finally:
            process.close(force=True)


if __name__ == "__main__":
    main()
