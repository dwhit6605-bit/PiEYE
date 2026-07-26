import cv2


class Detection:
    __slots__ = ("label", "confidence", "box")

    def __init__(self, label, confidence, box):
        self.label = label
        self.confidence = confidence
        self.box = box  # (x1, y1, x2, y2)


class YoloDetector:
    """Ultralytics YOLO-nano. Weights auto-download on first run (needs internet ONCE).

    On a Pi 4 a single inference is ~0.3-1s. We only call it on motion frames, so
    that latency is fine for a security trigger.
    """

    def __init__(self, model="yolo11n.pt", confidence=0.45, classes=None):
        from ultralytics import YOLO  # imported lazily so `backend: none` needs no torch
        self.model = YOLO(model)
        self.confidence = confidence
        self.classes = set(classes) if classes else None

    def detect(self, frame):
        result = self.model(frame, verbose=False, conf=self.confidence)[0]
        dets = []
        for b in result.boxes:
            label = result.names[int(b.cls)]
            if self.classes and label not in self.classes:
                continue
            box = tuple(int(v) for v in b.xyxy[0])
            dets.append(Detection(label, float(b.conf), box))
        return dets


def build_detector(cfg):
    backend = (cfg.get("backend") or "yolo").lower()
    if backend in ("none", "off"):
        return None
    if backend == "yolo":
        return YoloDetector(
            model=cfg.get("model", "yolo11n.pt"),
            confidence=cfg.get("confidence", 0.45),
            classes=cfg.get("classes_of_interest"),
        )
    raise ValueError(f"Unknown detection backend: {backend!r}")


def annotate(frame, detections):
    """Draw red boxes + labels in place, return the frame."""
    for d in detections:
        x1, y1, x2, y2 = d.box
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(
            frame, f"{d.label} {d.confidence:.0%}", (x1, max(y1 - 8, 14)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2,
        )
    return frame
