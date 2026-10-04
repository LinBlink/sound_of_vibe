"""Events shared by agent adapters and the narration pipeline."""

from dataclasses import dataclass
from typing import Literal

Language = Literal["zh", "en"]
Action = Literal["read", "search", "edit", "test", "command", "tool", "complete", "failed"]


@dataclass(frozen=True)
class Event:
    source: str
    event_id: str
    action: Action | None = None
    text: str = ""
    terminal: bool = False


@dataclass(frozen=True)
class Narration:
    text: str
    language: Language
    action: str
    terminal: bool = False
    session: str = ""
