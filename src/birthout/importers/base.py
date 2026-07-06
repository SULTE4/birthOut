from __future__ import annotations

from typing import Protocol

from birthout.models import ChatMessage


class ChatImporter(Protocol):
    def parse(self, payload: bytes, *, filename: str, day_order: str = "auto") -> list[ChatMessage]: ...
