"""Dependency-free auth: PBKDF2 password hashing + HMAC-signed session cookies."""
import base64
import hashlib
import hmac
import json
import os
import threading
import time

PBKDF2_ITERATIONS = 200_000


class LoginLimiter:
    """In-memory failed-login throttle, keyed by client-ip + username.

    After `max_attempts` failures within the lockout window, that key is locked
    for `lockout_seconds`. State is per-process (resets on restart) -- enough to
    stop credential stuffing without any external dependency.
    """

    def __init__(self, max_attempts=8, lockout_seconds=900):
        self.max = max(1, int(max_attempts))
        self.lockout = max(1, int(lockout_seconds))
        self._fails = {}
        self._lock = threading.Lock()

    def _recent(self, key):
        now = time.time()
        fails = [t for t in self._fails.get(key, []) if now - t < self.lockout]
        if fails:
            self._fails[key] = fails
        else:
            self._fails.pop(key, None)
        return fails

    def locked_for(self, key):
        """Seconds remaining in lockout, or 0 if allowed."""
        with self._lock:
            fails = self._recent(key)
            if len(fails) >= self.max:
                return max(0, int(fails[-1] + self.lockout - time.time()))
            return 0

    def record_failure(self, key):
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key):
        with self._lock:
            self._fails.pop(key, None)


def generate_secret(n=32):
    return os.urandom(n).hex()


# ---- passwords ---------------------------------------------------------
def hash_password(password, iterations=PBKDF2_ITERATIONS):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---- session tokens ----------------------------------------------------
def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(username, secret, ttl_hours=720, now=None):
    exp = int((now if now is not None else time.time()) + ttl_hours * 3600)
    payload = _b64e(json.dumps({"u": username, "exp": exp}).encode())
    sig = _b64e(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_session(token, secret, now=None):
    try:
        payload, sig = token.split(".")
        expected = _b64e(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64d(payload))
        if data.get("exp", 0) < (now if now is not None else time.time()):
            return None
        return data.get("u")
    except Exception:
        return None


# ---- config-aware helpers ----------------------------------------------
def auth_conf(cfg):
    return cfg.get("server", {}).get("auth", {}) or {}


def login_enabled(cfg):
    return bool(auth_conf(cfg).get("enabled"))


def auth_active(cfg):
    """True if any gate is configured (login OR a machine auth_token)."""
    return login_enabled(cfg) or bool(cfg.get("server", {}).get("auth_token"))


def check_credentials(cfg, username, password):
    a = auth_conf(cfg)
    if not a.get("enabled") or not a.get("password_hash"):
        return False
    return username == a.get("username") and verify_password(password or "", a["password_hash"])


def request_authorized(cfg, cookies, headers):
    """cookies/headers are plain dicts (case-insensitive lookups handled by caller)."""
    if not auth_active(cfg):
        return True
    token = cfg["server"].get("auth_token")
    if token and headers.get("x-auth-token") == token:
        return True
    a = auth_conf(cfg)
    if a.get("enabled") and a.get("secret"):
        c = cookies.get("pv_session")
        if c and verify_session(c, a["secret"]):
            return True
    return False
