import cv2

_ROTATIONS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


class Camera:
    """Wraps a single UVC webcam (by /dev/videoN index) or an RTSP/HTTP URL string.

    fourcc/width/height are optional. Many USB webcams default to raw YUYV, which
    exceeds USB2 bandwidth at higher resolutions and yields no frames -- setting
    fourcc="MJPG" (compressed) fixes that. These are ignored for RTSP/HTTP sources.
    """

    def __init__(self, cam_id, source, rotate=0, fourcc=None, width=None, height=None):
        self.id = cam_id
        self.source = source
        self.rotate = int(rotate) % 360
        self.fourcc = fourcc or None
        self.width = int(width) if width else None
        self.height = int(height) if height else None
        self.cap = None

    def open(self):
        # Use the V4L2 backend explicitly for local device indices (Linux/Pi).
        if isinstance(self.source, int):
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
        else:
            self.cap = cv2.VideoCapture(self.source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera '{self.id}' (source={self.source!r})")
        # FOURCC must be set before width/height for most UVC drivers.
        if self.fourcc:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
        if self.width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
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
