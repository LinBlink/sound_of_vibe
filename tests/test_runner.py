import asyncio
import io
import json
import os
import sys
import unittest
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
    def assert_process_exited(self, pid):
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x100000, False, pid)
            if handle:
                try:
                    # Closing a kill-on-close job requests termination; Windows
                    # can signal a descendant just after the parent exits.
                    self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
                finally:
                    kernel.CloseHandle(handle)
            else:
                self.assertEqual(ctypes.get_last_error(), 87)
        else:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

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
        self.assertEqual([s.text for s in spoken], ["我先查看文件。", "正在查看文件", "任务已完成"])
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

    async def test_kimi_wrapper_disables_only_our_global_voice_hook(self):
        code = "import os; print(os.environ.get('SOUND_OF_VIBE_DISABLED'))"
        result, output, _, _, _ = await self.run_fake(code)
        self.assertEqual(result, 0)
        self.assertEqual(output.strip(), "1")

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
        self.assert_process_exited(pid)
        self.assertTrue(speaker.closed)

    @unittest.skipUnless(os.name == "nt", "Windows job process-tree integration")
    async def test_cancel_reaps_descendant(self):
        output = io.StringIO()
        speaker = Speaker(None, lambda _: None, self.fail)
        code = ("import subprocess,sys,time,json,os\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "print(json.dumps({'pid':os.getpid(),'child':child.pid}),flush=True)\n"
                "time.sleep(60)")
        task = asyncio.create_task(consume_process([sys.executable, "-c", code], "kimi", "", Narrator(), speaker,
                                                   stdout=output, stderr=io.StringIO()))
        for _ in range(100):
            if output.getvalue():
                break
            await asyncio.sleep(0.02)
        self.assertTrue(output.getvalue())
        pids = json.loads(output.getvalue())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        self.assert_process_exited(pids["pid"])
        self.assert_process_exited(pids["child"])

    @unittest.skipUnless(os.name == "nt", "Windows job process-tree integration")
    async def test_normal_exit_closes_inherited_descendant_pipes(self):
        code = ("import subprocess,sys,json\n"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                "print(json.dumps({'child':child.pid}),flush=True)")
        result, output, _, spoken, _ = await self.run_fake(code, prompt="Inspect files")
        self.assertEqual(result, 0)
        self.assert_process_exited(json.loads(output)["child"])
        self.assertEqual(spoken[-1].text, "Task completed")


if __name__ == "__main__":
    unittest.main()
