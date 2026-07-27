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

# ---- web push: keys, subscribe/unsubscribe, redaction ----
from vision import push as vpush  # noqa: E402

priv, pub = vpush.generate_vapid_keys()
assert "BEGIN PRIVATE KEY" in priv and len(pub) > 80
print("PASS  web push: VAPID keypair generated")

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
    assert disk["notify"]["web_push"]["private_key"].startswith("-----BEGIN")
    print("PASS  web push: PUT config preserves redacted private key")

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
