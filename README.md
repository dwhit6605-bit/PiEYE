# PiEYE

![PiEYE](docs/hero.svg)

[![CI](https://github.com/dwhit6605-bit/PiEYE/actions/workflows/ci.yml/badge.svg)](https://github.com/dwhit6605-bit/PiEYE/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-4c8dff.svg)](LICENSE)

A privacy-first security-camera monitor for a Raspberry Pi 4 + UVC (USB) webcams.
Runs **100% offline** by default; the cloud Claude description tier is optional.

> The banner above is a styled mockup of the real UI — drop in an actual phone
> screenshot or a screen-recording GIF under `docs/` and swap the image link anytime.

## Quick start (one line on the Pi)

```bash
curl -fsSL https://raw.githubusercontent.com/dwhit6605-bit/PiEYE/main/install.sh | bash
```

That clones PiEYE to `~/PiEYE`, installs everything, generates a `systemd` service
for your user, and starts it. When it finishes it prints the URL to open. Options:

```bash
PIEYE_LITE=1 curl -fsSL .../install.sh | bash     # motion-only, skip torch (2 GB Pis)
```

Re-run `~/PiEYE/install.sh` any time to update (it `git pull`s and reinstalls).

## Pipeline

```
UVC cam ─► motion detection (OpenCV, free) ─► local YOLO detection (offline) ─► ntfy push + snapshot
                                                                            ├─ [optional] Claude one-line description
                                                                            └─ SQLite event history + web UI (PWA)
```

Motion detection is essentially free, so it inspects every frame and only wakes the
heavier object detector when something moves. YOLO then confirms *what* it is
(person / car / dog / …) before anything pings your phone — so you get "person at
front-door" instead of "a shadow moved."

## Web app (PWA)

`vision.server` runs the monitor in the background **and** serves a phone-installable
web app at `http://<pi-ip>:8080`:

- **Live** — smooth **MJPEG** stream from each camera (the monitor only ramps to full
  frame rate while a Live view is actually open, so it stays light on the Pi)
- **Events** — searchable history with the saved annotated snapshot, description,
  labels and confidence; tap for full image; delete individual events
- **Settings** — edit the *entire* config from the browser (cameras, motion
  sensitivity, detection backend/classes, ntfy, Claude toggle, retention). Saving
  writes `config.yaml` and hot-reloads the monitor — no SSH, no restart

Open it in mobile Chrome/Safari and "Add to Home Screen" to install it as a standalone
app. Events persist to SQLite (`data/events.db`) with snapshots under `data/snapshots/`,
auto-pruned by `storage.retention_days` / `storage.max_events`.

### Login

The UI is open by default (fine on a trusted LAN / over WireGuard). To require a
username + password, run this once on the Pi and restart the service:

```bash
cd ~/PiEYE && source .venv/bin/activate
python -m vision.set_password           # prompts for username + password
sudo systemctl restart pieye.service
```

This stores a salted **PBKDF2** password hash and an auto-generated session-signing
secret in `config.yaml` (never plaintext). Logging in sets an HttpOnly session cookie;
"Sign out" is on the Settings tab. Failed logins are **rate-limited**
(`server.auth.max_attempts` / `lockout_minutes`) to blunt brute-forcing. For
scripts/automation you can instead set `server.auth_token` and send it as an
`X-Auth-Token` header.

### HTTPS + a custom domain

To serve `https://pieye.yourdomain.com` with a Let's Encrypt cert, put a reverse proxy
in front (PiEYE stays plain HTTP behind it) and set `server.secure_cookies: true` +
`server.behind_proxy: true`. Ready-made configs are in [`deploy/`](deploy/) (Caddy,
nginx, Cloudflare Tunnel) and a full walkthrough is in **[docs/tls.md](docs/tls.md)**.

⚠️ A camera on the public internet is a real target. Prefer a **private** setup —
WireGuard + a DNS-01 cert, or a Cloudflare Tunnel with Access in front — over
port-forwarding. Both give you the HTTPS hostname with **zero open ports**.

## Hardware

- Raspberry Pi 4 (4 GB+ recommended if using YOLO; `backend: none` runs on anything)
- One or more UVC webcams on USB
- Phone with the [ntfy](https://ntfy.sh) app (free) subscribed to your topic

## After install

The one-line installer already set up and started the `pieye.service`. To finish:

1. Open `http://<pi-ip>:8080`, and subscribe your phone's ntfy app to the
   `notify.ntfy_topic` value (set your own unique topic in **Settings**).
2. Walk in front of the camera — you should get a push with an annotated snapshot and
   see the event in the **Events** tab.

```bash
journalctl -u pieye.service -f          # watch logs
sudo systemctl restart pieye.service    # after editing config.yaml by hand
```

Manual / headless run (no service): `python -m vision.server --config config.yaml`,
or without the web UI, `python -m vision.main --config config.yaml`.

## Adding more cameras

Add entries under `cameras:` (or use **Settings → Cameras → + Add camera**). Each needs a
unique `id`; the `id` shows up in alert titles and as the Live/Events label.

```yaml
cameras:
  - id: front-door          # USB webcam
    source: 0
    fourcc: MJPG
    width: 1280
    height: 720
  - id: driveway            # second USB webcam -- find its index first
    source: 2
  - id: garage              # IP camera / NVR substream
    source: "rtsp://user:pass@192.168.86.70:554/stream2"
  - id: old-phone           # phone running an IP Webcam app
    source: "http://192.168.86.90:8080/video"
```

Find USB indices with `v4l2-ctl --list-devices` — use the **first** `/dev/videoN` listed
under the camera's name (a UVC cam claims two nodes; only the first delivers frames).

**A failed camera no longer stops the others.** Cameras that can't be opened are skipped,
listed on the Live tab as `OFFLINE` with the reason, and retried every
`detection.camera_retry_seconds` (default 60). A camera that stops delivering frames
mid-run (unplugged) triggers an automatic rebuild so it recovers when reconnected.

### Practical limits on a Pi 4

| Concern | Guidance |
|---|---|
| USB bandwidth | Use the **blue USB3** ports and `fourcc: MJPG`. Two 1080p raw (YUYV) cams will starve one controller |
| CPU | Cameras are polled in one loop; YOLO runs only on motion. 2–3 cams is comfortable, 4+ wants `640x480` or a longer `cooldown_seconds` |
| Power | Several USB cams + a Pi need a solid 3 A supply, or use a **powered** USB hub |
| RTSP | Prefer the camera's **substream** (lower res) — decoding a 4 K main stream will peg the CPU |

## Tuning

| Symptom | Fix in `config.yaml` |
|---|---|
| Too many alerts | raise `motion.min_area`, raise `min_confidence_to_alert`, raise `cooldown_seconds` |
| Missing events | lower `motion.threshold`, lower `motion.min_area` |
| False object types | trim `classes_of_interest` |
| No ML at all (lightest) | `detection.backend: none` (alerts on motion only, no torch needed) |

## Optional: add Claude descriptions later

No API key needed for everything above. When you want sentence-level descriptions
("a delivery courier is leaving a box by the door") and package-type awareness that
local COCO models lack:

1. Get a key at console.anthropic.com
2. `pip install anthropic`
3. Put the key in an `.env` file next to the service and reference it via
   `EnvironmentFile` in the unit (keep it out of git)
4. Set `claude.enabled: true` in `config.yaml`

Claude is still only called on confirmed detections, so spend stays tiny.

## Remote viewing (GL.iNet)

Alerts reach you anywhere already (ntfy goes over normal internet). To *view* the Pi
remotely, enable the built-in **WireGuard server** in the GL-AR750's admin GUI, put
the Pi on its LAN, and connect your phone's WireGuard client from away. The AR300M is
a fine spare / travel unit for the same.
