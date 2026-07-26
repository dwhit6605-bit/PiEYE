#!/usr/bin/env bash
#
# PiEYE one-touch installer.
#
#   Fresh Pi, one line:
#     curl -fsSL https://raw.githubusercontent.com/dwhit6605-bit/PiEYE/main/install.sh | bash
#
#   Or from a clone:
#     git clone https://github.com/dwhit6605-bit/PiEYE.git && cd PiEYE && ./install.sh
#
# Idempotent: safe to re-run to update. Options via env vars:
#   PIEYE_DIR=~/PiEYE     install location (default: ~/PiEYE)
#   PIEYE_LITE=1          skip YOLO/torch (motion-only; light for 2 GB Pis)
#   PIEYE_NO_SERVICE=1    install deps only, don't touch systemd
#
set -euo pipefail

REPO_URL="https://github.com/dwhit6605-bit/PiEYE.git"
INSTALL_DIR="${PIEYE_DIR:-$HOME/PiEYE}"
SERVICE="pieye"
say() { printf "\n\033[1;36m>> %s\033[0m\n" "$1"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$1"; }

# --- 0. locate or fetch the source -------------------------------------
# If this script sits next to the app (a checkout), install from here.
SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  maybe="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -d "$maybe/vision" ] && [ -d "$maybe/web" ] && SRC="$maybe"
fi

if [ -z "$SRC" ]; then
  say "Fetching PiEYE into $INSTALL_DIR"
  if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only
  else
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
  SRC="$INSTALL_DIR"
else
  INSTALL_DIR="$SRC"
  say "Installing from checkout at $INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# --- 1. system packages -------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
  say "Installing system packages (sudo)"
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip libatlas-base-dev libopenjp2-7 ffmpeg v4l-utils
else
  warn "apt-get not found -- skipping system packages (install python3-venv, ffmpeg, v4l-utils yourself)."
fi

# --- 2. python environment ---------------------------------------------
say "Creating virtualenv + installing Python deps"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip wheel
if [ "${PIEYE_LITE:-0}" = "1" ]; then
  warn "LITE mode: installing without ultralytics/torch (motion-only detection)."
  grep -viE '^\s*ultralytics' requirements.txt > /tmp/pieye-req.txt
  pip install -r /tmp/pieye-req.txt
else
  pip install -r requirements.txt
fi

# --- 3. config ----------------------------------------------------------
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  if [ "${PIEYE_LITE:-0}" = "1" ]; then
    sed -i 's/^\(\s*backend:\s*\)yolo/\1none/' config.yaml || true
  fi
  say "Created config.yaml -- set a unique notify.ntfy_topic before first alert."
else
  say "Keeping existing config.yaml"
fi

# --- 4. detected cameras (informational) -------------------------------
if command -v v4l2-ctl >/dev/null 2>&1; then
  say "Cameras detected on this Pi:"
  v4l2-ctl --list-devices 2>/dev/null || echo "  (none found -- plug in a UVC camera)"
fi

# --- 5. systemd service -------------------------------------------------
if [ "${PIEYE_NO_SERVICE:-0}" = "1" ] || ! command -v systemctl >/dev/null 2>&1; then
  warn "Skipping systemd setup. Run manually:  python -m vision.server --config config.yaml"
else
  say "Installing systemd service '$SERVICE' for user '$USER'"
  UNIT="/etc/systemd/system/${SERVICE}.service"
  sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$INSTALL_DIR|g" \
      systemd/pieye.service | sudo tee "$UNIT" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${SERVICE}.service"
  sleep 1
  sudo systemctl --no-pager --lines=0 status "${SERVICE}.service" || true
fi

# --- 6. done ------------------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PORT="$(grep -E '^\s*port:' config.yaml | head -1 | grep -oE '[0-9]+' || echo 8080)"
cat <<DONE

============================================================
  PiEYE is installed.

  Open the web app:   http://${IP:-<pi-ip>}:${PORT}
  (On your phone: 'Add to Home Screen' to install the PWA.)

  Enable a login:     cd "$INSTALL_DIR" && source .venv/bin/activate
                      python -m vision.set_password
                      sudo systemctl restart ${SERVICE}.service

  Logs:               journalctl -u ${SERVICE}.service -f
  Update later:       cd "$INSTALL_DIR" && ./install.sh
============================================================
DONE
