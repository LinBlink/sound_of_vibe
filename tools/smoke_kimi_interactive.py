"""Manual Windows smoke test of plain `kimi` in an unrelated temporary project.

Requires the optional test tool `pip install pywinpty`, Kimi login, and installed
global hooks. Runs two read-only requests through the original interactive TUI.
"""

import os
import argparse
import re
import shutil
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from sound_of_vibe.kimi_hooks import state_directory
from sound_of_vibe.rules import commentary_sentences
from sound_of_vibe.kimi_transcript import WireTail, find_wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--long-task", action="store_true", help="Also verify narration during an 18-second tool call")
    parser.add_argument("--commentary", action="store_true", help="Verify actual intermediate assistant prose in both languages")
    arguments = parser.parse_args()
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
        for language in ("en", "zh"):
            (root / f"README-{language}.md").write_text(f"Global {language} voice smoke test. Version: 0.1.0.\n", encoding="utf-8")
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
                ("en", "Read only README-en.md using the Read tool. Do not write files or use shell commands. Answer only the version number."),
                ("zh", "只用 Read 工具读取尚未查看过的 README-zh.md，不修改文件，不执行命令，最后只回答版本号。"),
            ]
            if arguments.commentary:
                prompts = [(language,
                            ("先用中文在可见回复中说明你接下来如何读取文件和验证版本。" if language == "zh" else
                             "First explain in English visible prose how you will read the file and verify the version. ") +
                            f"Then use Read to read README-{language}.md. Do not write files. Your final answer must only be the version.")
                           for language in ("en", "zh")]
            previous_completions = 0
            session = None
            for language, prompt in prompts:
                turn_log_offset = len(new_log())
                wire = find_wire(session) if session else None
                wire_offset = wire.stat().st_size if wire else 0
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
                if not arguments.commentary:
                    wait_for(lambda: f"[voice/{language}] {text} session={session}" in new_log()[turn_log_offset:])
                # Require playback of the actual action, its result, and the
                # completion; a greeting/completion alone is insufficient.
                actions = ("commentary", "complete") if arguments.commentary else ("start", "read", "read_result", "complete")
                for action in actions:
                    marker = f"[audio/{language}] Playback completed. action={action} session={session}"
                    wait_for(lambda marker=marker: marker in new_log()[turn_log_offset:])
                turn_log = new_log()[turn_log_offset:]
                positions = [turn_log.index(f"[audio/{language}] Playback completed. action={action} session={session}")
                             for action in actions]
                if positions != sorted(positions):
                    raise AssertionError("Completion overtook execution narration")
                if arguments.commentary:
                    wire = find_wire(session)
                    if wire is None:
                        raise AssertionError("No journal for the test session")
                    expected = [sentence for event in WireTail(wire, wire_offset).poll()
                                for sentence in commentary_sentences(event.text)]
                    if not expected:
                        raise AssertionError("The model did not produce visible intermediate commentary")
                    for sentence in expected:
                        if f"[voice/{language}] {sentence.text} session={session}" not in turn_log:
                            raise AssertionError("Assistant prose was lost or replaced by a template")
                    count = turn_log.count(f"[audio/{language}] Playback completed. action=commentary session={session}")
                    if count != len(expected):
                        raise AssertionError("Not all commentary sentences completed playback")
                previous_completions = new_log().count("[hook/Stop]")
                label = "actual assistant commentary" if arguments.commentary else "start + read + result + completion"
                print(f"Passed: {language} {label} playback, session={session}", flush=True)
            if arguments.long_task:
                offset = len(new_log())
                prompt = ('Use Bash to run exactly python -c "import time; time.sleep(18); print(123)". '
                          'This only waits and prints; do not write files. Wait for the result, then answer only 123.')
                process.write("\x1b[200~" + prompt + "\x1b[201~\r")
                wait_for(lambda: f"[audio/en] Playback completed. action=tool_wait session={session}" in new_log()[offset:], timeout=60)
                wait_for(lambda: f"[audio/en] Playback completed. action=complete session={session}" in new_log()[offset:], timeout=60)
                print("Passed: in-progress audio during a long tool call.", flush=True)
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
