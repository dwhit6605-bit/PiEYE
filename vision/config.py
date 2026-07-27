import copy
import os
import tempfile

import yaml

DEFAULTS = {
    "cameras": [{"id": "cam0", "source": 0, "rotate": 0}],
    "motion": {"min_area": 3000, "threshold": 25, "warmup_frames": 30},
    "detection": {
        "backend": "yolo",
        "model": "yolo11n.pt",
        "confidence": 0.45,
        "cooldown_seconds": 60,
        "classes_of_interest": ["person", "car", "truck", "bus", "bicycle",
                                "motorcycle", "dog", "cat"],
    },
    "arming": {
        "armed": True,             # when false: live view still works, no alerts
        "schedule_enabled": False,
        "arm_at": "22:00",         # auto-arm at this local time
        "disarm_at": "07:00",      # auto-disarm at this local time
    },
    "notify": {
        "ntfy_enabled": True,
        "alert_on_camera_down": True,   # push when a camera stops reporting
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "change-me",
        "priority": "high",
        "min_confidence_to_alert": 0.5,
        "web_push": {
            "enabled": False,
            # VAPID contact claim; must be a real mailto: or https: URI or push
            # services (Apple especially) reject the JWT with 403.
            "subject": "https://github.com/dwhit6605-bit/PiEYE",
            "public_key": None,      # auto-generated when enabled
            "private_key": None,     # secret -- redacted from the API
        },
    },
    "claude": {"enabled": False, "model": "claude-haiku-4-5-20251001"},
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "auth_token": None,          # optional machine token (X-Auth-Token header)
        "live_fps": 10,              # MJPEG frame rate while someone is watching
        "secure_cookies": False,     # set True when served over HTTPS (behind a TLS proxy)
        "behind_proxy": False,       # trust X-Forwarded-* from a local reverse proxy
        "trusted_proxies": "127.0.0.1",  # forwarded_allow_ips for uvicorn
        "auth": {                    # username/password login for the web UI
            "enabled": False,
            "username": "admin",
            "password_hash": None,   # set via scripts/set_password.py
            "secret": None,          # HMAC session-signing key (auto-generated)
            "session_ttl_hours": 720,
            "max_attempts": 8,       # failed logins before lockout
            "lockout_minutes": 15,   # lockout duration after too many failures
        },
    },
    "storage": {
        "db_path": "data/events.db",
        "snapshot_dir": "data/snapshots",
        "retention_days": 14,
        "max_events": 5000,
        "clips": {                 # short video around each event
            "enabled": False,
            "pre_seconds": 4,      # buffered before the trigger
            "post_seconds": 6,     # recorded after
            "fps": 8,
        },
    },
    "loop_delay": 0.1,
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path):
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULTS, raw)


def validate_config(cfg):
    """Raise ValueError on obviously broken config coming from the web UI."""
    if not isinstance(cfg, dict):
        raise ValueError("config must be a mapping")
    cams = cfg.get("cameras")
    if not isinstance(cams, list) or not cams:
        raise ValueError("at least one camera is required")
    ids = set()
    for c in cams:
        if not isinstance(c, dict) or "id" not in c or "source" not in c:
            raise ValueError("each camera needs an 'id' and a 'source'")
        if c["id"] in ids:
            raise ValueError(f"duplicate camera id: {c['id']}")
        ids.add(c["id"])
    if cfg.get("detection", {}).get("backend") not in ("yolo", "none", "off", None):
        raise ValueError("detection.backend must be 'yolo' or 'none'")
    n = cfg.get("notify", {})
    if n.get("ntfy_enabled", True):
        topic = n.get("ntfy_topic", "")
        if not topic or topic == "change-me":
            raise ValueError("notify.ntfy_topic must be set to something unique "
                             "(or turn ntfy off and use web push)")
    return True


def save_config(path, cfg):
    """Atomically write config back to disk (used by the Settings tab)."""
    validate_config(cfg)
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
