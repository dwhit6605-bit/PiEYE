import cv2

_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


class Camera:
    """Wraps a single UVC webcam (by /dev/videoN index) or an RTSP/HTTP URL string."""

    def __init__(self, cam_id, source, rotate=0):
        self.id = cam_id
        self.source = source
        self.rotate = int(rotate) % 360
        self.cap = None

    def open(self):
        self.cap = cv2.VideoCapture(self.source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera '{self.id}' (source={self.source!r})")
        return self

    def read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        if self.rotate in _ROTATIONS:
            frame = cv2.rotate(frame, _ROTATIONS[self.rotate])
        return frame

    def release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
