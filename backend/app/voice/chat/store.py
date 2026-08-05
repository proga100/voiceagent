"""Chat storage — mirrors ``pipeline/memory.py``'s ``MemoryStore`` atomic file
pattern exactly: one JSON document per chat, written via tmp-file + ``rename``
(atomic on POSIX), corrupt files sidelined as ``.bad`` rather than breaking a
read.

Layout (contract §3.1)::

    data/chats/<user_id>/<chat_id>.json

``user_id`` and ``chat_id`` become path segments, so both are validated
BEFORE ever touching the filesystem. Every public method swallows and logs
unexpected I/O errors — a chat write must never break a live call
(fail-open, contract §8); the caller keeps running from in-memory state and
the conversation simply isn't persisted for that step.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from app.config import Settings
from app.voice.chat.models import (
    ChatDoc,
    ChatMessage,
    build_summary,
    derive_title,
    new_chat_doc,
    now_iso,
)
from app.voice.pipeline.memory import valid_device_id

logger = logging.getLogger("voice.chat.store")

# Accepts our own uuid4().hex AND external ids minted by the main Growz
# backend (dashed UUIDs). Still filesystem-safe: no dots, no separators.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# Caps enforced here, never trusted from callers (contract §3.3).
_MAX_TITLE_CHARS = 60
_MAX_MESSAGE_CHARS = 2000
_MAX_MESSAGES = 500

# One lock per chat id; single-process store (prod = one uvicorn worker),
# same pattern as memory.py's per-profile-key locks.
_locks: dict[str, asyncio.Lock] = {}


def lock_for(chat_id: str) -> asyncio.Lock:
    return _locks.setdefault(chat_id, asyncio.Lock())


def valid_chat_id(chat_id: object) -> bool:
    """The id becomes a filename (files backend) / a key (postgres) — allow
    uuid4().hex and the main backend's dashed UUIDs, nothing path-hostile."""
    return isinstance(chat_id, str) and _CHAT_ID_RE.fullmatch(chat_id) is not None


class ChatStore:
    """Chat store facade: file-backed by default (all paths under
    ``settings.chats_dir``); ``CHAT_STORE=postgres`` swaps the persistence
    for the JSONB ``voice_chats`` table while every caller keeps this same
    interface. ``append_message`` lives here once — it funnels into save()."""

    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.chats_dir)
        self._pg = None
        if (
            getattr(settings, "chat_store", "files") == "postgres"
            and getattr(settings, "database_url", "")
        ):
            from app.voice.chat.store_pg import PgChatBackend

            self._pg = PgChatBackend(settings)

    def _dir(self, user_id: str) -> Path:
        d = self._root / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, user_id: str, chat_id: str) -> Path:
        return self._dir(user_id) / f"{chat_id}.json"

    def create(self, user_id: str, chat_id: str | None = None) -> ChatDoc:
        """``chat_id`` lets the WS path adopt an id minted by the MAIN Growz
        backend (auto-create on unknown chat.start ids) — the id is theirs,
        we just store under it."""
        doc = new_chat_doc(user_id)
        if chat_id:
            doc.id = chat_id
        self.save(doc)
        return doc

    def read(self, user_id: str, chat_id: str) -> ChatDoc | None:
        """Read one chat; corrupt JSON is sidelined as ``.bad`` and treated
        as missing (the caller must keep working)."""
        if not valid_device_id(user_id) or not valid_chat_id(chat_id):
            return None
        if self._pg is not None:
            # chat_id alone is the identity in postgres (team decision):
            # a chat may be opened from several devices, so no owner check.
            return self._pg.read(chat_id)
        path = self._path(user_id, chat_id)
        if not path.exists():
            return None
        try:
            return ChatDoc.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — any parse problem -> treat as missing
            logger.warning("corrupt chat %s — sidelined as .bad", path.name)
            try:
                path.replace(path.with_suffix(".json.bad"))
            except OSError:
                pass
            return None

    def save(self, doc: ChatDoc) -> None:
        """Atomic write (tmp + os.replace), identical to MemoryStore.save."""
        if self._pg is not None:
            self._pg.save(doc)
            return
        try:
            doc.title = (doc.title or "")[:_MAX_TITLE_CHARS]
            if len(doc.messages) > _MAX_MESSAGES:
                doc.messages = doc.messages[-_MAX_MESSAGES:]
            path = self._path(doc.user_id, doc.id)
            # Phase 3 (contract addendum P3.1) — agronom_review preservation:
            # a writer that never saw a review can never erase one. The live
            # voice session/guide hold their own in-memory ChatDoc from
            # connect time; a mid-call REST agronom-request writes "pending"
            # to disk, and without this rule the guide's next
            # append_message/teardown save would silently clobber it.
            # Fail-open: any read/parse error here -> proceed with the plain
            # write (old behaviour).
            if doc.agronom_review is None:
                try:
                    if path.exists():
                        on_disk = ChatDoc.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                        if on_disk.agronom_review is not None:
                            doc.agronom_review = on_disk.agronom_review
                except Exception:  # noqa: BLE001 — fail-open: plain write proceeds
                    pass
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(path)  # atomic on POSIX
        except Exception:  # noqa: BLE001 — a write failure must never break a call
            logger.exception("chat save failed: %s/%s", doc.user_id, doc.id)

    def list_summaries(self, user_id: str, limit: int = 50) -> list[dict]:
        """A device's chats, newest-updated first. Corrupt files are skipped
        (sidelined) — the list never fails because of one bad file."""
        if not valid_device_id(user_id):
            return []
        if self._pg is not None:
            return self._pg.list_summaries(user_id, limit)
        d = self._root / user_id
        if not d.exists():
            return []
        limit = max(1, min(limit, 100))
        docs: list[ChatDoc] = []
        try:
            paths = sorted(d.glob("*.json"))
        except OSError:
            logger.exception("chat list dir scan failed: %s", user_id)
            return []
        for path in paths:
            try:
                docs.append(ChatDoc.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 — one bad file never fails the list
                logger.warning("corrupt chat %s — skipped in list", path.name)
                try:
                    path.replace(path.with_suffix(".json.bad"))
                except OSError:
                    pass
        docs.sort(key=lambda d: d.updated_at, reverse=True)
        return [build_summary(d) for d in docs[:limit]]

    def append_message(
        self, doc: ChatDoc, role: str, kind: str, text: str, photo_url: str = "",
    ) -> None:
        """Append one message (capped + truncated), recompute the title,
        bump ``updated_at`` and save — one persist unit (contract §4.4).
        ``photo_url`` rides kind=="photo" messages (the uploaded image URL)."""
        try:
            text = (text or "")[:_MAX_MESSAGE_CHARS]
            doc.messages.append(
                ChatMessage(role=role, kind=kind, text=text, ts=now_iso(), photo_url=photo_url)
            )
            if len(doc.messages) > _MAX_MESSAGES:
                doc.messages = doc.messages[-_MAX_MESSAGES:]
            doc.title = derive_title(doc)
            doc.updated_at = now_iso()
            self.save(doc)
        except Exception:  # noqa: BLE001 — history is best-effort, never fatal
            logger.exception("chat append_message failed: %s/%s", doc.user_id, doc.id)
