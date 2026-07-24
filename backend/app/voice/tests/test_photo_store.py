"""PhotoStore (§5.5 persistence): path shape, sanitization (no traversal),
mime extension mapping, overwrite, and fail-open on unwritable roots."""
from pathlib import Path

from app.config import Settings
from app.voice.pipeline.photo_store import PhotoStore


def _store(tmp_path) -> tuple[PhotoStore, Path]:
    root = tmp_path / "photos"
    return PhotoStore(Settings(photos_dir=str(root))), root


def test_save_writes_bytes_under_user_chat_photo_path(tmp_path):
    store, root = _store(tmp_path)
    path = store.save("user1", "chat9", "p1", b"jpegbytes", "image/jpeg")
    assert path == str(root / "user1" / "chat9" / "p1.jpg")
    assert Path(path).read_bytes() == b"jpegbytes"


def test_png_mime_maps_to_png_extension(tmp_path):
    store, root = _store(tmp_path)
    path = store.save("u", "c", "p", b"x", "image/png")
    assert path is not None and path.endswith("p.png")


def test_unknown_mime_defaults_to_jpg(tmp_path):
    store, _ = _store(tmp_path)
    path = store.save("u", "c", "p", b"x", "application/octet-stream")
    assert path is not None and path.endswith("p.jpg")


def test_traversal_segments_are_sanitized_inside_root(tmp_path):
    store, root = _store(tmp_path)
    # The client controls photo_id (and user/chat ids arrive over the wire) —
    # traversal characters must never escape the store root.
    path = store.save("../../evil", "c/../..", "p..1", b"x", "image/jpeg")
    assert path is not None
    assert Path(path).resolve().is_relative_to(root.resolve())


def test_dot_only_and_empty_segments_rejected(tmp_path):
    store, _ = _store(tmp_path)
    assert store.save("..", "c", "p", b"x", "image/jpeg") is None
    assert store.save("", "c", "p", b"x", "image/jpeg") is None
    assert store.save("u", "c", "...", b"x", "image/jpeg") is None


def test_overwrite_same_photo_id_succeeds(tmp_path):
    store, _ = _store(tmp_path)
    first = store.save("u", "c", "p", b"one", "image/jpeg")
    second = store.save("u", "c", "p", b"two", "image/jpeg")
    assert first == second
    assert Path(second).read_bytes() == b"two"


def test_unwritable_root_fails_open_returns_none(tmp_path):
    # A FILE occupies the store root -> mkdir(parents=True) fails -> None.
    root = tmp_path / "photos"
    root.write_text("not a directory")
    store = PhotoStore(Settings(photos_dir=str(root)))
    assert store.save("u", "c", "p", b"x", "image/jpeg") is None


# ---- DigitalOcean Spaces upload (public-read) + local fallback --------------


class _FakeS3:
    def __init__(self, exc=None):
        self.calls: list[dict] = []
        self._exc = exc

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return {}


def _spaces_settings(tmp_path, **over) -> Settings:
    base = dict(
        photos_dir=str(tmp_path / "photos"),
        do_spaces_bucket="consortgroup-growz-application-prod",
        do_spaces_key="KEY", do_spaces_secret="SECRET", do_spaces_region="fra1",
    )
    base.update(over)
    return Settings(**base)


def test_spaces_upload_returns_public_cdn_url(tmp_path):
    store = PhotoStore(_spaces_settings(tmp_path))
    fake = _FakeS3()
    store._client = fake  # inject; bypass real boto3

    url = store.save("user1", "chat9", "p1", b"jpegbytes", "image/jpeg")

    assert url == (
        "https://consortgroup-growz-application-prod.fra1.cdn.digitaloceanspaces.com"
        "/voiceagent/photos/user1/chat9/p1.jpg"
    )
    call = fake.calls[0]
    assert call["Bucket"] == "consortgroup-growz-application-prod"
    assert call["Key"] == "voiceagent/photos/user1/chat9/p1.jpg"
    assert call["ACL"] == "public-read"
    assert call["ContentType"] == "image/jpeg"
    assert call["Body"] == b"jpegbytes"
    # nothing written to local disk on a successful upload
    assert not (tmp_path / "photos").exists()


def test_spaces_upload_failure_falls_back_to_local_disk(tmp_path):
    store = PhotoStore(_spaces_settings(tmp_path))
    store._client = _FakeS3(exc=RuntimeError("spaces down"))

    path = store.save("u", "c", "p", b"bytes", "image/jpeg")

    # fell back to local disk (a real filesystem path, not a URL)
    assert path is not None and not path.startswith("http")
    assert Path(path) == (tmp_path / "photos" / "u" / "c" / "p.jpg")
    assert Path(path).read_bytes() == b"bytes"


def test_spaces_custom_public_base_is_honored(tmp_path):
    store = PhotoStore(_spaces_settings(
        tmp_path, do_spaces_public_base="https://cdn.growz.uz", do_spaces_prefix="va/pics",
    ))
    store._client = _FakeS3()
    url = store.save("u", "c", "p", b"x", "image/png")
    assert url == "https://cdn.growz.uz/va/pics/u/c/p.png"


def test_spaces_disabled_by_default_uses_local_disk(tmp_path):
    # No do_spaces_* set -> _spaces_enabled False -> local disk, no boto3 touched.
    store, root = _store(tmp_path)
    path = store.save("u", "c", "p", b"x", "image/jpeg")
    assert path == str(root / "u" / "c" / "p.jpg")


def test_spaces_upload_sanitizes_unsafe_segments_in_key(tmp_path):
    store = PhotoStore(_spaces_settings(tmp_path))
    fake = _FakeS3()
    store._client = fake
    store.save("../evil", "c", "p/../x", b"x", "image/jpeg")
    # A client-supplied "/" must NOT inject extra key hierarchy — each segment's
    # slashes are collapsed to "_", so the key has exactly the intended shape
    # (internal dots are harmless in a flat object key, unlike a filesystem path).
    assert fake.calls[0]["Key"] == "voiceagent/photos/_evil/c/p_.._x.jpg"
