import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from sound_of_vibe.cli import main
from sound_of_vibe.rules import Narrator
from sound_of_vibe.runner import build_command, consume_process
from sound_of_vibe.speech import Speaker


class CommandTests(unittest.TestCase):
    def test_prompt_is_data_and_no_shell_interpolation(self):
        prompt = '-hello " & echo dangerous'
        self.assertEqual(build_command("codex", "codex", prompt, ["--sandbox", "read-only"]),
                         ["codex", "exec", "--json", "--sandbox", "read-only", "-"])
        self.assertIn(prompt, build_command("kimi", "kimi", prompt, []))

    def test_output_and_prompt_overrides_rejected(self):
        for source in ("codex", "kimi"):
            for argument in ("--json", "--output-format=text", "--prompt", "-phello", "--help"):
                with self.assertRaises(ValueError):
                    build_command(source, source, "hello", [argument])


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def run_fake(self, code, source="kimi", prompt="检查项目"):
        output, errors, narration, diagnostics = io.StringIO(), io.StringIO(), [], []
        speaker = Speaker(None, narration.append, diagnostics.append)
        result = await asyncio.wait_for(consume_process(
            [sys.executable, "-c", code], source, prompt, Narrator(prompt), speaker,
            stdout=output, stderr=errors, diagnostic=diagnostics.append), timeout=10)
        return result, output.getvalue(), errors.getvalue(), narration, diagnostics

    async def test_chunked_unicode_stdout_and_large_stderr(self):
        message = {"role": "assistant", "content": "我先查看文件。", "tool_calls": [
            {"id": "1", "function": {"name": "ReadFile", "arguments": "{}"}}]}
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode()
        code = ("import sys\n"
                "sys.stderr.write('log' * 100000); sys.stderr.flush()\n"
                f"data = {payload!r}\n"
                "for byte in data:\n sys.stdout.buffer.write(bytes([byte])); sys.stdout.buffer.flush()\n")
        result, output, errors, spoken, diagnostics = await self.run_fake(code)
        self.assertEqual(result, 0)
        self.assertEqual(output.encode(), payload)
        self.assertEqual(len(errors), 300000)
        self.assertEqual([s.text for s in spoken], ["我先查看文件。", "任务已完成"])
        self.assertEqual(diagnostics, [])

    async def test_failure_exit_code_and_malformed_json(self):
        result, _, _, spoken, diagnostics = await self.run_fake("import sys; print('bad json'); sys.exit(7)", prompt="Inspect files")
        self.assertEqual(result, 7)
        self.assertEqual([s.text for s in spoken], ["Task failed"])
        self.assertEqual(len(diagnostics), 1)

    async def test_codex_stdin_and_final_suppression(self):
        prompt = "检查项目 & echo $HOME"
        code = ("import sys,json\n"
                f"assert sys.stdin.read() == {prompt!r}\n"
                "print(json.dumps({'type':'item.completed','item':{'id':'final','type':'agent_message','text':\"I'll inspect it tomorrow.\"}}))\n"
                "print(json.dumps({'type':'turn.completed'}))")
        result, _, _, spoken, _ = await self.run_fake(code, source="codex", prompt=prompt)
        self.assertEqual(result, 0)
        self.assertEqual([s.text for s in spoken], ["任务已完成"])

    async def test_cancel_reaps_process(self):
        output = io.StringIO()
        speaker = Speaker(None, lambda _: None, self.fail)
        task = asyncio.create_task(consume_process(
            [sys.executable, "-c", "import os,time,json; print(json.dumps({'pid':os.getpid()}), flush=True); time.sleep(60)"],
            "kimi", "", Narrator(), speaker, stdout=output, stderr=io.StringIO()))
        for _ in range(100):
            if output.getvalue():
                break
            await asyncio.sleep(0.02)
        self.assertTrue(output.getvalue())
        pid = json.loads(output.getvalue())["pid"]
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        if os.name == "nt":
            process = await asyncio.create_subprocess_exec("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH",
                                                           stdout=asyncio.subprocess.PIPE)
            report, _ = await process.communicate()
            self.assertNotIn(f'"{pid}"'.encode(), report)
        else:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertTrue(speaker.closed)


if __name__ == "__main__":
    unittest.main()
