#!/usr/bin/env python3
"""Webcam helpers for Coin Tray — OpenCV capture, coin circle detect, ROI crop."""

from __future__ import annotations

import sys

try:
    import cv2
    import numpy as np
    AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    AVAILABLE = False

# Preview / guide size in the UI
PREVIEW_W = 560
PREVIEW_H = 420
# Fixed guide is this fraction of the shorter frame side
GUIDE_FRAC = 0.40
# Prefer detection hits within this fraction of center
CENTER_FRAC = 0.22
# Stability: max center drift / radius change between frames (fraction of r)
STABLE_CENTER = 0.18
STABLE_RADIUS = 0.15


def list_cameras(max_index=5):
    """Return [(index, label), ...] for cameras that open briefly.

    Safe to call from a background thread.
    """
    if not AVAILABLE:
        return []
    found = []
    for i in range(max_index):
        cap = open_camera(i)
        if cap is None:
            continue
        ok, _ = cap.read()
        cap.release()
        if ok:
            found.append((i, "Camera %d" % i))
    return found


def open_camera(index):
    """Open a camera; use DirectShow on Windows for Logitech reliability."""
    if not AVAILABLE:
        return None
    try:
        if sys.platform == "win32":
            cap = cv2.VideoCapture(int(index), cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(int(index))
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return cap
    except Exception:
        return None


def guide_circle(frame):
    """Fixed center capture guide as (cx, cy, radius) in frame coords."""
    h, w = frame.shape[:2]
    r = int(min(w, h) * GUIDE_FRAC / 2)
    return (w // 2, h // 2, max(r, 40))


def detect_coin(frame):
    """Find a coin-like circle near the center. Returns (cx, cy, r) or None."""
    if not AVAILABLE:
        return None
    h, w = frame.shape[:2]
    scale = 640.0 / max(w, 1)
    if scale > 1:
        scale = 1.0
    small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (9, 9), 2)
    min_r = int(min(small.shape[:2]) * 0.14)
    max_r = int(min(small.shape[:2]) * 0.42)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=min_r,
        param1=120, param2=42, minRadius=min_r, maxRadius=max_r)
    if circles is None:
        return None
    cx0, cy0 = small.shape[1] / 2.0, small.shape[0] / 2.0
    limit = min(small.shape[:2]) * CENTER_FRAC
    best = None
    best_score = 1e18
    for c in circles[0]:
        x, y, r = float(c[0]), float(c[1]), float(c[2])
        dist = ((x - cx0) ** 2 + (y - cy0) ** 2) ** 0.5
        if dist > limit:
            continue
        score = dist - r * 0.12
        if score < best_score:
            best_score = score
            best = (x, y, r)
    if best is None:
        return None
    x, y, r = best
    inv = 1.0 / scale
    return (int(x * inv), int(y * inv), int(r * inv))


def detection_stable(prev, cur):
    """True if cur is a continuity of prev (position + radius)."""
    if not prev or not cur:
        return False
    x0, y0, r0 = prev
    x1, y1, r1 = cur
    if r0 <= 0 or r1 <= 0:
        return False
    dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if dist > max(r0, r1) * STABLE_CENTER:
        return False
    if abs(r1 - r0) / float(r0) > STABLE_RADIUS:
        return False
    return True


def draw_overlays(frame, detected=None, progress=0.0):
    """Copy frame; draw guide, detection, and 0..1 hold progress arc."""
    out = frame.copy()
    gx, gy, gr = guide_circle(out)
    cv2.circle(out, (gx, gy), gr, (0, 180, 70), 2)
    cv2.circle(out, (gx, gy), 4, (0, 180, 70), -1)
    if detected:
        dx, dy, dr = detected
        cv2.circle(out, (dx, dy), dr, (0, 255, 90), 3)
        t = max(8, dr // 8)
        cv2.line(out, (dx - dr, dy - t), (dx - dr, dy + t), (0, 255, 90), 2)
        cv2.line(out, (dx + dr, dy - t), (dx + dr, dy + t), (0, 255, 90), 2)
        cv2.line(out, (dx - t, dy - dr), (dx + t, dy - dr), (0, 255, 90), 2)
        cv2.line(out, (dx - t, dy + dr), (dx + t, dy + dr), (0, 255, 90), 2)
        # Progress arc around detection (or guide if somehow missing)
        p = max(0.0, min(1.0, float(progress)))
        if p > 0.02:
            axes = (dr + 10, dr + 10)
            end = int(p * 360)
            cv2.ellipse(out, (dx, dy), axes, -90, 0, end, (0, 255, 180), 4)
    elif progress > 0.02:
        p = max(0.0, min(1.0, float(progress)))
        end = int(p * 360)
        cv2.ellipse(out, (gx, gy), (gr + 8, gr + 8), -90, 0, end, (0, 255, 180), 3)
    return out


def crop_roi(frame, circle=None, pad=0.12):
    """Square crop around circle (or fixed guide). Returns BGR image."""
    h, w = frame.shape[:2]
    if circle is None:
        circle = guide_circle(frame)
    cx, cy, r = circle
    rad = int(r * (1.0 + pad))
    x0 = max(0, cx - rad)
    y0 = max(0, cy - rad)
    x1 = min(w, cx + rad)
    y1 = min(h, cy + rad)
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return frame
    return crop


def frame_to_pil(frame_bgr):
    """BGR OpenCV frame → RGB PIL Image."""
    from PIL import Image
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def preview_photoimage(frame_bgr, size=(PREVIEW_W, PREVIEW_H)):
    """Resize overlay frame to preview size and return (PhotoImage, PIL)."""
    from PIL import Image, ImageTk
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    im = Image.fromarray(rgb)
    im = im.resize(size, Image.BILINEAR)
    return ImageTk.PhotoImage(im), im
