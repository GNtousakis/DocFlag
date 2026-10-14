"""Image comparison of two captured regions.

Pipeline: grayscale -> ORB + RANSAC similarity transform aligns B onto A (so small shifts / zoom
differences are not reported) -> slight blur -> SSIM map -> threshold + morphology -> boxes + heat map.

The similarity percentage is the mean SSIM over the *content* area (dilated ink of A or B), so a mostly
white page does not inflate the score. No Qt here; inputs and outputs are PIL images.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw

MAX_SIDE = 2400          # working resolution cap (keeps ORB/SSIM in the tens of ms)
ORB_FEATURES = 3000
MIN_MATCHES = 12
MIN_INLIERS = 8
MIN_INLIER_RATIO = 0.3
SCALE_RANGE = (0.25, 4.0)
MAX_ROTATION_DEG = 10.0
ASPECT_TOLERANCE = 0.05
INK_DELTA = 30           # gray distance from the background that counts as content
CONTENT_DILATE = 15      # px; content area grows by this much around ink
SSIM_WINDOW = 11
SSIM_SIGMA = 1.5

WARN_RESIZED = "resized"        # alignment failed, B was stretched to A's size
WARN_ASPECT = "aspect"          # ... and the aspect ratios differ noticeably


@dataclass(frozen=True)
class ImageDiffOptions:
    blur_sigma: float = 1.0      # pre-blur before SSIM
    threshold: float = 0.4       # 1 - SSIM above this marks a pixel as changed
    min_area: int = 40           # ignore changed blobs smaller than this (px^2)
    merge_px: int = 9            # blobs closer than this are merged into one box

    @classmethod
    def from_sensitivity(cls, level: int) -> "ImageDiffOptions":
        """level 0 (tolerant) .. 2 (strict); 1 is the default."""
        presets = {
            0: cls(threshold=0.55, min_area=120),
            1: cls(),
            2: cls(threshold=0.25, min_area=16),
        }
        return presets[max(0, min(2, level))]


@dataclass
class ImageDiffResult:
    similarity: float                                 # 0..100
    boxes: list[tuple[int, int, int, int]] = field(default_factory=list)  # x, y, w, h in `a` coordinates
    a: Image.Image | None = None                      # A at working resolution
    b: Image.Image | None = None                      # B aligned onto A (same size as `a`)
    heat: Image.Image | None = None                   # RGBA overlay, same size as `a`
    aligned: bool = True                              # False when the fallback resize was used
    warning: str | None = None
    scale: float = 1.0                                # transform applied to B (diagnostics)
    shift: tuple[float, float] = (0.0, 0.0)

    @property
    def regions(self) -> int:
        return len(self.boxes)


def _cap(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    longest = max(img.size)
    if longest <= MAX_SIDE:
        return img
    f = MAX_SIDE / longest
    return img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)


def _estimate_transform(ga: np.ndarray, gb: np.ndarray):
    """2x3 matrix mapping B coordinates to A coordinates, or None when no trustworthy match exists."""
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < MIN_MATCHES or len(kb) < MIN_MATCHES:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(db, da, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < MIN_MATCHES:
        return None
    src = np.float32([kb[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([ka[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    m, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if m is None or inliers is None:
        return None
    n_in = int(inliers.sum())
    if n_in < MIN_INLIERS or n_in < MIN_INLIER_RATIO * len(good):
        return None
    scale = math.hypot(m[0, 0], m[1, 0])
    angle = math.degrees(math.atan2(m[1, 0], m[0, 0]))
    if not (SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]) or abs(angle) > MAX_ROTATION_DEG:
        return None
    return m


def _ssim_map(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k = (SSIM_WINDOW, SSIM_WINDOW)

    def blur(v: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(v, k, SSIM_SIGMA)

    mx, my = blur(x), blur(y)
    vx = blur(x * x) - mx * mx
    vy = blur(y * y) - my * my
    cxy = blur(x * y) - mx * my
    return ((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2))


def _ink(gray: np.ndarray) -> np.ndarray:
    """Content pixels, independent of light/dark theme: far from the (median) background."""
    bg = float(np.median(gray))
    return np.abs(gray.astype(np.float32) - bg) > INK_DELTA


def compare_images(a: Image.Image, b: Image.Image, options: ImageDiffOptions = ImageDiffOptions()) -> ImageDiffResult:
    a, b = _cap(a), _cap(b)
    w, h = a.size
    ga = cv2.cvtColor(np.asarray(a), cv2.COLOR_RGB2GRAY)
    gb_full = cv2.cvtColor(np.asarray(b), cv2.COLOR_RGB2GRAY)

    m = _estimate_transform(ga, gb_full)
    warning = None
    if m is not None:
        rgb_b = cv2.warpAffine(np.asarray(b), m, (w, h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
        valid = cv2.warpAffine(np.full(gb_full.shape, 255, np.uint8), m, (w, h), flags=cv2.INTER_NEAREST) > 0
        scale = math.hypot(m[0, 0], m[1, 0])
        shift = (float(m[0, 2]), float(m[1, 2]))
        aligned = True
    else:
        rgb_b = np.asarray(b.resize((w, h), Image.LANCZOS))
        valid = np.ones((h, w), bool)
        scale, shift, aligned = 1.0, (0.0, 0.0), False
        ar_a, ar_b = w / h, b.width / b.height
        warning = WARN_ASPECT if abs(ar_a - ar_b) / ar_a > ASPECT_TOLERANCE else WARN_RESIZED
    gb = cv2.cvtColor(rgb_b, cv2.COLOR_RGB2GRAY)

    sig = options.blur_sigma
    fa = cv2.GaussianBlur(ga.astype(np.float32), (0, 0), sig)
    fb = cv2.GaussianBlur(gb.astype(np.float32), (0, 0), sig)
    ssim = _ssim_map(fa, fb)

    content = (_ink(ga) | (_ink(gb) & valid)) & valid
    content = cv2.dilate(content.astype(np.uint8), np.ones((CONTENT_DILATE, CONTENT_DILATE), np.uint8)) > 0
    content &= valid

    diff = np.clip(1.0 - ssim, 0.0, 1.0)
    diff[~content] = 0.0
    similarity = 100.0 if not content.any() else float(np.clip(ssim[content].mean(), 0.0, 1.0) * 100.0)

    changed = (diff > options.threshold).astype(np.uint8)
    k = options.merge_px
    blobs = cv2.dilate(changed, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    blob_mask = np.zeros((h, w), np.uint8)
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if (changed[y : y + bh, x : x + bw]).sum() < options.min_area:
            continue
        boxes.append((x, y, bw, bh))
        cv2.drawContours(blob_mask, [c], -1, 1, thickness=cv2.FILLED)
    boxes.sort(key=lambda r: (r[1], r[0]))

    alpha = np.where(blob_mask > 0, np.clip(diff * 255, 40, 110), 0).astype(np.uint8)
    heat = np.zeros((h, w, 4), np.uint8)
    heat[..., 0] = 230
    heat[..., 3] = alpha

    return ImageDiffResult(
        similarity=similarity, boxes=boxes, a=a, b=Image.fromarray(rgb_b),
        heat=Image.fromarray(heat, "RGBA"), aligned=aligned, warning=warning, scale=scale, shift=shift,
    )


def render_overlay(image: Image.Image, boxes, heat: Image.Image | None = None,
                   color: tuple[int, int, int] = (220, 38, 38), width: int = 3) -> Image.Image:
    """Return ``image`` with optional heat overlay and rectangles around the changed regions."""
    out = image.convert("RGBA")
    if heat is not None:
        out = Image.alpha_composite(out, heat)
    d = ImageDraw.Draw(out)
    pad = 3
    for x, y, w, h in boxes:
        d.rectangle((x - pad, y - pad, x + w + pad, y + h + pad), outline=color + (255,), width=width)
    return out.convert("RGB")
