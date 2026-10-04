"""Bounded JSONL framing independent of subprocess chunk boundaries."""

import json


class JsonlDecoder:
    def __init__(self, max_line_bytes: int = 1024 * 1024):
        self.limit = max_line_bytes
        self.buffer = bytearray()
        self.dropping = False
        self.invalid = 0

    def feed(self, chunk: bytes) -> list[dict]:
        messages = []
        pieces = chunk.split(b"\n")
        for index, piece in enumerate(pieces):
            complete = index < len(pieces) - 1
            if not self.dropping:
                if len(self.buffer) + len(piece) > self.limit:
                    self.buffer.clear()
                    self.dropping = True
                    self.invalid += 1
                else:
                    self.buffer.extend(piece)
            if complete:
                if not self.dropping:
                    parsed = self._parse(bytes(self.buffer))
                    if parsed is not None:
                        messages.append(parsed)
                self.buffer.clear()
                self.dropping = False
        return messages

    def finish(self) -> list[dict]:
        parsed = self._parse(bytes(self.buffer)) if self.buffer and not self.dropping else None
        self.buffer.clear()
        self.dropping = False
        return [parsed] if parsed is not None else []

    def _parse(self, line: bytes) -> dict | None:
        if not line.strip():
            return None
        try:
            result = json.loads(line.decode("utf-8-sig"))
            if not isinstance(result, dict):
                raise ValueError("Expected an object")
            return result
        except (ValueError, UnicodeError):
            self.invalid += 1
            return None
