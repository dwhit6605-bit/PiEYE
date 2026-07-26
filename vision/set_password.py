"""Enable login and set the web-UI username/password.

    python -m vision.set_password --config config.yaml
"""
import argparse
import getpass

from .config import load_config, save_config
from . import auth


def main():
    ap = argparse.ArgumentParser(description="Set the PiEYE web login")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--username", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    a = cfg["server"]["auth"]

    username = args.username or input(f"Username [{a.get('username', 'admin')}]: ").strip() \
        or a.get("username", "admin")
    pw = getpass.getpass("Password: ")
    if not pw:
        raise SystemExit("password cannot be empty")
    if pw != getpass.getpass("Confirm password: "):
        raise SystemExit("passwords do not match")

    a["enabled"] = True
    a["username"] = username
    a["password_hash"] = auth.hash_password(pw)
    if not a.get("secret"):
        a["secret"] = auth.generate_secret()
    save_config(args.config, cfg)
    print(f"Login enabled for '{username}'. Restart the service to apply.")


if __name__ == "__main__":
    main()
