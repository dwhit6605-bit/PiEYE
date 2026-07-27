"""Native Web Push (VAPID) — notifications straight from the PWA, no third party.

Requires the site to be served over HTTPS (browsers refuse push subscriptions on
plain http://, except on localhost). See docs/tls.md for free TLS options.
"""
import base64
import json


def generate_vapid_keys():
    """Return (private_pem, public_key_b64url) for a fresh P-256 VAPID keypair."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw_pub = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    return private_pem, public_b64


class WebPushSender:
    """Sends Web Push messages to every stored subscription.

    Subscriptions that the push service reports as gone (404/410) are pruned
    automatically, so uninstalled/expired clients don't accumulate.
    """

    def __init__(self, store, private_pem, subject="mailto:pieye@localhost"):
        self.store = store
        self.private_pem = private_pem
        self.subject = subject

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
        for sub in self.store.list_push_subs():
            try:
                webpush(
                    subscription_info=json.loads(sub["sub_json"]),
                    data=payload,
                    vapid_private_key=self.private_pem,
                    vapid_claims={"sub": self.subject},
                    timeout=10,
                )
                sent += 1
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    self.store.delete_push_sub(sub["endpoint"])
                    print(f"[push] pruned expired subscription ({status})", flush=True)
                else:
                    print(f"[push] send failed: {e}", flush=True)
            except Exception as e:  # never let a push error break an alert
                print(f"[push] send error: {e}", flush=True)
        return sent
