"""Console entry point."""

import argparse
import asyncio
import re
import sys
from pathlib import Path

from . import __version__
from .rules import Narrator
from .runner import build_command, consume_process, expand_windows_shim, replay, resolve_executable
from .speech import DEFAULT_VOICES, EdgeSpeech, Speaker
from . import hook_config


def rate_value(value: str) -> str:
    if not re.fullmatch(r"[+-]\d+%", value):
        raise argparse.ArgumentTypeError("rate must be a signed percentage, for example +10% or -10%")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Chinese / English progress narration for Codex and Kimi CLI")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    hooks = commands.add_parser("hooks", help="Enable automatic voice narration for plain kimi")
    actions = hooks.add_subparsers(dest="hook_action", required=True)
    for name in ("install", "disable", "status"):
        sub = actions.add_parser(name)
        sub.add_argument("--config", type=Path, help="Alternate Kimi config.toml path")
        sub.add_argument("--state-dir", type=Path, help="Alternate narration state directory")
        if name == "install":
            sub.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
            sub.add_argument("--voice-zh", default=DEFAULT_VOICES["zh"])
            sub.add_argument("--voice-en", default=DEFAULT_VOICES["en"])
            sub.add_argument("--rate", type=rate_value, default="+10%")
            sub.add_argument("--text-only", action="store_true", help="Log hook narration without audio")
    for name in ("run", "replay"):
        sub = commands.add_parser(name, help="Launch an agent" if name == "run" else "Replay captured JSONL without an agent")
        sub.add_argument("source", choices=("kimi", "codex"))
        sub.add_argument("--prompt", required=name == "run", default="")
        sub.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
        sub.add_argument("--voice-zh", default=DEFAULT_VOICES["zh"])
        sub.add_argument("--voice-en", default=DEFAULT_VOICES["en"])
        sub.add_argument("--rate", type=rate_value, default="+10%")
        sub.add_argument("--text-only", action="store_true", help="Disable speech; run mode still launches the agent")
        if name == "run":
            sub.add_argument("--cwd", type=Path, default=Path.cwd())
            sub.add_argument("--executable", help="Explicit CLI executable path")
        else:
            sub.add_argument("--file", type=Path, required=True)
            sub.add_argument("--failed", action="store_true", help="Replay a failed process exit")
    return result


async def execute(args, extra: list[str]) -> int:
    if args.command == "hooks":
        import json
        if args.hook_action == "install":
            result = hook_config.install(args.config, args.state_dir, args.language,
                                         {"zh": args.voice_zh, "en": args.voice_en}, args.rate,
                                         text_only=args.text_only)
        elif args.hook_action == "disable":
            result = hook_config.disable(args.config, args.state_dir)
        else:
            result = hook_config.status(args.config, args.state_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    command = None
    if args.command == "run":
        if not args.cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {args.cwd}")
        command = expand_windows_shim(build_command(
            args.source, resolve_executable(args.source, args.executable), args.prompt, extra), args.source)
    narrator = Narrator(args.prompt, args.language)
    backend = None if args.text_only else EdgeSpeech({"zh": args.voice_zh, "en": args.voice_en}, args.rate)
    speaker = Speaker(backend,
                      lambda item: print(f"[voice/{item.language}] {item.text}", file=sys.stderr, flush=True),
                      lambda message: print(f"[sound-of-vibe] {message}", file=sys.stderr, flush=True),
                      interval=1, preserve_progress=True, max_pending=6, prefer_commentary=True)
    if command is not None:
        return await consume_process(command, args.source, args.prompt, narrator, speaker, args.cwd)
    return await replay(args.file, args.source, narrator, speaker, args.failed)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in arguments:
        separator = arguments.index("--")
        extra, arguments = arguments[separator + 1:], arguments[:separator]
    cli = parser()
    args = cli.parse_args(arguments)
    if args.command != "run" and extra:
        cli.error("passthrough arguments are only supported by run")
    if args.command == "run" and not args.prompt.strip():
        cli.error("--prompt cannot be empty")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return asyncio.run(execute(args, extra))
    except KeyboardInterrupt:
        print("[sound-of-vibe] Cancelled.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"[sound-of-vibe] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
