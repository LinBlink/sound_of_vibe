"""Conservative, bilingual progress extraction without a language model."""

import re
import shlex
from collections import OrderedDict
from collections.abc import Callable
from time import monotonic
from typing import Literal

from .models import Action, Event, Language, Narration

TEMPLATES: dict[str, dict[str, str]] = {
    "read": {"zh": "正在查看文件", "en": "Reading files"},
    "search": {"zh": "正在搜索代码", "en": "Searching the code"},
    "edit": {"zh": "正在修改文件", "en": "Updating files"},
    "test": {"zh": "正在运行测试", "en": "Running tests"},
    "command": {"zh": "正在执行命令", "en": "Running a command"},
    "tool": {"zh": "正在调用工具", "en": "Using a tool"},
    "complete": {"zh": "任务已完成", "en": "Task completed"},
    "failed": {"zh": "任务执行失败", "en": "Task failed"},
}
ACTION_WORDS = {
    "test": r"运行(?:单元)?测试|测试|验证|检查结果|\b(?:run(?:ning)?|execut(?:e|ing))\s+(?:the\s+)?tests?\b|\b(?:test\w*|verif\w*|validat\w*)\b",
    "search": r"搜索|查找|定位|\b(?:search\w*|find\w*|locat\w*)\b",
    "edit": r"修改|编辑|更新|重构|修复|创建|添加|实现|\b(?:edit\w*|updat\w*|refactor\w*|fix\w*|creat\w*|add\w*|implement\w*)\b",
    "read": r"查看|读取|阅读|检查|分析|\b(?:read\w*|inspect\w*|review\w*|check\w*|analy[sz]\w*|examin\w*)\b",
    "command": r"运行|执行|\b(?:run\w*|execut\w*)\b",
}
ZH_PROGRESS = re.compile(r"^(?:(?:我|我们)(?:会|将|准备|先|现在|正在|接下来|再|来)*|(?:接下来|首先|现在|然后)[，,:： ]*|正在|准备|先)")
EN_PROGRESS = re.compile(
    r"^(?:(?:I|we)(?:['’]ll| will|['’]m| am|['’]re| are)\s+|"
    r"let['’]s\s+|(?:next|first|now|then)[,:]?\s+(?:(?:I|we)(?:['’]ll| will|['’]m| am|['’]re| are)\s+)?|"
    r"(?:reading|inspecting|reviewing|checking|analy[sz]ing|searching|finding|locating|"
    r"editing|updating|refactoring|fixing|creating|adding|implementing|testing|verifying|validating|running|executing)\b)",
    re.IGNORECASE,
)
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
IDENTIFIERS = re.compile(r"`[^`]*`|https?://\S+|(?:[A-Za-z]:[\\/]|[./\\])[\w./\\:-]+|\b[\w-]+\.(?:py|ts|js|java|md|json|toml|yaml|yml|log|txt)\b")


def detect_language(text: str) -> Language | None:
    text = IDENTIFIERS.sub(" ", text)
    if re.search(r"[\u3400-\u9fff]", text):
        return "zh"
    return "en" if re.search(r"[A-Za-z]", text) else None


