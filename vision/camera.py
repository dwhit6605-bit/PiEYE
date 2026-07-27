import os

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

    def __init__(self, cam_id, source, rotate=0, fourcc=None, width=None, height=None,
                 transport="tcp", timeout_seconds=8, verify_tls=False):
        self.id = cam_id
        self.source = source
        self.rotate = int(rotate) % 360
        self.fourcc = fourcc or None
        self.width = int(width) if width else None
        self.height = int(height) if height else None
        self.transport = (transport or "tcp").lower()
        self.timeout_seconds = int(timeout_seconds or 8)
        self.verify_tls = bool(verify_tls)
        self.cap = None

    @property
    def is_network(self):
        return isinstance(self.source, str) and "://" in self.source

    def _ffmpeg_options(self):
        """RTSP over UDP drops packets and smears frames; TCP is far more reliable.
        A timeout matters even more -- without one a dead camera blocks the whole
        capture loop, since cameras are polled from a single thread."""
        us = self.timeout_seconds * 1_000_000
        opts = [f"stimeout;{us}", f"timeout;{us}"]
        src = str(self.source).lower()
        if src.startswith("rtsp://") or src.startswith("rtsps://"):
            opts.insert(0, f"rtsp_transport;{self.transport}")
        if src.startswith("rtsps://") and not self.verify_tls:
            # Cameras (Wyze included) ship self-signed certs; verification would
            # reject every stream.
            opts.append("tls_verify;0")
        return "|".join(opts)

    def open(self):
        # Use the V4L2 backend explicitly for local device indices (Linux/Pi).
        if isinstance(self.source, int):
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
        else:
            # OpenCV reads this env var when the capture is constructed.
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._ffmpeg_options()
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera '{self.id}' (source={self.source!r})")
        if self.is_network:
            # Keep only the newest frame; otherwise reads return stale buffered
            # frames and motion detection lags seconds behind reality.
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Capture-format settings apply to local UVC devices only; forcing them on
        # a network stream can break the decode. FOURCC must precede width/height.
        if not self.is_network:
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
