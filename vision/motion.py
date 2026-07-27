import cv2


class MotionDetector:
    """Cheap frame-differencing gate. Returns (moved, changed_area_px) per frame.

    Runs on the CPU for effectively free, so it can inspect every frame and only
    wake the (heavier) object detector when something actually changes.
    """

    def __init__(self, min_area=3000, threshold=25, warmup_frames=30, blur=21, zone=None):
        self.min_area = min_area
        self.threshold = threshold
        self.warmup_frames = warmup_frames
        self.blur = blur | 1  # kernel size must be odd
        self.zone = zone      # normalized polygon; motion outside it is ignored
        self.prev = None
        self.count = 0
        self._mask = None
        self._mask_shape = None

    def _zone_mask(self, gray):
        """Cache the rasterized zone mask; rebuild if the frame size changes."""
        from . import zones
        if not zones.is_valid(self.zone):
            return None
        if self._mask is None or self._mask_shape != gray.shape:
            h, w = gray.shape[:2]
            self._mask = zones.build_mask(self.zone, w, h)
            self._mask_shape = gray.shape
        return self._mask

    def update(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (self.blur, self.blur), 0)
        self.count += 1

        if self.prev is None:
            self.prev = gray
            return False, 0

        delta = cv2.absdiff(self.prev, gray)
        self.prev = gray

        # ignore the first few frames while exposure/auto-gain settles
        if self.count <= self.warmup_frames:
            return False, 0

        thresh = cv2.threshold(delta, self.threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        mask = self._zone_mask(gray)
        if mask is not None:
            thresh = cv2.bitwise_and(thresh, mask)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        area = int(sum(cv2.contourArea(c) for c in contours))
        return area > self.min_area, area
