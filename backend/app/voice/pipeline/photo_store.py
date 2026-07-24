"""Photo persistence — DigitalOcean Spaces (S3-compatible) with a local-disk
fallback.

Accepted farmer photo bytes are uploaded to ``settings.do_spaces_bucket``
(public-read) under ``{prefix}/{user}/{chat}/{photo_id}.<ext>`` and the object's
public CDN URL is returned as the photo's ``stored_path`` (flows into the
diagnosis refs + agronom payload). When Spaces is not configured, or an upload
fails, the bytes are written to ``settings.photos_dir`` on disk instead
(``ChatStore``'s atomic-write idiom). Every path segment is sanitized — no
traversal, no surprises.

Synchronous by design (boto3 + plain file I/O); the caller runs it through
``asyncio.to_thread``. NEVER raises: any failure is logged and degrades
(Spaces → disk → ``None``) so the photo pipeline stays fail-open.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path

from app.config import Settings

logger = logging.getLogger("voice.pipeline.photo_store")

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")

_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def _sanitize(segment: str) -> str | None:
    """Reduce a path segment to ``[A-Za-z0-9._-]``; reject empty results (no
    traversal is possible since ``.``/``..`` alone never survive as a
    meaningful segment on their own after replacement + dot-stripping)."""
    cleaned = _SAFE_CHARS.sub("_", segment or "")
    cleaned = cleaned.strip(".")  # drop leading/trailing dots (blocks "..")
    if not cleaned:
        return None
    return cleaned


def _ext_for_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), ".jpg")


def _object_key(prefix: str, user_seg: str, chat_seg: str, name: str) -> str:
    parts = [p for p in (prefix or "").strip("/").split("/") if p]
    parts += [user_seg, chat_seg, name]
    return "/".join(parts)


class PhotoStore:
    """Persists accepted photo bytes: DigitalOcean Spaces (public-read) with a
    local-disk fallback."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._root = Path(settings.photos_dir)
        self._client = None  # lazy boto3 S3 client (created on first upload)

    # ---- config helpers -----------------------------------------------------

    @property
    def _spaces_enabled(self) -> bool:
        s = self._s
        return bool(s.do_spaces_bucket and s.do_spaces_key and s.do_spaces_secret)

    def _endpoint(self) -> str:
        s = self._s
        return s.do_spaces_endpoint or f"https://{s.do_spaces_region}.digitaloceanspaces.com"

    def _public_base(self) -> str:
        s = self._s
        base = s.do_spaces_public_base or (
            f"https://{s.do_spaces_bucket}.{s.do_spaces_region}.cdn.digitaloceanspaces.com"
        )
        return base.rstrip("/")

    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.client import Config

            s = self._s
            self._client = boto3.session.Session().client(
                "s3",
                region_name=s.do_spaces_region,
                endpoint_url=self._endpoint(),
                aws_access_key_id=s.do_spaces_key,
                aws_secret_access_key=s.do_spaces_secret,
                config=Config(s3={"addressing_style": "virtual"}),
            )
        return self._client

    # ---- public API ---------------------------------------------------------

    def save(
        self, user_id: str, chat_id: str, photo_id: str, data: bytes, mime: str,
    ) -> str | None:
        """Persist ``data`` and return its location string (a public Spaces CDN
        URL, or a local disk path on fallback), or ``None`` if both fail.
        Never raises."""
        user_seg = _sanitize(user_id)
        chat_seg = _sanitize(chat_id)
        photo_seg = _sanitize(photo_id)
        if not user_seg or not chat_seg or not photo_seg:
            logger.warning(
                "photo store: unsafe path segment (user=%r chat=%r photo=%r)",
                user_id, chat_id, photo_id,
            )
            return None
        name = f"{photo_seg}{_ext_for_mime(mime)}"

        if self._spaces_enabled:
            url = self._upload_spaces(user_seg, chat_seg, name, data, mime)
            if url is not None:
                return url
            # Upload failed — fall through to the local-disk fallback below.

        return self._save_local(user_seg, chat_seg, name, data)

    # ---- backends -----------------------------------------------------------

    def _upload_spaces(
        self, user_seg: str, chat_seg: str, name: str, data: bytes, mime: str,
    ) -> str | None:
        """Upload public-read to Spaces; return the CDN URL or None on failure."""
        try:
            key = _object_key(self._s.do_spaces_prefix, user_seg, chat_seg, name)
            self._get_client().put_object(
                Bucket=self._s.do_spaces_bucket,
                Key=key,
                Body=data,
                ACL="public-read",
                ContentType=mime or "image/jpeg",
            )
            return f"{self._public_base()}/{key}"
        except Exception:  # noqa: BLE001 — Spaces upload must never block a photo
            logger.warning("photo store: Spaces upload failed", exc_info=True)
            return None

    def _save_local(
        self, user_seg: str, chat_seg: str, name: str, data: bytes,
    ) -> str | None:
        """Atomic local-disk write; returns the path or None on failure."""
        try:
            directory = self._root / user_seg / chat_seg
            directory.mkdir(parents=True, exist_ok=True)
            final_path = directory / name
            tmp_path = directory / f".{name}.{uuid.uuid4().hex}.tmp"
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, final_path)
            return str(final_path)
        except Exception:  # noqa: BLE001 — persistence must never block a photo
            logger.warning("photo store: local save failed", exc_info=True)
            return None
