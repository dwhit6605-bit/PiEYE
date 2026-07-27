"""Local smoke test: store + web API + auth + MJPEG, no real camera needed.
Run:  ./.venv-test/bin/python scripts/smoketest.py"""
import os
import sys
import tempfile
import time

import cv2
import numpy as np
import yaml
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

work = tempfile.mkdtemp(prefix="pv-smoke-")
os.chdir(work)

# a tiny 12-frame test clip so a "camera" can open + produce live frames
vid = os.path.join(work, "clip.mp4")
w = cv2.VideoWriter(vid, cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
for i in range(12):
    w.write(np.full((120, 160, 3), i * 10 % 255, np.uint8))
w.release()


def write_cfg(name, auth=None, secure=False):
    cfg = {
        "cameras": [{"id": "testcam", "source": vid, "rotate": 0}],
        "detection": {"backend": "none"},
        "notify": {"ntfy_server": "https://ntfy.sh", "ntfy_topic": "pv-smoke-unique-123"},
        "server": {"host": "127.0.0.1", "port": 8099, "auth_token": None, "live_fps": 10,
                   "secure_cookies": secure},
        "storage": {"db_path": "data/events.db", "snapshot_dir": "data/snaps"},
    }
    if auth:
        cfg["server"]["auth"] = auth
    p = os.path.join(work, name)
    with open(p, "w") as f:
        yaml.safe_dump(cfg, f)
    return p


# ---- EventStore ----
from vision.store import EventStore  # noqa: E402
from vision import auth as vauth  # noqa: E402

st = EventStore("data/events.db", "data/snaps")
jpg = cv2.imencode(".jpg", np.zeros((60, 80, 3), np.uint8))[1].tobytes()
eid = st.add_event(time.time(), "2026-07-26T10:00:00", "testcam", "person", "Detected person", jpg, 0.91)
assert st.get_event(eid)["camera"] == "testcam"
assert len(st.list_events()) == 1 and st.stats()["total"] == 1
assert st.delete_event(eid) and st.get_event(eid) is None
print("PASS  EventStore add/get/list/stats/delete")

# ---- auth unit ----
h = vauth.hash_password("s3cret")
assert vauth.verify_password("s3cret", h) and not vauth.verify_password("wrong", h)
tok = vauth.make_session("admin", "sk", ttl_hours=1)
assert vauth.verify_session(tok, "sk") == "admin"
assert vauth.verify_session(tok, "different-secret") is None
assert vauth.verify_session(vauth.make_session("admin", "sk", ttl_hours=-1), "sk") is None
print("PASS  auth password hash + session sign/verify/expire")

lim = vauth.LoginLimiter(max_attempts=3, lockout_seconds=60)
assert lim.locked_for("k") == 0
for _ in range(3):
    lim.record_failure("k")
assert lim.locked_for("k") > 0 and lim.locked_for("other") == 0
lim.reset("k")
assert lim.locked_for("k") == 0
print("PASS  LoginLimiter locks after N failures, per-key, resettable")

from vision.server import create_app  # noqa: E402

# ---- open (no-auth) app: config + events + MJPEG ----
app = create_app(write_cfg("open.yaml"))
with TestClient(app) as c:
    time.sleep(0.8)  # let the monitor open the clip + encode a live frame
    assert c.get("/api/health").json()["ok"] is True
    assert c.get("/api/config").json()["notify"]["ntfy_topic"] == "pv-smoke-unique-123"
    print("PASS  open app: health + config")

    cfg = c.get("/api/config").json()
    cfg["notify"]["ntfy_topic"] = "pv-smoke-changed-456"
    assert c.put("/api/config", json=cfg).json()["reloaded"] is True
    assert yaml.safe_load(open(os.path.join(work, "open.yaml")))["notify"]["ntfy_topic"] == "pv-smoke-changed-456"
    print("PASS  open app: PUT config persists + reloads")

    idx = c.get("/")
    assert idx.status_code == 200 and "PiEYE" in idx.text
    assert c.get("/manifest.webmanifest").status_code == 200 and c.get("/sw.js").status_code == 200
    print("PASS  open app: PWA shell + manifest + sw")

    # MJPEG wiring: unknown camera -> 404 (bounded; the live stream itself is an
    # infinite response verified in-browser, not via TestClient).
    app.state.monitor.latest_jpeg["testcam"] = jpg
    assert c.get("/api/cameras/ghost/stream.mjpg").status_code == 404
    assert c.get("/api/cameras/testcam/live.jpg").status_code == 200
    print("PASS  open app: live.jpg + stream route wiring (404 on unknown cam)")

# ---- auth-enabled app: login gating ----
auth_cfg = {"enabled": True, "username": "dave",
            "password_hash": vauth.hash_password("hunter2"),
            "secret": vauth.generate_secret(), "session_ttl_hours": 720,
            "max_attempts": 8, "lockout_minutes": 15}
app2 = create_app(write_cfg("auth.yaml", auth=auth_cfg))
with TestClient(app2) as c:
    time.sleep(0.4)
    me = c.get("/api/me").json()
    assert me["auth_required"] is True and me["authenticated"] is False
    print("PASS  auth app: /api/me reports required + not-authed")

    assert c.get("/api/config").status_code == 401
    print("PASS  auth app: protected endpoint 401 without login")

    assert c.post("/api/login", json={"username": "dave", "password": "nope"}).status_code == 401
    print("PASS  auth app: wrong password rejected")

    r = c.post("/api/login", json={"username": "dave", "password": "hunter2"})
    assert r.status_code == 200 and "pv_session" in r.cookies
    print("PASS  auth app: login sets session cookie")

    assert c.get("/api/config").status_code == 200          # cookie now carried by client
    assert c.get("/api/me").json()["authenticated"] is True
    # secrets must be redacted to the browser
    got = c.get("/api/config").json()
    assert got["server"]["auth"]["password_hash"] == "__unchanged__"
    assert got["server"]["auth"]["secret"] == "__unchanged__"
    print("PASS  auth app: authed access + secrets redacted")

    # saving redacted config must NOT wipe the real hash/secret
    c.put("/api/config", json=got)
    disk = yaml.safe_load(open(os.path.join(work, "auth.yaml")))
    assert disk["server"]["auth"]["password_hash"] == auth_cfg["password_hash"]
    assert disk["server"]["auth"]["secret"] == auth_cfg["secret"]
    print("PASS  auth app: PUT preserves redacted secrets")

    c.post("/api/logout")
    assert c.get("/api/config").status_code == 401
    print("PASS  auth app: logout clears session")

# ---- rate-limiting: lockout after too many failures ----
rl_cfg = {"enabled": True, "username": "dave",
          "password_hash": vauth.hash_password("hunter2"),
          "secret": vauth.generate_secret(), "session_ttl_hours": 720,
          "max_attempts": 3, "lockout_minutes": 15}
app3 = create_app(write_cfg("rl.yaml", auth=rl_cfg))
with TestClient(app3) as c:
    for _ in range(3):
        assert c.post("/api/login", json={"username": "dave", "password": "nope"}).status_code == 401
    # even the CORRECT password is now refused with 429 during lockout
    r = c.post("/api/login", json={"username": "dave", "password": "hunter2"})
    assert r.status_code == 429, r.status_code
    print("PASS  rate-limit: locks out after 3 failures (correct pw -> 429)")

# ---- RTSP / network cameras ----
from vision.camera import Camera  # noqa: E402

_rt = Camera("c", "rtsp://192.0.2.1:8554/stream0", timeout_seconds=3)
assert _rt.is_network and not Camera("l", 0).is_network
_opts = _rt._ffmpeg_options()
assert "rtsp_transport;tcp" in _opts and "stimeout;3000000" in _opts, _opts
assert "rtsp_transport;udp" in Camera("u", "rtsp://x/y", transport="udp")._ffmpeg_options()
_t0 = time.time()
try:
    _rt.open()
    raise AssertionError("unreachable RTSP host should not open")
except RuntimeError:
    pass
# cameras share one thread, so a dead stream must not stall the loop
assert time.time() - _t0 < 25, "dead RTSP camera blocked for too long"
print("PASS  rtsp: tcp transport, buffer trim, and fail-fast timeout")

# ---- arming schedule ----
from vision import arming as varm  # noqa: E402
from vision.monitor import Monitor  # noqa: E402

assert varm.parse_hhmm("22:30", 0) == 22 * 60 + 30
assert varm.parse_hhmm("nonsense", 123) == 123 and varm.parse_hhmm("25:00", 7) == 7
print("PASS  arming: HH:MM parsing with fallback")

# overnight window 22:00 -> 07:00
for mins, want in [(23 * 60, True), (2 * 60, True), (6 * 60 + 59, True),
                   (7 * 60, False), (12 * 60, False), (21 * 60 + 59, False)]:
    got = varm.scheduled_armed(mins, "22:00", "07:00")
    assert got is want, f"overnight {mins // 60:02d}:{mins % 60:02d} -> {got}, want {want}"
# same-day window 09:00 -> 17:00
for mins, want in [(8 * 60, False), (9 * 60, True), (16 * 60, True), (17 * 60, False)]:
    assert varm.scheduled_armed(mins, "09:00", "17:00") is want
assert varm.scheduled_armed(600, "08:00", "08:00") is None
print("PASS  arming: schedule windows incl. overnight wrap and degenerate case")

# disarming must suppress alerting
_marm = Monitor.__new__(Monitor)
_marm.armed = True
_marm.status = {}
assert Monitor.set_armed(_marm, False) is False and _marm.armed is False
assert Monitor.set_armed(_marm, True) is True
print("PASS  arming: set_armed toggles state")

# ---- video clips ----
from vision.clips import ClipRecorder  # noqa: E402

clipdir = os.path.join(work, "clips")
rec = ClipRecorder(clipdir, pre_seconds=1, post_seconds=1, fps=5)
t = 1000.0
# pre-roll: feed frames before anything happens
for i in range(10):
    rec.offer("cam", np.full((120, 160, 3), (i * 20) % 255, np.uint8), t)
    t += 0.2
assert len(rec._buf["cam"]) == 5, "pre-roll ring buffer should cap at pre_seconds*fps"
done = []
assert rec.start("cam", t, "test.mp4", on_done=done.append) is True
assert rec.start("cam", t, "again.mp4") is False, "second start while recording must be refused"
for i in range(10):                      # post-roll
    t += 0.2
    rec.offer("cam", np.full((120, 160, 3), 90, np.uint8), t)
for _ in range(50):                      # writer runs on a thread
    if done:
        break
    time.sleep(0.1)
assert done == ["test.mp4"], f"on_done not called: {done}"
clip_file = os.path.join(clipdir, "test.mp4")
assert os.path.exists(clip_file) and os.path.getsize(clip_file) > 0
cap = cv2.VideoCapture(clip_file)
n = 0
while True:
    ok, _f = cap.read()
    if not ok:
        break
    n += 1
cap.release()
assert n >= 5, f"clip should contain pre+post frames, got {n}"
print(f"PASS  clips: pre/post buffering wrote a readable {n}-frame mp4")

# iOS/Safari will only play H.264 in yuv420p -- mp4v renders as a green screen.
import shutil as _sh  # noqa: E402
import subprocess as _sp  # noqa: E402
from vision.clips import _FFMPEG  # noqa: E402

if _FFMPEG and _sh.which("ffprobe"):
    probe = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=codec_name,pix_fmt,width,height", "-of", "default=nw=1",
                     clip_file], capture_output=True, text=True).stdout
    assert "codec_name=h264" in probe, f"clips must be H.264 for iOS:\n{probe}"
    assert "pix_fmt=yuv420p" in probe, f"clips must be yuv420p for Safari:\n{probe}"
    _w = int([l for l in probe.splitlines() if l.startswith("width=")][0].split("=")[1])
    _h = int([l for l in probe.splitlines() if l.startswith("height=")][0].split("=")[1])
    assert _w % 2 == 0 and _h % 2 == 0, "H.264 requires even dimensions"
    print("PASS  clips: encoded H.264/yuv420p with even dimensions (iOS-playable)")
