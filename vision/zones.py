"""Per-camera detection zones.

A zone is a polygon of NORMALIZED points -- [[x, y], ...] with x/y in 0..1 -- so it
keeps its meaning if the capture resolution changes. Fewer than 3 points means
"no zone" (watch the whole frame).

Motion is masked to the zone, and detections are kept only when their anchor point
(bottom-centre of the box, i.e. where a person meets the ground) is inside it.
"""
import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - cv2 always present at runtime
    cv2 = None


def is_valid(zone):
    return isinstance(zone, (list, tuple)) and len(zone) >= 3


def to_pixels(zone, width, height):
    """Normalized polygon -> int pixel array shaped (N, 1, 2) for cv2."""
    pts = [(int(round(float(x) * width)), int(round(float(y) * height))) for x, y in zone]
    return np.array(pts, dtype=np.int32).reshape((-1, 1, 2))


def build_mask(zone, width, height):
    """White (255) inside the polygon, black outside. None if the zone is unset."""
    if not is_valid(zone) or cv2 is None:
        return None
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [to_pixels(zone, width, height)], 255)
    return mask


def contains_point(zone, x, y, width, height):
    """Is pixel (x, y) inside the zone? True when no zone is configured."""
    if not is_valid(zone) or cv2 is None:
        return True
    poly = to_pixels(zone, width, height).reshape((-1, 2)).astype(np.int32)
    return cv2.pointPolygonTest(poly, (float(x), float(y)), False) >= 0


def detection_in_zone(zone, det, width, height):
    """Keep a detection if the bottom-centre of its box falls inside the zone."""
    if not is_valid(zone):
        return True
    x1, y1, x2, y2 = det.box
    return contains_point(zone, (x1 + x2) / 2.0, y2, width, height)


def draw_outline(frame, zone):
    """Faint outline of the zone, for annotated snapshots."""
    if not is_valid(zone) or cv2 is None:
        return frame
    h, w = frame.shape[:2]
    cv2.polylines(frame, [to_pixels(zone, w, h)], True, (0, 200, 255), 1)
    return frame
