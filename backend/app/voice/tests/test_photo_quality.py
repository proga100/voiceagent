"""Deterministic §4a photo checks (photo_quality.py): blur, exposure, dHash
near-duplicate distance — all on synthetic in-memory PIL images. NEVER raises:
garbage bytes degrade to a neutral ``failed=True`` report."""
import io

import numpy as np
from PIL import Image

from app.config import Settings
from app.voice.pipeline.photo_quality import (
    QualityReport,
    assess_photo_quality,
    dhash_distance,
)


def _jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _noise(size: int = 256, lo: int = 0, hi: int = 256, seed: int = 7) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size, size), dtype=np.uint8)
    return Image.fromarray(arr, "L")


def _blocky(seed: int) -> Image.Image:
    """8x9 random coarse grid upscaled with NEAREST — its dHash bits follow the
    coarse gradients, so it is stable under JPEG re-encoding but two seeds
    produce clearly different hashes."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (8, 9), dtype=np.uint8)
    return Image.fromarray(coarse, "L").resize((288, 256), Image.NEAREST)


# ---- blur -------------------------------------------------------------------


def test_solid_image_is_blurry():
    report = assess_photo_quality(_jpeg(Image.new("L", (256, 256), 128)), Settings())
    assert not report.failed
    assert report.blur_var < 60.0 and report.blurry


def test_noise_image_is_sharp():
    report = assess_photo_quality(_jpeg(_noise()), Settings())
    assert not report.failed
    assert report.blur_var > 60.0 and not report.blurry


# ---- exposure ----------------------------------------------------------------


def test_near_black_image_is_too_dark():
    report = assess_photo_quality(_jpeg(Image.new("L", (256, 256), 3)), Settings())
    assert report.too_dark and not report.too_bright


def test_near_white_image_is_too_bright():
    report = assess_photo_quality(_png(Image.new("L", (256, 256), 252)), Settings())
    assert report.too_bright and not report.too_dark


def test_midtone_noise_is_neither_dark_nor_bright_nor_blurry():
    report = assess_photo_quality(_jpeg(_noise(lo=60, hi=200)), Settings())
    assert not report.too_dark and not report.too_bright and not report.blurry


# ---- dHash / duplicates -------------------------------------------------------


def test_same_bytes_hash_to_distance_zero():
    data = _jpeg(_blocky(1))
    a = assess_photo_quality(data, Settings())
    b = assess_photo_quality(data, Settings())
    assert dhash_distance(a.dhash, b.dhash) == 0


def test_reencoded_copy_stays_within_dup_threshold():
    img = _blocky(2)
    a = assess_photo_quality(_jpeg(img, quality=90), Settings())
    b = assess_photo_quality(_jpeg(img, quality=70), Settings())
    assert dhash_distance(a.dhash, b.dhash) <= Settings().photo_dup_hamming_max


def test_distinct_images_exceed_dup_threshold():
    a = assess_photo_quality(_jpeg(_blocky(3)), Settings())
    b = assess_photo_quality(_jpeg(_blocky(4)), Settings())
    assert dhash_distance(a.dhash, b.dhash) > Settings().photo_dup_hamming_max


def test_dhash_distance_bit_math():
    assert dhash_distance(0, 0) == 0
    assert dhash_distance(0b1011, 0b0011) == 1
    assert dhash_distance(0, (1 << 64) - 1) == 64


# ---- fail-open ----------------------------------------------------------------


def test_garbage_bytes_fail_open():
    report = assess_photo_quality(b"definitely not an image", Settings())
    assert report == QualityReport(
        blur_var=0.0, blurry=False, too_dark=False, too_bright=False,
        dhash=0, failed=True,
    )


def test_empty_bytes_fail_open():
    report = assess_photo_quality(b"", Settings())
    assert report.failed and not report.blurry


def test_tiny_image_does_not_raise():
    # 1x1 px: Laplacian window doesn't fit -> var 0.0 -> blurry, never a crash.
    report = assess_photo_quality(_png(Image.new("L", (1, 1), 128)), Settings())
    assert not report.failed
    assert report.blur_var == 0.0 and report.blurry


def test_palette_mode_image_does_not_raise():
    img = Image.new("P", (64, 64))
    report = assess_photo_quality(_png(img), Settings())
    assert isinstance(report, QualityReport)


def test_large_image_is_downscaled_and_classified():
    report = assess_photo_quality(_jpeg(Image.new("L", (4000, 3000), 128)), Settings())
    assert not report.failed and report.blurry