else:
    print("SKIP  clips: ffmpeg/ffprobe unavailable, cannot verify H.264 output")

st3 = EventStore("data/events.db", "data/snaps")
eid3 = st3.add_event(time.time(), "2026-07-27T03:00:00", "cam", "person", "m", jpg, 0.9)
st3.set_event_clip(eid3, "test.mp4")
assert st3.get_event(eid3)["clip"] == "test.mp4"
print("PASS  clips: event row records the clip filename")

# ---- detection zones ----
from vision import zones as vz  # noqa: E402


class _Det:
    def __init__(self, box):
        self.box = box


assert not vz.is_valid(None) and not vz.is_valid([[0, 0], [1, 1]])
assert vz.is_valid([[0, 0], [1, 0], [1, 1]])
print("PASS  zones: validity requires >= 3 points")

# left half of a 640x480 frame
left = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
mask = vz.build_mask(left, 640, 480)
assert mask.shape == (480, 640)
assert mask[240, 100] == 255 and mask[240, 500] == 0, "mask must cover only the left half"
print("PASS  zones: mask rasterizes the polygon correctly")

assert vz.contains_point(left, 100, 240, 640, 480) is True
assert vz.contains_point(left, 500, 240, 640, 480) is False
assert vz.contains_point(None, 500, 240, 640, 480) is True, "no zone => everything counts"
print("PASS  zones: point-in-zone (and no-zone allows all)")