def classify_command(command: str) -> Action:
    """Only recognize explicit executable names; never search arbitrary arguments."""
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return "command"
    if not parts:
        return "command"
    executable = parts[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    executable = re.sub(r"\.(?:exe|cmd|bat)$", "", executable)
    if executable in {"bash", "sh", "zsh"} and len(parts) == 3 and parts[1] in {"-c", "-lc"}:
        return classify_command(parts[2])
    if executable in {"pwsh", "powershell"}:
        command_index = next((index for index, part in enumerate(parts) if part.lower() in {"-command", "-c"}), None)
        if command_index is not None and command_index == len(parts) - 2:
            return classify_command(parts[-1])
        return "command"
    if any(token in {";", "|", "||", "&&", "&"} or ";" in token for token in parts):
        return "command"
    if executable in {"pytest", "jest", "vitest"}:
        return "test"
    if executable in {"python", "python3", "py"} and parts[1:3] == ["-m", "pytest"]:
        return "test"
    if executable in {"npm", "pnpm", "yarn", "bun"} and (
        parts[1:2] == ["test"] or parts[1:3] == ["run", "test"]
    ):
        return "test"
    if executable in {"cargo", "go", "mvn", "mvnw", "gradle", "gradlew"} and "test" in parts[1:]:
        return "test"
    if executable in {"rg", "grep", "find", "findstr"}:
        return "search"
    if executable in {"cat", "head", "tail", "ls", "dir", "get-content", "get-childitem"}:
        return "read"
    return "command"


def classify_tool(name: str, arguments: dict) -> Action:
    normalized = re.sub(r"[^a-z]", "", name.lower())
    if normalized in {"bash", "shell", "execcommand", "runcommand", "terminal"}:
        return classify_command(str(arguments.get("command", arguments.get("cmd", ""))))
    if normalized in {"read", "readfile", "readfiles", "view", "viewfile"}:
        return "read"
    if normalized in {"grep", "glob", "search", "searchfiles", "websearch"}:
        return "search"
    if normalized in {"write", "writefile", "edit", "editfile", "strreplace", "strreplacefile", "applypatch"}:
        return "edit"
    return "tool"


def progress_sentences(text: str) -> list[Narration]:
    """Drop code/log lines before splitting prose; emit only explicit action sentences."""
    prose = []
    fence: str | None = None
    for line in ANSI.sub("", text).splitlines():
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence or not stripped or line.startswith(("    ", "\t")):
            continue
        if re.match(r"^(?:[+>@$]|---|@@|diff\b|Traceback|File\s+\"|\d{4}-\d\d-\d\d|\[(?:INFO|DEBUG|WARN|ERROR)\]|(?:INFO|DEBUG|WARN|ERROR)\b)", stripped):
            continue
        stripped = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", stripped)
        prose.append(stripped)
    results = []
    for sentence in re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+|\n", "\n".join(prose)):
        sentence = sentence.strip()
        language = detect_language(sentence)
        if not language or not (ZH_PROGRESS if language == "zh" else EN_PROGRESS).match(sentence):
            continue
        if re.search(r"已完成|已经|已修改|\b(?:finished|completed|already|have\s+\w+ed)\b", sentence, re.I):
            continue
        # Inline snippets are omitted, retaining the surrounding action description.
        sentence = re.sub(r"`[^`]*`", "", sentence)
        sentence = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", sentence)
        sentence = re.sub(r"[*_]+", "", sentence)
        sentence = " ".join(sentence.split())
        if re.search(r"[{}]|=>|\b(?:def|class|const|function|import)\s+\w+", sentence):
            continue
        # Classify the first action, not nouns later in the description:
        # "inspect the tests" is inspection, while "run tests" is testing.
        action_text = IDENTIFIERS.sub(" ", sentence)
        matches = [(match.start(), index, action) for index, (action, pattern) in enumerate(ACTION_WORDS.items())
                   if (match := re.search(pattern, action_text, re.I))]
        action = min(matches)[2] if matches else None
        if not action:
            continue
        if language == "zh" and len(sentence) > 60:
            sentence = sentence[:59] + "…"
        elif language == "en" and len(sentence.split()) > 30:
            sentence = " ".join(sentence.split()[:30]) + "…"
        results.append(Narration(sentence, language, action))
    return results


class Narrator:
    """Language selection and bounded deduplication; pacing is handled by the player."""

    def __init__(self, prompt: str = "", language: Literal["auto", "zh", "en"] = "auto",
                 clock: Callable[[], float] = monotonic, dedup_seconds: float = 30):
        self.mode = language
        self.language: Language = (detect_language(prompt) or "zh") if language == "auto" else language
        self.clock = clock
        self.dedup_seconds = dedup_seconds
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.recent: dict[tuple[str, str], float] = {}
        self.ended = False

    def consume(self, event: Event) -> list[Narration]:
        if self.ended or event.event_id in self.seen:
            return []
        self.seen[event.event_id] = None
        if len(self.seen) > 4096:
            self.seen.popitem(last=False)
        sentences = progress_sentences(event.text)
        selected = []
        for sentence in sentences:
            if self.mode != "auto" and sentence.language != self.mode:
                continue
            self.language = sentence.language
            selected.append(sentence)
        if event.terminal:
            self.ended = True
            action = "failed" if event.action == "failed" else "complete"
            return [Narration(TEMPLATES[action][self.language], self.language, action, True)]
        if not selected and event.action:
            selected = [Narration(TEMPLATES[event.action][self.language], self.language, event.action)]
        now = self.clock()
        self.recent = {key: when for key, when in self.recent.items() if now - when < self.dedup_seconds}
        accepted = []
        for sentence in selected:
            key = (sentence.action, sentence.language)
            if key not in self.recent:
                self.recent[key] = now
                accepted.append(sentence)
        return accepted
