"""Deterministic photo-quality checks — §4a of diagnostic_flow.md.

Pure, synchronous, PIL + numpy only (no opencv, no imagehash). Every public
function is a fast, best-effort classifier: blur (variance of Laplacian),
exposure (histogram tail fractions) and a perceptual dHash for near-duplicate
detection. NEVER raises — any decode/compute error degrades to a neutral
"ok" report (``failed=True``) so a bad/corrupt photo never blocks the flow.

Advisory only: callers combine this with the VLM verdict (``photo_check.py``)
to decide ``image_confidence``; nothing here rejects a photo outright.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.config import Settings

logger = logging.getLogger("voice.pipeline.photo_quality")

#: Bound the working image so blur/exposure thresholds (calibrated at this
#: size) stay meaningful and CPU cost stays flat regardless of upload size.
_WORK_SIZE = (512, 512)

# 3x3 discrete Laplacian kernel: [[0,1,0],[1,-4,1],[0,1,0]].
_LAPLACIAN = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)


@dataclass
class QualityReport:
    blur_var: float
    blurry: bool
    too_dark: bool
    too_bright: bool
    dhash: int          # 64-bit int; 0 when failed
    failed: bool = False  # decode/compute error -> treat photo as OK


def _laplacian_var(gray: np.ndarray) -> float:
    """Variance of the 3x3 Laplacian response, via manual shifted-array sums
    (no scipy). ``gray`` is a 2D float array; borders are dropped (valid-mode
    convolution equivalent) so no padding artefacts skew the variance."""
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    acc = np.zeros((h - 2, w - 2), dtype=np.float64)
    # Kernel taps at offsets (dy, dx) relative to the center pixel.
    taps = [(0, 1, 1.0), (1, 0, 1.0), (1, 2, 1.0), (2, 1, 1.0), (1, 1, -4.0)]
    for dy, dx, weight in taps:
        acc += weight * gray[dy : dy + h - 2, dx : dx + w - 2]
    return float(acc.var())


def _dhash(gray_img: "Image.Image") -> int:
    """64-bit difference hash: resize to 9x8 grayscale, one bit per
    horizontal gradient (px[x] > px[x+1]), packed MSB-first row by row."""
    small = gray_img.resize((9, 8))
    pixels = np.asarray(small, dtype=np.int16)
    bits = pixels[:, :-1] > pixels[:, 1:]  # (8, 8) bool grid
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def assess_photo_quality(data: bytes, settings: Settings) -> QualityReport:
    """Classify one photo's blur/exposure and compute its dHash. Never raises
    — any failure degrades to a neutral, non-blurry, non-dup ``QualityReport``
    with ``failed=True`` (the photo is then treated as "ok")."""
    try:
        img = Image.open(io.BytesIO(data))
        gray_img = img.convert("L")
        gray_img.thumbnail(_WORK_SIZE)
        gray = np.asarray(gray_img, dtype=np.float64)

        blur_var = _laplacian_var(gray)
        blurry = blur_var < settings.photo_blur_var_threshold

        hist = gray_img.histogram()  # 256 bins, 0..255
        total = sum(hist) or 1
        dark_frac = sum(hist[:16]) / total
        bright_frac = sum(hist[240:]) / total
        too_dark = dark_frac >= settings.photo_exposure_clip_frac
        too_bright = bright_frac >= settings.photo_exposure_clip_frac

        dhash = _dhash(gray_img)

        return QualityReport(
            blur_var=blur_var, blurry=blurry, too_dark=too_dark,
            too_bright=too_bright, dhash=dhash, failed=False,
        )
    except Exception:  # noqa: BLE001 — quality checks must never block a photo
        logger.warning("photo quality assessment failed", exc_info=True)
        return QualityReport(
            blur_var=0.0, blurry=False, too_dark=False, too_bright=False,
            dhash=0, failed=True,
        )


def dhash_distance(a: int, b: int) -> int:
    """Hamming distance between two 64-bit dHash values."""
    return bin(a ^ b).count("1")