# anchor is the bottom-centre of the box
inside = _Det((80, 100, 200, 300))     # centre x=140 -> left half
outside = _Det((400, 100, 600, 300))   # centre x=500 -> right half
assert vz.detection_in_zone(left, inside, 640, 480) is True
assert vz.detection_in_zone(left, outside, 640, 480) is False
assert vz.detection_in_zone(None, outside, 640, 480) is True
print("PASS  zones: detections filtered by bottom-centre anchor")

# motion outside the zone must not trigger
from vision.motion import MotionDetector  # noqa: E402

md = MotionDetector(min_area=50, threshold=25, warmup_frames=0, zone=left)
base = np.zeros((480, 640, 3), np.uint8)
md.update(base)
right_blob = base.copy()
right_blob[200:300, 450:600] = 255          # big change, RIGHT half (outside zone)
moved, _ = md.update(right_blob)
assert moved is False, "motion outside the zone must be ignored"
md2 = MotionDetector(min_area=50, threshold=25, warmup_frames=0, zone=left)
md2.update(base)
left_blob = base.copy()
left_blob[200:300, 40:200] = 255            # same change, LEFT half (inside zone)
moved2, _ = md2.update(left_blob)
assert moved2 is True, "motion inside the zone must trigger"
print("PASS  zones: motion masked — outside ignored, inside triggers")

