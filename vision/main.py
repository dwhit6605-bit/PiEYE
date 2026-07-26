import argparse

from .config import load_config
from .store import EventStore
from .monitor import Monitor


def run(config_path):
    """Headless runner (no web UI). For the PWA + web server, use vision.server."""
    cfg = load_config(config_path)
    store = EventStore(cfg["storage"]["db_path"], cfg["storage"]["snapshot_dir"])
    monitor = Monitor(cfg, store)
    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\n[exit] stopped by user", flush=True)
        monitor.stop()


def main():
    ap = argparse.ArgumentParser(description="Homelab Pi security-vision (headless)")
    ap.add_argument("--config", default="config.yaml")
    run(ap.parse_args().config)


if __name__ == "__main__":
    main()
