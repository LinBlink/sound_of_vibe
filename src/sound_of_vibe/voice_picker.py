"""Loopback-only voice selection, online previews and global settings."""

import argparse
import asyncio
import json
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .hook_config import atomic_write
from .kimi_hooks import load_settings, state_directory
from .voice_assignment import voice_profiles


async def catalog():
    import edge_tts
    voices = await asyncio.wait_for(edge_tts.list_voices(), 15)
    return voice_profiles([v for v in voices if v["Locale"].startswith(("zh-", "en-"))])


def validate(data, voices):
    names = {v["ShortName"]: v for v in voices}
    selected = data.get("voices", {})
    for language in ("zh", "en"):
        if selected.get(language) not in names or not names[selected[language]]["Locale"].startswith(language + "-"):
            raise ValueError("请选择有效的中英文音色 / Select valid Chinese and English voices")
    rate = data.get("rate", "+0%")
    if not isinstance(rate, str) or not re.fullmatch(r"[+-]\d{1,3}%", rate) or not -50 <= int(rate[:-1]) <= 100:
        raise ValueError("语速范围 -50% 至 +100% / Rate range: -50% to +100%")
    if not isinstance(data.get("per_session_voice", True), bool):
        raise ValueError("Invalid automatic voice setting")
    if not isinstance(data.get("adaptive_rate", True), bool):
        raise ValueError("Invalid adaptive rate setting")
    maximum = data.get("max_rate", 100)
    if type(maximum) is not int or not int(rate[:-1]) <= maximum <= 100:
        raise ValueError("追赶上限须在基础语速至 +100% / Catch-up limit must be between base rate and +100%")
    return selected, rate


def save_settings(root, data, voices):
    selected, rate = validate(data, voices)
    source = data.get("source", "both")
    if source not in {"kimi", "codex", "both"}:
        raise ValueError("Invalid source")
    for name in (("kimi", "codex") if source == "both" else (source,)):
        directory = root / "Codex" if name == "codex" else root
        path = directory / "settings.json"
        # Saving a voice preference must never enable uninstalled/disabled hooks.
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"enabled": False}
        settings.update(voices=selected, rate=rate, per_session_voice=data.get("per_session_voice", True),
                        adaptive_rate=data.get("adaptive_rate", True), max_rate=data.get("max_rate", 100))
        atomic_write(path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")


async def preview(voice, rate, voices):
    import edge_tts
    entry = next((v for v in voices if v["ShortName"] == voice), None)
    if entry is None:
        raise ValueError("Invalid voice")
    text = "文件修改已完成。测试全部通过。接下来检查执行结果。" if entry["Locale"].startswith("zh-") else "The file update is complete. All tests passed. Next, I will check the results."
    async def synthesize():
        audio = bytearray()
        async for chunk in edge_tts.Communicate(text, entry.get("BaseVoice", voice), rate=rate).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
                if len(audio) > 4 * 1024 * 1024:
                    raise ValueError("Preview audio too large")
        from .robotic import mechanical_audio
        return await asyncio.to_thread(mechanical_audio, bytes(audio), entry["Gender"], entry.get("PitchHz"))
    return await asyncio.wait_for(synthesize(), 25)


def create_server(root=None, voices=None):
    root = root or state_directory()
    voices = asyncio.run(catalog()) if voices is None else voices
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, body, kind="application/json", status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed():
                return self.reply({"error": "Invalid host"}, status=403)
            if self.path == "/":
                html = (Path(__file__).parent / "assets" / "voices.html").read_text(encoding="utf-8")
                return self.reply(html.replace("__TOKEN__", token).encode(), "text/html; charset=utf-8")
            if self.path == "/api/settings":
                result = {name: {key: load_settings(directory).get(key) for key in ("voices", "rate", "per_session_voice", "adaptive_rate", "max_rate")}
                          for name, directory in (("kimi", root), ("codex", root / "Codex"))}
                return self.reply({"settings": result, "voices": voices})
            if self.path in {"/sounds/complete", "/sounds/ask"}:
                return self.reply((Path(__file__).parent / "assets" / (self.path.rsplit("/", 1)[1] + ".ogg")).read_bytes(), "audio/ogg")
            self.reply({"error": "Not found"}, status=404)

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.allowed() or self.headers.get("X-Voice-Token") != token or self.headers.get("Origin", origin) != origin:
                return self.reply({"error": "Invalid request"}, status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Invalid request")
                if self.path == "/api/save":
                    save_settings(root, data, voices)
                    return self.reply({"saved": True})
                if self.path == "/api/preview":
                    _, rate = validate(data, voices)
                    return self.reply(asyncio.run(preview(data.get("voice"), rate, voices)), "audio/wav")
                self.reply({"error": "Not found"}, status=404)
            except (ValueError, TypeError) as error:
                self.reply({"error": str(error)}, status=400)
            except Exception as error:
                self.reply({"error": "请求失败 / Request failed: " + type(error).__name__}, status=503)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server


def open_picker(no_browser=False):
    server = create_server()
    url = f"http://127.0.0.1:{server.server_port}/"
    print("音色选择 / Voice settings: " + url, flush=True)
    print("按 Ctrl+C 关闭设置页服务 / Ctrl+C stops the settings server", flush=True)
    if not no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    open_picker(args.no_browser)
