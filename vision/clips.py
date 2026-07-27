"""Short video clips around an event: N seconds before + M seconds after.

Frames are buffered as encoded JPEGs rather than raw arrays -- a 5 s pre-roll of
720p raw frames would be ~140 MB, while the JPEGs are a few MB. They're decoded
only when a clip is actually written, which happens on a worker thread so the
capture loop never stalls.
"""
import os
import shutil
import subprocess
import threading
from collections import deque

import cv2
import numpy as np


_FFMPEG = shutil.which("ffmpeg")


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
        if not frames:
            return
        ok = self._write_h264(frames, path) or self._write_cv2(frames, path)
        if ok and rec.get("on_done"):
            rec["on_done"](rec["filename"])

    def _write_h264(self, frames, path):
        """Encode H.264/yuv420p via ffmpeg -- the only format Safari/iOS will play.

        The buffered frames are already JPEGs, so they pipe straight into ffmpeg's
        mjpeg demuxer with no decode step.
        """
        if not _FFMPEG:
            return False
        cmd = [
            _FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(self.fps), "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p",                       # required by Safari
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # H.264 needs even dimensions
            "-movflags", "+faststart",                   # play before fully downloaded
            path,
        ]
        try:
            p = subprocess.run(cmd, input=b"".join(frames), capture_output=True, timeout=120)
            if p.returncode != 0:
                print(f"[clips] ffmpeg failed: {p.stderr.decode('utf-8', 'replace')[:300]}",
                      flush=True)
                return False
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception as e:
            print(f"[clips] ffmpeg error: {e}", flush=True)
            return False

    def _write_cv2(self, frames, path):
        """Fallback when ffmpeg is unavailable. Note: mp4v will NOT play on iOS."""
        try:
            first = cv2.imdecode(np.frombuffer(frames[0], np.uint8), cv2.IMREAD_COLOR)
            if first is None:
                return False
            h, w = first.shape[:2]
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
            if not writer.isOpened():
                print(f"[clips] could not open writer for {path}", flush=True)
                return False
            for jpeg in frames:
                img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                if img.shape[:2] != (h, w):
                    img = cv2.resize(img, (w, h))
                writer.write(img)
            writer.release()
            print("[clips] wrote mp4v (ffmpeg missing) -- will not play on iOS", flush=True)
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception as e:
            print(f"[clips] write failed: {e}", flush=True)
            return False

    def flush(self):
        """Finish any in-progress recordings (called on reload/shutdown)."""
        for cam_id in list(self._active):
            self._finish(cam_id)