# ---- multi-camera resilience: a bad camera must not kill the good ones ----
multi = {
    "cameras": [{"id": "good", "source": vid}, {"id": "broken", "source": "/dev/does-not-exist"}],
    "detection": {"backend": "none"},
    "notify": {"ntfy_enabled": False, "ntfy_topic": "x"},
    "server": {"host": "127.0.0.1", "port": 8099},
    "storage": {"db_path": "data/events.db", "snapshot_dir": "data/snaps"},
}
mpath = os.path.join(work, "multi.yaml")
with open(mpath, "w") as f:
    yaml.safe_dump(multi, f)


from vision.config import load_config  # noqa: E402

mon = Monitor(load_config(mpath), EventStore("data/events.db", "data/snaps"))
_cfg, _cams, _det, _n, _d = mon._build()
opened = [c.id for c, _ in _cams]
assert "broken" in mon.status["failed_cameras"], "broken camera should be recorded as failed"
assert mon.status["error"] and "broken" in mon.status["error"]
for c, _ in _cams:
    c.release()
print(f"PASS  multi-camera: bad camera isolated (opened={opened}, failed=['broken'])")

# ---- web push: keys, subscribe/unsubscribe, redaction ----
from vision import push as vpush  # noqa: E402

priv, pub = vpush.generate_vapid_keys()
assert "-----" not in priv and len(priv) == 43 and len(pub) == 87
# The key MUST load through py_vapid the way pywebpush does it, and must derive
# the same public key we hand the browser -- otherwise pushes silently never send.
from py_vapid import Vapid02  # noqa: E402
from cryptography.hazmat.primitives import serialization as _ser  # noqa: E402
import base64 as _b64  # noqa: E402

