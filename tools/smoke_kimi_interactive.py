"""Manual Windows smoke test of plain `kimi` in an unrelated temporary project.

Requires the optional test tool `pip install pywinpty`, Kimi login, and installed
global hooks. Runs two read-only requests through the original interactive TUI.
"""

import os
import re
import shutil
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from sound_of_vibe.kimi_hooks import state_directory


def main():
    if os.name != "nt":
        raise SystemExit("This interactive smoke test requires Windows.")
    from winpty import PtyProcess

    executable = shutil.which("kimi")
    if not executable:
        raise SystemExit("kimi was not found in PATH")
    logfile = state_directory() / "worker.log"
    baseline = logfile.stat().st_size if logfile.exists() else 0
    chunks = deque(maxlen=512)

    def new_log():
        if not logfile.exists():
            return ""
        with logfile.open("rb") as stream:
            stream.seek(baseline)
            return stream.read().decode("utf-8", errors="replace")

    def wait_for(predicate, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise TimeoutError("Interactive smoke condition was not reached")

    with tempfile.TemporaryDirectory(prefix="sound-of-vibe-interactive-") as temporary:
        root = Path(temporary)
        (root / "README.md").write_text("Global voice smoke test. Version: 0.1.0.\n", encoding="utf-8")
        process = PtyProcess.spawn([executable], cwd=str(root), dimensions=(32, 110))

        def read_terminal():
            try:
                while process.isalive():
                    chunk = process.read()
                    chunks.append(chunk)
                    # Answer terminal queries that a real terminal answers itself.
                    if "\x1b[c" in chunk:
                        process.write("\x1b[?1;2c")
                    if "\x1b[6n" in chunk:
                        process.write("\x1b[1;1R")
                    if "\x1b]11;?" in chunk:
                        process.write("\x1b]11;rgb:0000/0000/0000\x1b\\")
            except (EOFError, OSError):
                pass

        threading.Thread(target=read_terminal, daemon=True).start()
        try:
            wait_for(lambda: any(text in "".join(chunks) for text in
                                  ("Welcome to Kimi Code", "Trust this folder?")), timeout=15)
            if "Trust this folder?" in "".join(chunks):
                # Only this freshly created directory containing our README is
                # trusted. There are no project MCP configurations to launch.
                process.write("\r")
            wait_for(lambda: "Welcome to Kimi Code" in "".join(chunks), timeout=10)
            print("Original interactive kimi started in an unrelated project.", flush=True)
            prompts = [
                ("en", "Read only README.md using the Read tool. Do not write files or use shell commands. Answer only the version number."),
                ("zh", "只用 Read 工具读取 README.md，不修改文件，不执行命令，最后只回答版本号。"),
            ]
            previous_completions = 0
            session = None
            for language, prompt in prompts:
                turn_log_offset = len(new_log())
                process.write("\x1b[200~" + prompt + "\x1b[201~\r")
                wait_for(lambda: new_log().count("[hook/Stop]") > previous_completions)
                log = new_log()
                sessions = re.findall(r"\[hook/SessionStart\] session=(\S+)", log)
                if not sessions:
                    raise AssertionError("No SessionStart event from the interactive session")
                if session is not None and sessions[-1] != session:
                    raise AssertionError("The two turns did not use the same session")
                session = sessions[-1]
                text = "Reading files" if language == "en" else "正在查看文件"
                if f"[voice/{language}] {text}" not in log:
                    raise AssertionError(f"Missing {language} tool narration")
                # Require playback of the actual action, its result, and the
                # completion; a greeting/completion alone is insufficient.
                for action in ("start", "read", "read_result", "complete"):
                    marker = f"[audio/{language}] Playback completed. action={action}"
                    wait_for(lambda marker=marker: marker in new_log()[turn_log_offset:])
                previous_completions = new_log().count("[hook/Stop]")
                print(f"Passed: {language} start + read + result + completion playback, session={session}", flush=True)
            process.write("/exit\r")
            wait_for(lambda: not process.isalive(), timeout=5)
            print("Interactive kimi exited normally.", flush=True)
        except BaseException:
            # Only the disposable test transcript is displayed on failure.
            print(repr("".join(chunks)[-1800:]), flush=True)
            raise
        finally:
            process.close(force=True)


if __name__ == "__main__":
    main()
