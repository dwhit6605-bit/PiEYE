import argparse
import asyncio
import copy
import os

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import load_config, save_config, validate_config
from .store import EventStore
from .monitor import Monitor

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PUBLIC_API = {"/api/login", "/api/logout", "/api/me"}
REDACTED = "__unchanged__"


def _redact(cfg):
    """Never ship the session secret / password hash / machine token to the browser."""
    out = copy.deepcopy(cfg)
    s = out.get("server", {})
    if s.get("auth_token"):
        s["auth_token"] = REDACTED
    a = s.get("auth", {})
    if a.get("secret"):
        a["secret"] = REDACTED
    if a.get("password_hash"):
        a["password_hash"] = REDACTED
    return out


def _restore_secrets(incoming, current):
    """Put redacted secrets back before saving, so the Settings tab can't wipe them."""
    ci, cc = incoming.get("server", {}), current.get("server", {})
    if ci.get("auth_token") == REDACTED:
        ci["auth_token"] = cc.get("auth_token")
    ai, ac = ci.get("auth", {}) or {}, cc.get("auth", {}) or {}
    for k in ("secret", "password_hash"):
        if ai.get(k) == REDACTED or ai.get(k) is None:
            ai[k] = ac.get(k)
    ci["auth"] = ai
    incoming["server"] = ci
    return incoming


def create_app(config_path):
    cfg = load_config(config_path)

    # ensure a stable session-signing secret exists whenever login is enabled
    if auth.login_enabled(cfg) and not cfg["server"]["auth"].get("secret"):
        cfg["server"]["auth"]["secret"] = auth.generate_secret()
        save_config(config_path, cfg)

    store = EventStore(cfg["storage"]["db_path"], cfg["storage"]["snapshot_dir"])
    monitor = Monitor(cfg, store)
    a = cfg["server"]["auth"]
    limiter = auth.LoginLimiter(a.get("max_attempts", 8),
                                int(a.get("lockout_minutes", 15)) * 60)

    app = FastAPI(title="PiEYE")
    app.state.config_path = config_path
    app.state.cfg = cfg
    app.state.store = store
    app.state.monitor = monitor

    @app.on_event("startup")
    def _startup():
        monitor.start()

    @app.on_event("shutdown")
    def _shutdown():
        monitor.stop()

    def authorized(request: Request):
        return auth.request_authorized(app.state.cfg, request.cookies,
                                       {k.lower(): v for k, v in request.headers.items()})

    @app.middleware("http")
    async def _gate(request: Request, call_next):
        p = request.url.path
        if p.startswith("/api/") and p not in PUBLIC_API:
            if not authorized(request):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    # ---- auth endpoints --------------------------------------------------
    @app.get("/api/me")
    def me(request: Request):
        return {"auth_required": auth.login_enabled(app.state.cfg),
                "authenticated": authorized(request),
                "username": app.state.cfg["server"]["auth"].get("username")}

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        username = body.get("username") or ""
        ip = request.client.host if request.client else "unknown"
        key = f"{ip}:{username}"

        wait = limiter.locked_for(key)
        if wait:
            raise HTTPException(status_code=429,
                                detail=f"Too many attempts. Try again in {wait}s.")
        if not auth.check_credentials(app.state.cfg, username, body.get("password")):
            limiter.record_failure(key)
            raise HTTPException(status_code=401, detail="Invalid username or password")

        limiter.reset(key)
        a = app.state.cfg["server"]["auth"]
        token = auth.make_session(a["username"], a["secret"], a.get("session_ttl_hours", 720))
        resp = JSONResponse({"ok": True, "username": a["username"]})
        resp.set_cookie("pv_session", token, httponly=True, samesite="lax",
                        secure=bool(app.state.cfg["server"].get("secure_cookies")),
                        max_age=int(a.get("session_ttl_hours", 720) * 3600), path="/")
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("pv_session", path="/")
        return resp

    # ---- config ----------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"ok": True, "status": monitor.status}

    @app.get("/api/config")
    def get_config():
        return _redact(app.state.cfg)

    @app.put("/api/config")
    async def put_config(request: Request):
        new_cfg = _restore_secrets(await request.json(), app.state.cfg)
        try:
            validate_config(new_cfg)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        save_config(app.state.config_path, new_cfg)
        app.state.cfg = load_config(app.state.config_path)
        monitor.request_reload(app.state.cfg)
        return {"ok": True, "reloaded": True}

    # ---- events ----------------------------------------------------------
    @app.get("/api/events")
    def events(limit: int = 50, offset: int = 0, camera: str = None, label: str = None):
        return {"events": store.list_events(limit, offset, camera, label),
                "stats": store.stats()}

    @app.get("/api/events/{event_id}")
    def event(event_id: int):
        row = store.get_event(event_id)
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return row

    @app.delete("/api/events/{event_id}")
    def delete_event(event_id: int):
        if not store.delete_event(event_id):
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @app.get("/api/snapshots/{filename}")
    def snapshot(filename: str):
        path = store.snapshot_path(filename)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type="image/jpeg")

    # ---- live: single frame + MJPEG stream ------------------------------
    @app.get("/api/cameras/{cam_id}/live.jpg")
    def live(cam_id: str):
        jpeg = monitor.latest_jpeg.get(cam_id)
        if not jpeg:
            raise HTTPException(status_code=404, detail="no frame yet")
        return Response(content=jpeg, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{cam_id}/stream.mjpg")
    def stream(cam_id: str, request: Request):
        if cam_id not in monitor.status.get("cameras", []) and cam_id not in monitor.latest_jpeg:
            raise HTTPException(status_code=404, detail="unknown camera")
        interval = 1.0 / max(1, app.state.cfg["server"].get("live_fps", 10))

        async def gen():
            monitor.add_viewer()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    jpeg = monitor.latest_jpeg.get(cam_id)
                    if jpeg:
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                               b"Content-Length: " + str(len(jpeg)).encode() +
                               b"\r\n\r\n" + jpeg + b"\r\n")
                    await asyncio.sleep(interval)
            finally:
                monitor.remove_viewer()

        return StreamingResponse(
            gen(), media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store", "Connection": "close"})

    # ---- PWA static (mounted last so /api/* wins) ------------------------
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def main():
    ap = argparse.ArgumentParser(description="Pi security-vision web server + PWA")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    import uvicorn
    cfg = load_config(args.config)
    app = create_app(args.config)
    s = cfg["server"]
    # TLS is terminated by a reverse proxy (see deploy/ + docs/tls.md); uvicorn
    # stays plain HTTP and just trusts the proxy's X-Forwarded-* headers.
    uvicorn.run(app, host=s["host"], port=s["port"], log_level="info",
                proxy_headers=bool(s.get("behind_proxy")),
                forwarded_allow_ips=s.get("trusted_proxies", "127.0.0.1"))


if __name__ == "__main__":
    main()
