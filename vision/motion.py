import cv2


class MotionDetector:
    """Cheap frame-differencing gate. Returns (moved, changed_area_px) per frame.

    Runs on the CPU for effectively free, so it can inspect every frame and only
    wake the (heavier) object detector when something actually changes.
    """

    def __init__(self, min_area=3000, threshold=25, warmup_frames=30, blur=21):
        self.min_area = min_area
        self.threshold = threshold
        self.warmup_frames = warmup_frames
        self.blur = blur | 1  # kernel size must be odd
        self.prev = None
        self.count = 0

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
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        area = int(sum(cv2.contourArea(c) for c in contours))
        return area > self.min_area, area
