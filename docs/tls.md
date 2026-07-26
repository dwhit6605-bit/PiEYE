# HTTPS + a custom domain for PiEYE

PiEYE serves plain HTTP; a **reverse proxy** in front terminates TLS and gets the
Let's Encrypt cert. This gives you `https://pieye.yourdomain.com`, a fully
installable PWA, and lets you turn on `Secure` session cookies.

> **Security first:** a camera on the open internet is a real target. Prefer the
> private options (1 or 2) below. Only do public port-forwarding (option 3) with the
> hardening in place, and never with a weak password.

Whichever option you pick, set these in `config.yaml` and restart the service, because
the browser will now be talking HTTPS:

```yaml
server:
  secure_cookies: true    # cookie only sent over HTTPS
  behind_proxy: true      # trust the local proxy's X-Forwarded-* headers
  trusted_proxies: 127.0.0.1
```

PiEYE also throttles failed logins (`server.auth.max_attempts` / `lockout_minutes`) —
keep that on for any remote access.

---

## Option 1 — WireGuard + internal cert (most private)

Nothing is exposed; the hostname resolves to a **private** LAN IP reachable only over
the tunnel you already run on the GL-AR750.

1. **DNS:** create an A record `pieye.yourdomain.com → 192.168.8.50` (the Pi's LAN IP).
   Public DNS is fine — it just points at a private address.
2. **Cert (DNS-01):** issue a cert without opening ports, using your DNS provider's API.
   With Caddy + the Cloudflare DNS module, use [`deploy/Caddyfile.dns01`](../deploy/Caddyfile.dns01);
   with certbot: `sudo certbot certonly --dns-cloudflare -d pieye.yourdomain.com` then
   point [`deploy/nginx-pieye.conf`](../deploy/nginx-pieye.conf) at the cert.
3. **Access:** connect the WireGuard client (GL.iNet app / config) and open
   `https://pieye.yourdomain.com`.

> If your router has *DNS-rebinding protection*, it may strip the private-IP answer.
> Add an exception for the domain, or run local DNS (Pi-hole/AdGuard) that returns it.

## Option 2 — Cloudflare Tunnel + Access (browser from anywhere, no open ports)

No port-forwarding, TLS at Cloudflare's edge, and an optional SSO gate in front of
PiEYE's own login. Use [`deploy/cloudflared-config.yml`](../deploy/cloudflared-config.yml).

1. `cloudflared tunnel login && cloudflared tunnel create pieye`
2. `cloudflared tunnel route dns pieye pieye.yourdomain.com`
3. Fill in the tunnel ID in the config, then `sudo cloudflared service install`.
4. In the Cloudflare **Zero Trust** dashboard, add an **Access** policy on the hostname
   (allow only your email) — now nobody reaches PiEYE without passing Cloudflare first.

## Option 3 — Public port-forward + Caddy auto-HTTPS (simplest, most exposed)

Only with hardening: a strong password, login throttling on (default), and updates
applied. Forward router ports **80 and 443 → the Pi**, then use
[`deploy/Caddyfile`](../deploy/Caddyfile):

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the hostname
sudo systemctl restart caddy
```
Caddy fetches and auto-renews the cert on first request. Consider putting it behind
Cloudflare (proxied DNS) to hide your home IP, or restrict source IPs at the router.

---

## Verify

```bash
curl -I https://pieye.yourdomain.com            # 200, valid TLS
# MJPEG must stream, not buffer — this should keep printing boundaries:
curl -N https://pieye.yourdomain.com/api/cameras/<cam-id>/stream.mjpg | head -c 300
```

If the Live view stalls behind the proxy, the stream is being buffered — confirm Caddy
has `flush_interval -1` or nginx has `proxy_buffering off` on the `stream.mjpg` route.
