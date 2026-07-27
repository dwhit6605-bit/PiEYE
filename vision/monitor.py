import threading
import time
from datetime import datetime

import cv2

from .camera import Camera
from .motion import MotionDetector
from .detector import build_detector, annotate
from .notifier import NtfyNotifier


class Monitor:
    """Runs the capture -> motion -> detect -> notify/store loop.

    Usable headless (call run() on the main thread) or under the web server
    (call start() to run in a background thread). Config can be hot-reloaded
    from the Settings tab via request_reload().
    """

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self._stop = threading.Event()
        self._reload = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.latest_jpeg = {}       # camera_id -> jpeg bytes (for the Live tab)
        self._last_live_encode = {}
        self.live_fps = 10
        self._viewers = 0           # active MJPEG stream clients
        self._viewers_lock = threading.Lock()
        self.status = {"running": False, "cameras": [], "backend": None, "claude": False,
                       "last_event": None, "error": None}

    # ---- live-stream viewer accounting (drives adaptive FPS) -------------
    def add_viewer(self):
        with self._viewers_lock:
            self._viewers += 1

    def remove_viewer(self):
        with self._viewers_lock:
            self._viewers = max(0, self._viewers - 1)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self.run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def request_reload(self, new_cfg):
        with self._lock:
            self.cfg = new_cfg
        self._reload.set()

    # ---- build from config ----------------------------------------------
    def _build(self):
        with self._lock:
            cfg = self.cfg
        cams = []
        failed = {}
        for c in cfg["cameras"]:
            cam_id = c.get("id", "?")
            try:
                cam = Camera(cam_id, c["source"], c.get("rotate", 0),
                             fourcc=c.get("fourcc"), width=c.get("width"),
                             height=c.get("height")).open()
            except Exception as e:
                # One bad camera must not take down the others.
                failed[cam_id] = str(e)
                print(f"[monitor] camera '{cam_id}' unavailable: {e}", flush=True)
                continue
            cams.append((cam, MotionDetector(**{k: cfg["motion"][k] for k in
                         ("min_area", "threshold", "warmup_frames") if k in cfg["motion"]})))
        detector = build_detector(cfg["detection"])
        self.live_fps = cfg["server"].get("live_fps", 10)
        n = cfg["notify"]
        notifier = None
        if n.get("ntfy_enabled", True):
            notifier = NtfyNotifier(n.get("ntfy_server"), n.get("ntfy_topic"),
                                    n.get("priority", "high"), n.get("ntfy_token"))
        wp = n.get("web_push", {})
        pusher = None
        if wp.get("enabled") and wp.get("private_key"):
            from .push import WebPushSender
            pusher = WebPushSender(self.store, wp["private_key"],
                                   wp.get("subject", "mailto:pieye@localhost"))
        self._pusher = pusher
        describer = None
        if cfg["claude"].get("enabled"):
            from .describe import ClaudeDescriber
            describer = ClaudeDescriber(cfg["claude"].get("model"))
        self.status.update({
            "cameras": [c.id for c, _ in cams],
            "failed_cameras": failed,
            "backend": cfg["detection"].get("backend", "yolo"),
            "claude": bool(describer),
            "error": (f"{len(failed)} camera(s) unavailable: "
                      + "; ".join(f"{k} ({v})" for k, v in failed.items())) if failed else None,
        })
        return cfg, cams, detector, notifier, describer

    def _teardown(self, cams):
        for cam, _ in cams:
            cam.release()

    def _describe(self, describer, frame, labels):
        try:
            return describer.describe(frame, hint=f"Local detector saw: {labels}.")
        except Exception as e:
            print(f"[claude] describe failed: {e}", flush=True)
            return None

    # ---- main loop -------------------------------------------------------
    def run(self):
        self.status["running"] = True
        while not self._stop.is_set():
            self._reload.clear()
            try:
                cfg, cams, detector, notifier, describer = self._build()
            except Exception as e:
                self.status["error"] = str(e)
                print(f"[monitor] build failed: {e}", flush=True)
                if self._stop.wait(5):
                    break
                continue

            interest = set(cfg["detection"].get("classes_of_interest") or [])
            min_conf = cfg["notify"].get("min_confidence_to_alert", 0.5)
            cooldown = cfg["detection"].get("cooldown_seconds", 60)
            loop_delay = cfg.get("loop_delay", 0.1)
            retention = cfg["storage"].get("retention_days", 14)
            max_events = cfg["storage"].get("max_events", 5000)
            last_alert = {}
            last_prune = 0
            dead_reads = {}
            last_retry = time.time()
            retry_every = cfg["detection"].get("camera_retry_seconds", 60)
            print(f"[monitor] running: cams={self.status['cameras']} "
                  f"failed={list(self.status.get('failed_cameras') or {})} "
                  f"backend={self.status['backend']} claude={self.status['claude']}", flush=True)

            # Nothing opened at all -- wait and retry rather than spinning hot.
            if not cams:
                if self._stop.wait(min(retry_every, 15)):
                    break
                continue

            while not self._stop.is_set() and not self._reload.is_set():
                for cam, motion in cams:
                    frame = cam.read()
                    if frame is None:
                        # A camera that stops delivering (unplugged, crashed) gets
                        # a rebuild so the rest keep running and it can recover.
                        dead_reads[cam.id] = dead_reads.get(cam.id, 0) + 1
                        if dead_reads[cam.id] == 150:
                            print(f"[monitor] camera '{cam.id}' stopped delivering "
                                  f"frames -- rebuilding", flush=True)
                            self._reload.set()
                        continue
                    dead_reads[cam.id] = 0
                    self._update_live(cam.id, frame)

                    moved, _ = motion.update(frame)
                    if not moved:
                        continue
                    now = time.time()
                    if now - last_alert.get(cam.id, 0) < cooldown:
                        continue

                    interesting = []
                    if detector is not None:
                        dets = detector.detect(frame)
                        interesting = [d for d in dets
                                       if (not interest or d.label in interest)
                                       and d.confidence >= min_conf]
                        if not interesting:
                            continue

                    last_alert[cam.id] = now
                    self._handle_alert(cam, frame, interesting, describer, notifier)

                # periodic retention sweep (~ every 10 min)
                if time.time() - last_prune > 600:
                    self.store.prune(retention, max_events)
                    last_prune = time.time()

                # retry cameras that were unavailable at build time
                if self.status.get("failed_cameras") and time.time() - last_retry > retry_every:
                    last_retry = time.time()
                    print("[monitor] retrying unavailable camera(s)", flush=True)
                    self._reload.set()

                time.sleep(loop_delay)

            self._teardown(cams)
        self.status["running"] = False

    def _update_live(self, cam_id, frame):
        # Encode fast (live_fps) only while someone is watching a stream;
        # otherwise idle at ~2 fps so it doesn't eat the Pi's CPU.
        interval = (1.0 / max(1, self.live_fps)) if self._viewers > 0 else 0.5
        now = time.time()
        if now - self._last_live_encode.get(cam_id, 0) < interval:
            return
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.latest_jpeg[cam_id] = buf.tobytes()
            self._last_live_encode[cam_id] = now

    def _handle_alert(self, cam, frame, interesting, describer, notifier):
        annotate(frame, interesting)
        labels = ", ".join(sorted({d.label for d in interesting})) or "motion"
        max_conf = max((d.confidence for d in interesting), default=None)
        message = None
        if describer is not None:
            message = self._describe(describer, frame, labels)
        if message is None:
            message = ("Detected " + ", ".join(f"{d.label} {d.confidence:.0%}"
                                                for d in interesting)) if interesting else "Motion detected"

        ts = time.time()
        iso = datetime.now().astimezone().isoformat(timespec="seconds")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        jpeg = buf.tobytes() if ok else None
        self.latest_jpeg[cam.id] = jpeg or self.latest_jpeg.get(cam.id)

        event_id = self.store.add_event(ts, iso, cam.id, labels, message, jpeg, max_conf)
        self.status["last_event"] = {"camera": cam.id, "labels": labels,
                                     "message": message, "iso": iso}
        title = f"{cam.id}: {labels}"
        print(f"[alert] {title} -- {message}", flush=True)

        if notifier is not None:
            notifier.send(title, message, frame=frame, tags=["rotating_light", "camera"])
        if getattr(self, "_pusher", None) is not None:
            row = self.store.get_event(event_id) or {}
            self._pusher.send(title, message, url="/#events",
                              snapshot=row.get("snapshot"), tag=f"pieye-{cam.id}")
