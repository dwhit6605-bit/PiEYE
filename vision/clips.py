"""Short video clips around an event: N seconds before + M seconds after.

Frames are buffered as encoded JPEGs rather than raw arrays -- a 5 s pre-roll of
720p raw frames would be ~140 MB, while the JPEGs are a few MB. They're decoded
only when a clip is actually written, which happens on a worker thread so the
capture loop never stalls.
"""
import os
import threading
from collections import deque

import cv2
import numpy as np


class ClipRecorder:
    """Per-camera pre/post-event frame buffering and MP4 writing."""

    def __init__(self, clip_dir, pre_seconds=4, post_seconds=6, fps=8, quality=70):
        self.clip_dir = clip_dir
        self.pre = max(0, float(pre_seconds))
        self.post = max(0, float(post_seconds))
        self.fps = max(1, int(fps))
        self.quality = int(quality)
        os.makedirs(clip_dir, exist_ok=True)
        self._buf = {}        # cam_id -> deque of jpeg bytes
        self._last = {}       # cam_id -> last buffered timestamp
        self._active = {}     # cam_id -> in-progress recording

    # ---- buffering ----
    def offer(self, cam_id, frame, now):
        """Feed a frame in; kept only at the configured clip fps."""
        interval = 1.0 / self.fps
        if now - self._last.get(cam_id, 0) < interval:
            return
        self._last[cam_id] = now
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return
        jpeg = buf.tobytes()
        if cam_id not in self._buf:
            self._buf[cam_id] = deque(maxlen=max(1, int(self.pre * self.fps)))
        self._buf[cam_id].append(jpeg)
        rec = self._active.get(cam_id)
        if rec is not None:
            rec["frames"].append(jpeg)
            if now >= rec["until"]:
                self._finish(cam_id)

    # ---- recording ----
    def start(self, cam_id, now, filename, on_done=None):
        """Begin a clip: pre-roll from the buffer, then post-roll frames."""
        if cam_id in self._active:
            return False
        self._active[cam_id] = {
            "frames": list(self._buf.get(cam_id, [])),
            "until": now + self.post,
            "filename": filename,
            "on_done": on_done,
        }
        return True

    def _finish(self, cam_id):
        rec = self._active.pop(cam_id, None)
        if not rec:
            return
        threading.Thread(target=self._write, args=(rec,), daemon=True).start()

    def _write(self, rec):
        frames, path = rec["frames"], os.path.join(self.clip_dir, rec["filename"])
        try:
            if not frames:
                return
            first = cv2.imdecode(np.frombuffer(frames[0], np.uint8), cv2.IMREAD_COLOR)
            if first is None:
                return
            h, w = first.shape[:2]
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"),
                                     self.fps, (w, h))
            if not writer.isOpened():
                print(f"[clips] could not open writer for {path}", flush=True)
                return
            for jpeg in frames:
                img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                if img.shape[:2] != (h, w):
                    img = cv2.resize(img, (w, h))
                writer.write(img)
            writer.release()
            if rec.get("on_done"):
                rec["on_done"](rec["filename"])
        except Exception as e:
            print(f"[clips] write failed: {e}", flush=True)

    def flush(self):
        """Finish any in-progress recordings (called on reload/shutdown)."""
        for cam_id in list(self._active):
            self._finish(cam_id)
