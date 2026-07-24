"""Per-session conversation memory fed to the LLM.

Keeps a bounded turn history so context stays small (voice answers are short and
latency-sensitive). Pure and synchronous — unit-testable without I/O.
"""
from __future__ import annotations

from app.voice.providers.base import ChatMessage


class ConversationState:
    def __init__(self, max_turns: int = 12) -> None:
        # max_turns counts individual messages (user/assistant), not pairs.
        self._messages: list[ChatMessage] = []
        self._max = max_turns

    def add_user(self, text: str) -> None:
        self._append("user", text)

    def add_assistant(self, text: str) -> None:
        self._append("assistant", text)

    def _append(self, role: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._messages.append(ChatMessage(role=role, text=text))  # type: ignore[arg-type]
        if len(self._messages) > self._max:
            self._messages = self._messages[-self._max :]

    def messages(self) -> list[ChatMessage]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()
