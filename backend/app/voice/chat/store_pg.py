"""PostgreSQL chat backend — the whole ``ChatDoc`` lives in one JSONB column.

Why a document column and not per-field tables: the doc schema still moves
(``crop_context_answers`` landed this week) and the guide only ever needs the
whole document; the MAIN Growz backend can later lift history with a plain
``SELECT doc FROM voice_chats``. ``chat_id`` alone is the primary key (team
decision 2026-08-05): one chat may be opened from several devices, and in the
App <-> Main backend <-> AI backend topology the id is MINTED by the main
backend — ``user_id`` is just an attribute (creator device, list scoping).

Engine per settings-URL, created lazily and cached at module level (one
process, granian worker). Every method mirrors the file store's fail-open
contract: log and degrade, never break a live call.
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.voice.chat.models import ChatDoc, build_summary, new_chat_doc

logger = logging.getLogger("voice.chat.store_pg")

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS voice_chats (
    chat_id    TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    doc        JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS voice_chats_user_idx ON voice_chats (user_id);
"""

_engines: dict[str, object] = {}


def _engine(url: str):
    eng = _engines.get(url)
    if eng is None:
        import sqlalchemy

        eng = sqlalchemy.create_engine(
            url, pool_size=5, max_overflow=10, pool_pre_ping=True,
            pool_recycle=3600,
        )
        with eng.begin() as conn:
            for stmt in _TABLE_DDL.strip().split(";"):
                if stmt.strip():
                    conn.execute(sqlalchemy.text(stmt))
        _engines[url] = eng
        logger.info("voice_chats table ready (postgres chat store)")
    return eng


class PgChatBackend:
    """The postgres twin of the file store's create/read/save/list quartet."""

    def __init__(self, settings: Settings) -> None:
        self._url = settings.database_url

    # ---- helpers ---------------------------------------------------------

    def _exec(self, fn):
        import sqlalchemy

        eng = _engine(self._url)
        with eng.begin() as conn:
            return fn(conn, sqlalchemy.text)

    # ---- ChatStore surface ----------------------------------------------

    def create(self, user_id: str, chat_id: str | None = None) -> ChatDoc:
        doc = new_chat_doc(user_id)
        if chat_id:
            doc.id = chat_id
        self.save(doc)
        return doc

    def read(self, chat_id: str) -> ChatDoc | None:
        """By chat_id ONLY — knowing the id grants access (the main backend
        is the gatekeeper; ids are unguessable and never listed cross-user)."""
        try:
            def q(conn, text):
                row = conn.execute(
                    text("SELECT doc FROM voice_chats WHERE chat_id = :c"),
                    {"c": chat_id},
                ).fetchone()
                return row[0] if row else None

            raw = self._exec(q)
            if raw is None:
                return None
            return ChatDoc.model_validate(raw)
        except Exception:  # noqa: BLE001 — a bad row must not break the call
            logger.exception("pg chat read failed: %s", chat_id)
            return None

    def save(self, doc: ChatDoc) -> None:
        try:
            # agronom_review preservation (contract P3.1), same rule as the
            # file store: a writer that never saw a review can't erase one.
            if doc.agronom_review is None:
                on_db = self.read(doc.id)
                if on_db is not None and on_db.agronom_review is not None:
                    doc.agronom_review = on_db.agronom_review

            def q(conn, text):
                conn.execute(
                    text(
                        "INSERT INTO voice_chats (chat_id, user_id, doc, updated_at)"
                        " VALUES (:c, :u, CAST(:d AS jsonb), now())"
                        " ON CONFLICT (chat_id) DO UPDATE"
                        " SET doc = EXCLUDED.doc, updated_at = now()"
                    ),
                    {"c": doc.id, "u": doc.user_id, "d": doc.model_dump_json()},
                )

            self._exec(q)
        except Exception:  # noqa: BLE001 — a write failure must never break a call
            logger.exception("pg chat save failed: %s/%s", doc.user_id, doc.id)

    def list_summaries(self, user_id: str, limit: int = 50) -> list[dict]:
        try:
            limit = max(1, min(limit, 100))

            def q(conn, text):
                rows = conn.execute(
                    text(
                        "SELECT doc FROM voice_chats WHERE user_id = :u"
                        " ORDER BY updated_at DESC LIMIT :n"
                    ),
                    {"u": user_id, "n": limit},
                ).fetchall()
                return [r[0] for r in rows]

            out = []
            for raw in self._exec(q):
                try:
                    out.append(build_summary(ChatDoc.model_validate(raw)))
                except Exception:  # noqa: BLE001 — one bad row never fails the list
                    logger.warning("pg chat row skipped in list (parse failed)")
            return out
        except Exception:  # noqa: BLE001
            logger.exception("pg chat list failed: %s", user_id)
            return []