_v = Vapid02.from_string(priv)
_derived = _b64.urlsafe_b64encode(_v.public_key.public_bytes(
    _ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
assert _derived == pub, "derived public key must match the one sent to browsers"
print("PASS  web push: VAPID keypair generated, loadable, and self-consistent")

# legacy PEM keys must migrate to the same keypair (don't invalidate subscriptions)
from cryptography.hazmat.primitives.asymmetric import ec as _ec  # noqa: E402

_k = _ec.generate_private_key(_ec.SECP256R1())
_pem = _k.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.PKCS8,
                        _ser.NoEncryption()).decode()
_mig = Vapid02.from_string(vpush.normalize_private_key(_pem))
assert _b64.urlsafe_b64encode(_mig.public_key.public_bytes(
    _ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)).rstrip(b"=").decode() == \
    _b64.urlsafe_b64encode(_k.public_key().public_bytes(
        _ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
print("PASS  web push: legacy PEM key migrates to the same keypair")

# VAPID `sub` must be a contactable mailto:/https: URI or Apple returns 403.
assert vpush.normalize_subject("mailto:pieye@localhost") == vpush.DEFAULT_SUBJECT
assert vpush.normalize_subject("") == vpush.DEFAULT_SUBJECT
assert vpush.normalize_subject(None) == vpush.DEFAULT_SUBJECT
assert vpush.normalize_subject("nonsense") == vpush.DEFAULT_SUBJECT
assert vpush.normalize_subject("mailto:me@example.com") == "mailto:me@example.com"
assert vpush.normalize_subject("me@example.com") == "mailto:me@example.com"
assert vpush.normalize_subject("https://pieye.example.com") == "https://pieye.example.com"
print("PASS  web push: VAPID subject normalization rejects uncontactable values")

st2 = EventStore("data/events.db", "data/snaps")
st2.add_push_sub("https://push.example/abc", '{"endpoint":"https://push.example/abc"}', "test-ua")
assert len(st2.list_push_subs()) == 1
st2.add_push_sub("https://push.example/abc", '{"endpoint":"https://push.example/abc"}')  # upsert
assert len(st2.list_push_subs()) == 1, "duplicate endpoint should upsert, not duplicate"
assert st2.delete_push_sub("https://push.example/abc") and len(st2.list_push_subs()) == 0
print("PASS  web push: subscription store add/upsert/delete")

app5 = create_app(write_cfg("push.yaml"))
with TestClient(app5) as c:
    assert c.get("/api/push/status").json()["enabled"] is False
    r = c.post("/api/push/enable")
    assert r.status_code == 200 and r.json()["public_key"], r.text
    pubkey = r.json()["public_key"]
    stat = c.get("/api/push/status").json()
    assert stat["enabled"] is True and stat["public_key"] == pubkey
    print("PASS  web push: enable generates + persists VAPID keys")

    # private key must never reach the browser
    got = c.get("/api/config").json()
    assert got["notify"]["web_push"]["private_key"] == "__unchanged__"
    assert got["notify"]["web_push"]["public_key"] == pubkey
    print("PASS  web push: private key redacted, public key exposed")

    sub = {"endpoint": "https://push.example/xyz", "keys": {"p256dh": "k", "auth": "a"}}
    assert c.post("/api/push/subscribe", json=sub).json()["subscriptions"] == 1
    assert c.post("/api/push/unsubscribe", json={"endpoint": sub["endpoint"]}).json()["subscriptions"] == 0
    print("PASS  web push: subscribe/unsubscribe endpoints")

    # saving a redacted config must not wipe the real private key
    c.put("/api/config", json=got)
    disk = yaml.safe_load(open(os.path.join(work, "push.yaml")))
    saved_priv = disk["notify"]["web_push"]["private_key"]
    assert saved_priv != "__unchanged__" and len(saved_priv) == 43, saved_priv
    assert Vapid02.from_string(saved_priv), "persisted key must still be loadable"
    print("PASS  web push: PUT config preserves a usable private key")

# ---- ntfy can be disabled without failing validation ----
from vision.config import validate_config  # noqa: E402
ok_cfg = {"cameras": [{"id": "c", "source": 0}], "detection": {"backend": "none"},
          "notify": {"ntfy_enabled": False, "ntfy_topic": "change-me"}}
assert validate_config(ok_cfg) is True
print("PASS  config: ntfy_enabled=false allows unset topic (push-only mode)")

# ---- secure_cookies flag emits a Secure cookie ----
app4 = create_app(write_cfg("secure.yaml", auth=auth_cfg, secure=True))
with TestClient(app4) as c:
    r = c.post("/api/login", json={"username": "dave", "password": "hunter2"})
    assert r.status_code == 200 and "secure" in r.headers.get("set-cookie", "").lower()
    print("PASS  secure_cookies: login Set-Cookie carries Secure attribute")

print("\nALL SMOKE TESTS PASSED")
