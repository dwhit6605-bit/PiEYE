"""Native Web Push (VAPID) — notifications straight from the PWA, no third party.

Requires the site to be served over HTTPS (browsers refuse push subscriptions on
plain http://, except on localhost). See docs/tls.md for free TLS options.
"""
import base64
import json


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_vapid_keys():
    """Return (private_key_b64url, public_key_b64url) for a fresh P-256 keypair.

    The private key is the RAW 32-byte scalar, base64url-encoded -- the standard
    VAPID format. pywebpush hands string keys to Vapid.from_string(), which only
    understands raw/DER, so a PEM here would fail to load at send time.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    raw_priv = key.private_numbers().private_value.to_bytes(32, "big")
    raw_pub = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url(raw_priv), _b64url(raw_pub)


#: RFC 8292 requires the VAPID `sub` claim to be a contactable mailto: or https:
#: URI. Apple's push service rejects anything else with 403 -- notably the old
#: `mailto:pieye@localhost` default, since localhost is not contactable.
DEFAULT_SUBJECT = "https://github.com/dwhit6605-bit/PiEYE"


def normalize_subject(subject):
    """Return a VAPID `sub` the push services will accept."""
    s = (subject or "").strip()
    if not s:
        return DEFAULT_SUBJECT
    if s.startswith("https://"):
        return s
    if s.startswith("mailto:"):
        addr = s[len("mailto:"):]
        # needs a real, routable-looking domain: user@host.tld
        if "@" in addr:
            domain = addr.rsplit("@", 1)[1]
            if "." in domain and "localhost" not in domain:
                return s
        return DEFAULT_SUBJECT
    if "@" in s and "." in s.rsplit("@", 1)[1]:   # bare email address
        return f"mailto:{s}"
    return DEFAULT_SUBJECT


def normalize_private_key(private_key):
    """Migrate a previously stored PEM key to raw base64url, same keypair.

    Early builds stored PKCS8 PEM, which pywebpush cannot load. Converting keeps
    the identical key, so public key and existing browser subscriptions stay valid.
    """
    if not private_key or "-----BEGIN" not in private_key:
        return private_key
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    return _b64url(key.private_numbers().private_value.to_bytes(32, "big"))


class WebPushSender:
    """Sends Web Push messages to every stored subscription.

    Subscriptions that the push service reports as gone (404/410) are pruned
    automatically, so uninstalled/expired clients don't accumulate.
    """

    def __init__(self, store, private_key, subject=None):
        self.store = store
        self.private_key = normalize_private_key(private_key)
        self.subject = normalize_subject(subject)

    def send(self, title, body, url="/#events", snapshot=None, tag="pieye"):
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            print("[push] pywebpush not installed -- skipping web push", flush=True)
            return 0

        payload = json.dumps({
            "title": title, "body": body, "url": url,
            "image": f"/api/snapshots/{snapshot}" if snapshot else None,
            "tag": tag,
        })

        sent = 0
        self.last_errors = []
        for sub in self.store.list_push_subs():
            try:
                webpush(
                    subscription_info=json.loads(sub["sub_json"]),
                    data=payload,
                    vapid_private_key=self.private_key,
                    vapid_claims={"sub": self.subject},
                    timeout=10,
                )
                sent += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    self.store.delete_push_sub(sub["endpoint"])
                    print(f"[push] pruned expired subscription ({status})", flush=True)
                    continue
                # The status alone is rarely enough -- the body says WHY.
                body = ""
                try:
                    body = (e.response.text or "")[:300]
                except Exception:
                    pass
                host = sub["endpoint"].split("/")[2] if "://" in sub["endpoint"] else "?"
                print(f"[push] send failed ({status}) to {host}: {body or e}", flush=True)
                self.last_errors.append(f"{status} from {host}: {body[:120] or 'no detail'}")
                if status == 403:
                    print(f"[push] 403 usually means the VAPID 'sub' claim is rejected "
                          f"(currently {self.subject!r}) or the subscription was made "
                          f"with a different key -- re-enable push on the device.",
                          flush=True)
            except Exception as e:  # never let a push error break an alert
                print(f"[push] send error: {e}", flush=True)
        return sent
