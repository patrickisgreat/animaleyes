"""Single-page dashboard. No build step; the page polls JSON every 3 seconds and shows the
camera as an MJPEG stream.

Every route except /healthz requires HTTP basic auth (DASH_USER / DASH_PASSWORD). It is
served on loopback only and reached through Cloudflare Tunnel or Tailscale Serve, both of
which terminate TLS, so the password never crosses a network in the clear. Controls write
config.toml or set flags the state machine consumes on its next tick; the dashboard never
talks to the feeder itself.
"""

from __future__ import annotations

import asyncio
import json
import secrets as secrets_lib
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .camera import FrameBuffer
from .config import ConfigStore, Secrets
from .machine import Machine
from .store import Store

# Module level so FastAPI can resolve the annotation (this file uses postponed annotations).
Credentials = Annotated[
    HTTPBasicCredentials | None, Depends(HTTPBasic(auto_error=False, realm="animaleyes"))
]

STREAM_POLL_S = 0.1
# The page reconnects when a stream ends, so a tab left open on a phone cannot hold a
# connection through the tunnel forever.
STREAM_MAX_S = 600

MAIN_EVENT_KINDS = (
    "open",
    "close",
    "veto",
    "feed_failed",
    "close_failed",
    "wanted_food_none_left",
    "startup",
    "bowie_during_feed",
    "cat_during_feed",
    "camera_offline",
    "camera_online",
    "plates_set",
    "grrr_blocked",
    "manual_open",
    "manual_close",
    "manual_rotate",
    "manual_open_failed",
    "manual_close_failed",
    "manual_rotate_failed",
)


def create_app(
    machine: Machine,
    store: Store,
    config: ConfigStore,
    secrets: Secrets,
    frames_dir: Path,
    reload_references=None,
    reference_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="animaleyes", docs_url=None, redoc_url=None)
    ANIMALS = ("grrr", "bowie", "cat")

    def ref_counts() -> dict[str, int]:
        # Count the photos actually on disk, independent of the active detector (YOLO doesn't
        # track reference images, so reading them off the identifier wrongly showed 0).
        counts: dict[str, int] = {}
        for a in ANIMALS:
            d = (reference_dir / a) if reference_dir else None
            counts[a] = (
                sum(1 for p in d.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
                if d and d.is_dir()
                else 0
            )
        return counts

    def _basic_ok(credentials: Credentials) -> bool:
        return (
            bool(secrets.dash_password)
            and credentials is not None
            and secrets_lib.compare_digest(
                credentials.username.encode(), secrets.dash_user.encode()
            )
            and secrets_lib.compare_digest(
                credentials.password.encode(), secrets.dash_password.encode()
            )
        )

    def auth(request: Request, credentials: Credentials) -> None:
        mode = machine.settings.DASH_AUTH
        if mode == "none":
            return
        # Tailscale trust: `tailscale serve` injects an identity header for the connecting
        # tailnet device, so being on the tailnet IS the authentication — no password. We only
        # trust it when the request did NOT arrive via the public Cloudflare tunnel (which adds
        # cf-ray), so the header can't be forged from the internet.
        ts_user = request.headers.get("tailscale-user-login")
        via_cloudflare = "cf-ray" in request.headers
        if mode == "tailscale" and ts_user and not via_cloudflare:
            return
        # Basic-auth fallback (public/Cloudflare path, or mode="basic").
        if _basic_ok(credentials):
            return
        raise HTTPException(
            status_code=401,
            detail="login required",
            headers={"WWW-Authenticate": 'Basic realm="animaleyes"'},
        )

    guarded = [Depends(auth)]

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """Liveness for the container healthcheck (and autoheal). Fails if the state-machine
        loop has stopped ticking, so a hung-but-running process gets restarted. Unauthenticated
        and reveals nothing sensitive."""
        hb = store.get("heartbeat_at")
        if hb:
            age = (machine.clock() - datetime.fromisoformat(hb)).total_seconds()
            limit = max(180, 3 * machine.settings.HEARTBEAT_MIN * 60)
            if age > limit:
                raise HTTPException(status_code=503, detail=f"loop stale {int(age)}s")
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse, dependencies=guarded)
    def index() -> str:
        return PAGE

    @app.get("/api/status", dependencies=guarded)
    def status() -> dict[str, Any]:
        now = machine.clock()
        settings = machine.settings
        heartbeat = store.get("heartbeat_at")
        last_frame = machine.frames.last_frame_at()
        calls, cost = store.llm_totals_since(now.replace(hour=0, minute=0, second=0, microsecond=0))
        next_feed = machine.next_allowed_feed()
        verdict = machine.last_verdict.to_dict() if machine.last_verdict else None
        return {
            "now": now.isoformat(timespec="seconds"),
            "state": machine.state,
            "enabled": settings.ENABLED,
            "dry_run": settings.DRY_RUN,
            "active_window": f"{settings.ACTIVE_START}-{settings.ACTIVE_END}",
            "in_window": settings.is_active_at(now.time()),
            "plates": store.plates(),
            "loaded_plates": store.loaded_plates(),
            "feeds_this_window": store.get_int("feeds_this_window"),
            "last_open_at": store.get("last_open_at"),
            "motion_source": settings.MOTION_SOURCE,
            "camera_motion": getattr(machine.events, "motion_state", None),
            "camera_animal": getattr(machine.events, "animal_state", None),
            "camera_events_ok": (machine.events.last_error is None)
            if machine.events is not None
            else None,
            # Prefer the feeder's real lid state (catches opens done from the PetLibro app);
            # fall back to what the machine knows from its own actions.
            "lid_open": (store.get("lid_actual_open") == "1")
            if store.get("lid_actual_open") is not None
            else (
                machine.state in ("OPENING", "FEEDING", "CLOSING")
                or bool(store.get("lid_manual_open"))
            ),
            "current_plate": store.get_int("current_plate", 0) or None,
            "feeding_plate": (store.get_int("feeding_plate", 0) or None)
            if machine.state == "FEEDING"
            else None,
            "next_allowed_feed": next_feed.isoformat(timespec="seconds") if next_feed else None,
            "next_allowed_feed_in_s": max(0, int((next_feed - now).total_seconds()))
            if next_feed
            else 0,
            "heartbeat_age_s": _age(heartbeat, now),
            "camera_age_s": int((now - last_frame).total_seconds()) if last_frame else None,
            "camera_offline": machine.camera_offline,
            "motion_fraction": round(machine.motion.last_fraction, 4),
            "llm_calls_today": calls,
            "llm_cost_today_usd": round(cost, 4),
            "llm_model": settings.LLM_MODEL,
            "identifier": settings.IDENTIFIER,
            "last_verdict": verdict,
            "last_llm_at": machine.last_llm_at.isoformat(timespec="seconds")
            if machine.last_llm_at
            else None,
            "reference_counts": ref_counts(),
            "feeding_since": store.get("opened_at") if machine.state == "FEEDING" else None,
        }

    @app.get("/api/config", dependencies=guarded)
    def get_config() -> dict[str, Any]:
        return config.load().__dict__

    @app.post("/api/config", dependencies=guarded)
    async def set_config(request: Request) -> dict[str, Any]:
        changes = await request.json()
        try:
            settings = config.update(changes)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.add_event(machine.clock(), "config", "dashboard", changes)
        return settings.__dict__

    @app.post("/api/plates", dependencies=guarded)
    async def set_plates(request: Request) -> dict[str, Any]:
        body = await request.json()
        loaded = {int(p) for p in body.get("loaded", [])}
        now = machine.clock()
        for plate in (1, 2, 3):
            store.set_plate(plate, "loaded" if plate in loaded else "empty", now)
        store.set("plates_updated", 1)
        return {"plates": store.plates()}

    @app.post("/api/feed-now", dependencies=guarded)
    def feed_now() -> dict[str, str]:
        machine.request_feed_now()
        return {"ok": "feed requested; the state machine opens on its next tick"}

    @app.post("/api/feeder/{action}", dependencies=guarded)
    def feeder_action(action: str) -> dict[str, str]:
        # The page only enqueues the request; the state machine is still the sole caller of
        # the feeder and performs it on its next tick (product invariant).
        try:
            machine.request_manual_action(action)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": f"{action} requested; the state machine runs it on its next tick"}

    def _animal_dir(animal: str) -> Path:
        if animal not in ANIMALS or reference_dir is None:
            raise HTTPException(status_code=400, detail="unknown animal")
        d = reference_dir / animal
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_reference(animal: str, name: str, jpeg: bytes) -> dict[str, Any]:
        safe = Path(name).name or "frame.jpg"
        if not safe.lower().endswith((".jpg", ".jpeg", ".png")):
            safe += ".jpg"
        (_animal_dir(animal) / safe).write_bytes(jpeg)
        if reload_references:
            reload_references()
        return {"saved": safe, "reference_counts": ref_counts()}

    @app.post("/api/reference/{animal}", dependencies=guarded)
    async def upload_reference(animal: str, request: Request) -> dict[str, Any]:
        # Raw image bytes in the body (no multipart dependency); filename in the query.
        name = request.query_params.get("filename", "upload.jpg")
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="empty upload")
        return _save_reference(animal, name, body)

    @app.post("/api/reference/{animal}/capture", dependencies=guarded)
    def capture_reference(animal: str) -> dict[str, Any]:
        frames = machine.frames.latest(1)
        if not frames:
            raise HTTPException(status_code=404, detail="no camera frame yet")
        name = f"cam-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
        return _save_reference(animal, name, frames[0].jpeg)

    @app.post("/api/reload-references", dependencies=guarded)
    def reload_refs() -> dict[str, Any]:
        if reload_references:
            reload_references()
        return {"reference_counts": ref_counts()}

    @app.get("/frame.jpg", dependencies=guarded)
    def frame() -> Response:
        frames = machine.frames.latest(1)
        if not frames:
            raise HTTPException(status_code=404, detail="no frame yet")
        return Response(
            content=frames[0].jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.get("/stream.mjpg", dependencies=guarded)
    def stream() -> StreamingResponse:
        # Read-only view of the frames the camera thread already decodes; it opens no new
        # connection to the camera. POC: runs at the ingest rate (~2 fps), which motion
        # detection is tuned for.
        return StreamingResponse(
            mjpeg(machine.frames),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/events", dependencies=guarded)
    def events(all: bool = False, limit: int = 100) -> JSONResponse:
        rows = store.events(limit=limit, kinds=None if all else MAIN_EVENT_KINDS)
        return JSONResponse([_event_json(e) for e in rows])

    @app.get("/events/{event_id}", response_class=HTMLResponse, dependencies=guarded)
    def event_page(event_id: int) -> str:
        event = store.event(event_id)
        if not event:
            raise HTTPException(status_code=404)
        images = "".join(f'<img src="/frames/{name}" alt="{name}">' for name in event.frames)
        return EVENT_PAGE.format(
            id=event.id,
            at=event.at.strftime("%Y-%m-%d %H:%M:%S"),
            kind=event.kind,
            reason=_escape(event.reason),
            data=_escape(json.dumps(event.data, indent=2)),
            images=images,
        )

    @app.get("/frames/{name}", dependencies=guarded)
    def frame_file(name: str) -> FileResponse:
        path = (frames_dir / Path(name).name).resolve()
        if not path.is_file() or frames_dir.resolve() not in path.parents:
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="image/jpeg")

    return app


MJPEG_BOUNDARY = "frame"


async def mjpeg(
    frames: FrameBuffer, poll_s: float = STREAM_POLL_S, max_s: float = STREAM_MAX_S
) -> AsyncIterator[bytes]:
    """Yield each new frame in the buffer as one multipart/x-mixed-replace part."""
    deadline = time.monotonic() + max_s
    last_at: datetime | None = None
    while time.monotonic() < deadline:
        latest = frames.latest(1)
        if latest and latest[0].at != last_at:
            last_at = latest[0].at
            jpeg = latest[0].jpeg
            yield (
                f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                + jpeg
                + b"\r\n"
            )
        await asyncio.sleep(poll_s)


def _age(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    return int((now - datetime.fromisoformat(iso)).total_seconds())


def _event_json(e) -> dict[str, Any]:
    return {
        "id": e.id,
        "at": e.at.isoformat(timespec="seconds"),
        "kind": e.kind,
        "reason": e.reason,
        "data": e.data,
        "frames": e.frames,
    }


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def seconds_to_text(seconds: int) -> str:
    return str(timedelta(seconds=seconds))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>animaleyes</title>
<style>
 :root{
   --bg:#0e0f13;--surface:#171920;--surface2:#1f222c;--border:#2a2e3a;
   --fg:#e7e9ef;--muted:#949aa7;--accent:#6aa9ff;--accent-ink:#06142b;
   --ok:#3ad68a;--warn:#ffb454;--bad:#ff5c66;--radius:14px;
 }
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
   font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
 .wrap{max-width:1080px;margin:0 auto;padding:16px}
 header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
 header h1{font-size:19px;font-weight:800;margin:0;letter-spacing:.2px}
 header h1 .paw{opacity:.9}
 .badge{font-size:12px;font-weight:700;padding:4px 9px;border-radius:999px;border:1px solid var(--border)}
 .badge.live{background:rgba(58,214,138,.12);color:var(--ok);border-color:rgba(58,214,138,.3)}
 .badge.dry{background:rgba(255,180,84,.12);color:var(--warn);border-color:rgba(255,180,84,.3)}
 .badge.off{background:rgba(255,92,102,.12);color:var(--bad);border-color:rgba(255,92,102,.3)}
 .spacer{flex:1}
 .cols{display:grid;gap:16px;grid-template-columns:1fr}
 @media(min-width:880px){.cols{grid-template-columns:1.25fr 1fr;align-items:start}}
 .col{display:grid;gap:16px;min-width:0}
 .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px}
 .card h2{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;
   color:var(--muted);margin:0 0 12px}
 /* state banner */
 .state{display:flex;align-items:center;gap:12px}
 .dot{width:12px;height:12px;border-radius:50%;flex:none;background:var(--muted);box-shadow:0 0 0 4px rgba(255,255,255,.04)}
 .dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}.dot.accent{background:var(--accent)}
 .state .big{font-size:22px;font-weight:800;line-height:1.1}
 .state .sub{color:var(--muted);font-size:13px;margin-top:2px}
 /* live */
 .live-wrap{position:relative;border-radius:12px;overflow:hidden;background:#000;aspect-ratio:16/9}
 .live-wrap img{width:100%;height:100%;object-fit:cover;display:block}
 .live-chip{position:absolute;top:10px;left:10px;display:flex;gap:6px;align-items:center;
   background:rgba(8,10,14,.7);backdrop-filter:blur(4px);padding:5px 10px;border-radius:999px;font-size:12px;font-weight:700}
 .live-verdict{position:absolute;left:10px;right:10px;bottom:10px;background:rgba(8,10,14,.72);
   backdrop-filter:blur(4px);padding:8px 11px;border-radius:10px;font-size:13px}
 .live-verdict .who{font-weight:700}.live-verdict .why{color:var(--muted);font-size:12px;margin-top:2px}
 .live-btn{position:absolute;top:10px;right:10px}
 /* stat tiles */
 .tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
 .tile{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
 .tile .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
 .tile .val{font-size:17px;font-weight:700;margin-top:3px;word-break:break-word}
 /* feeder */
 .lidrow{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
 .pill{display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border-radius:999px;
   font-weight:700;font-size:13px;border:1px solid var(--border);background:var(--surface2)}
 .pill.open{color:var(--ok);border-color:rgba(58,214,138,.35)}
 .pill.closed{color:var(--muted)}
 .btnrow{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
 button{font:inherit;font-weight:700;border:1px solid var(--border);background:var(--surface2);color:var(--fg);
   border-radius:10px;padding:10px 14px;min-height:42px;cursor:pointer;transition:.12s}
 button:hover{border-color:#3a4150}button:active{transform:translateY(1px)}
 button.primary{background:var(--accent);color:var(--accent-ink);border-color:transparent}
 button.danger{background:rgba(255,92,102,.14);color:var(--bad);border-color:rgba(255,92,102,.35)}
 button.sm{padding:7px 11px;min-height:36px;font-size:13px}
 .chips{display:flex;gap:8px;flex-wrap:wrap}
 .chip{padding:9px 14px;border-radius:10px;border:1px solid var(--border);background:var(--surface2);
   font-weight:700;cursor:pointer;min-height:42px;display:flex;align-items:center;gap:7px}
 .chip.on{background:rgba(58,214,138,.14);color:var(--ok);border-color:rgba(58,214,138,.35)}
 .chip .pn{opacity:.7;font-size:12px}
 .hint{color:var(--muted);font-size:12px;margin-top:10px}
 /* events */
 .evlist{display:flex;flex-direction:column;max-height:460px;overflow-y:auto;
   margin:-4px -4px 0;padding:0 4px}
 .evlist::-webkit-scrollbar{width:8px}.evlist::-webkit-scrollbar-thumb{background:var(--border);border-radius:8px}
 .ev{display:flex;gap:11px;padding:10px 0;border-bottom:1px solid var(--border);align-items:flex-start}
 .ev:last-child{border-bottom:0}
 .ev .ico{width:26px;height:26px;border-radius:7px;flex:none;display:grid;place-items:center;
   background:var(--surface2);font-size:14px}
 .ev .body{min-width:0;flex:1}
 .ev .title{font-weight:700;font-size:14px}
 .ev .meta{color:var(--muted);font-size:12px;margin-top:1px;word-break:break-word}
 .ev a{color:var(--accent);text-decoration:none}
 .toggle{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;font-weight:600;cursor:pointer}
 /* refs + settings */
 .refgrid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
 .ref{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:11px}
 .ref .name{font-weight:700;text-transform:capitalize}
 .ref .cnt{color:var(--muted);font-size:12px;margin:2px 0 8px}
 .ref input[type=file]{font-size:12px;width:100%;color:var(--muted)}
 details{border-top:1px solid var(--border);margin-top:4px}
 details summary{cursor:pointer;padding:12px 0 4px;font-size:12px;font-weight:700;
   text-transform:uppercase;letter-spacing:.08em;color:var(--muted);list-style:none}
 details summary::-webkit-details-marker{display:none}
 details summary::after{content:" ▾";opacity:.6}
 details[open] summary::after{content:" ▴"}
 .setgrp{margin:12px 0 4px;color:var(--accent);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
 .field{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)}
 .field label{font-size:14px}.field .d{color:var(--muted);font-size:12px}
 .field input,.field select{background:var(--bg);color:var(--fg);border:1px solid var(--border);
   border-radius:8px;padding:8px;font-size:16px;min-width:120px;max-width:160px}
 .field input[type=checkbox]{min-width:auto;width:20px;height:20px}
 .toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(20px);
   background:var(--surface2);border:1px solid var(--border);color:var(--fg);padding:11px 16px;
   border-radius:10px;font-size:14px;font-weight:600;opacity:0;transition:.25s;pointer-events:none;z-index:50;max-width:90vw}
 .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
 .toast.err{border-color:rgba(255,92,102,.5);color:var(--bad)}
</style></head><body>
<div class="wrap">
  <header>
    <h1><span class="paw">🐾</span> animaleyes</h1>
    <span class="spacer"></span>
    <span class="badge" id="modeBadge">—</span>
    <span class="badge" id="camBadge">—</span>
  </header>

  <div class="cols">
    <div class="col">
      <div class="card">
        <div class="state"><span class="dot" id="stateDot"></span>
          <div><div class="big" id="stateBig">…</div><div class="sub" id="stateSub"></div></div></div>
      </div>

      <div class="card">
        <div class="live-wrap">
          <img id="live" alt="live camera">
          <span class="live-chip" id="liveChip">● LIVE</span>
          <button class="sm live-btn" id="liveBtn" onclick="toggleLive()">Pause</button>
          <div class="live-verdict" id="verdict"><div class="who">Waiting…</div></div>
        </div>
      </div>

      <div class="card">
        <h2>Feeder</h2>
        <div class="lidrow">
          <span class="pill" id="lidPill">lid —</span>
          <span class="pill" id="plshow">plate —</span>
          <span class="pill" id="feedsPill">0 feeds</span>
        </div>
        <div class="btnrow">
          <button class="primary" onclick="feedNow()">Feed now</button>
          <button onclick="feeder('open')">Open</button>
          <button onclick="feeder('close')">Close</button>
          <button onclick="feeder('rotate')">Rotate</button>
        </div>
        <div class="hint">Buttons ask the feeder controller to act on its next check (a second or two). Nothing moves in dry‑run mode.</div>
      </div>

      <div class="card">
        <h2>Food loaded</h2>
        <div class="chips" id="plates"></div>
        <div class="btnrow"><button class="primary sm" onclick="savePlates()">Save loaded plates</button></div>
        <div class="hint">Mark which bowls you filled. Saving resets the night's count and re‑arms the feeder.</div>
      </div>
    </div>

    <div class="col">
      <div class="card">
        <h2>Status</h2>
        <div class="tiles" id="tiles"></div>
      </div>

      <div class="card">
        <h2 style="display:flex;justify-content:space-between;align-items:center">Activity
          <span class="toggle"><input type="checkbox" id="allEvents" onchange="loadEvents()"> show all</span></h2>
        <div class="evlist" id="events"></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <h2>Reference photos</h2>
    <div class="refgrid" id="refs"></div>
    <div class="btnrow"><button class="sm" onclick="reloadRefs()">Reload photos</button></div>
    <div class="hint">What the detector learns each animal from. At night the camera is infra‑red, so a black coat is invisible — “Grab frame” captures real IR shots to teach it size and shape.</div>
  </div>

  <div class="card" style="margin-top:16px">
    <details>
      <summary>Settings</summary>
      <div id="controls"></div>
      <div class="btnrow"><button class="primary sm" onclick="saveConfig()">Save settings</button></div>
    </details>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const ANIMALS = ["grrr","bowie","cat"];
const NAMES = {grrr:"Grrr",bowie:"Bowie",cat:"the cat",none:"Nothing",unsure:"Unsure"};
const STATES = {
  OUTSIDE_WINDOW:["Off hours","Outside the active feeding window","muted"],
  IDLE:["Watching","Waiting for something to move at the bowl","accent"],
  WATCHING:["Checking","Figuring out who's at the bowl","accent"],
  OPENING:["Opening","Opening the feeder","warn"],
  FEEDING:["Feeding","Lid is open — Grrr is eating","ok"],
  CLOSING:["Closing","Closing the feeder","warn"],
  COOLDOWN:["Cooldown","Waiting before the next feed","muted"],
  DONE:["Done","Finished for this window","muted"],
};
const EV = {
  open:["🍽️","Fed"],close:["✅","Closed"],veto:["🚫","Blocked — wrong animal"],
  feed_failed:["⚠️","Feed failed"],close_failed:["⚠️","Close failed"],
  wanted_food_none_left:["🙁","Wanted food, none left"],startup:["▶️","Started up"],
  bowie_during_feed:["🐕","Bowie showed up during feeding"],cat_during_feed:["🐈","Cat showed up during feeding"],
  camera_offline:["📵","Camera offline"],camera_online:["📶","Camera back online"],
  plates_set:["🥣","Plates updated"],grrr_blocked:["⏳","Grrr seen but not fed yet"],
  manual_open:["🖐️","Opened by hand"],manual_close:["🖐️","Closed by hand"],manual_rotate:["🔄","Rotated by hand"],
  manual_open_failed:["⚠️","Manual open failed"],manual_close_failed:["⚠️","Manual close failed"],
  manual_rotate_failed:["⚠️","Manual rotate failed"],config:["⚙️","Settings changed"],
};
// key, friendly label, type, group. type: bool|number|text|select(options)
const CONTROLS = [
  ["ENABLED","Enabled","bool","Schedule"],
  ["ACTIVE_START","Active from","text","Schedule"],
  ["ACTIVE_END","Active until","text","Schedule"],
  ["DRY_RUN","Dry run (don't move feeder)","bool","Schedule"],
  ["IDENTIFIER","Detector","select:cascade,yolo,claude","Identification"],
  ["GRRR_MIN_CONF","Min confidence for Grrr","number","Identification"],
  ["CONFIRMATIONS_REQUIRED","Confirmations before feeding","number","Identification"],
  ["OPEN_REQUIRES_AT_BOWL","Require head-in-bowl to open","bool","Identification"],
  ["GRRR_MAX_BOX_FRACTION","YOLO: max size that's Grrr","number","Identification"],
  ["YOLO_MIN_CONF","YOLO: min detection confidence","number","Identification"],
  ["LLM_MODEL","Claude model","text","Identification"],
  ["MIN_GAP_MIN","Min minutes between feeds","number","Feeding"],
  ["LEAVE_TIMEOUT_S","Close after gone (sec)","number","Feeding"],
  ["FEEDING_MAX_MIN","Max feeding length (min)","number","Feeding"],
  ["FEEDING_POLL_S","Check interval while feeding (sec)","number","Feeding"],
  ["MOTION_SOURCE","Motion source","select:camera,frames","Motion & sensing"],
  ["MOTION_HOLD_S","Keep watching after motion (sec)","number","Motion & sensing"],
  ["MOTION_PIXEL_FRACTION","Frame-diff sensitivity","number","Motion & sensing"],
  ["LLM_MIN_INTERVAL_S","Min seconds between checks","number","Motion & sensing"],
  ["LID_POLL_S","Lid check interval (sec)","number","Advanced"],
  ["FEED_RETRY_BACKOFF_S","Backoff after a failed feed (sec)","number","Advanced"],
  ["HEARTBEAT_MIN","Heartbeat interval (min)","number","Advanced"],
  ["DASH_AUTH","Dashboard auth","select:tailscale,basic,none","Advanced"],
];
let plates = {};
let live = true;
const fmtAge = (s) => s == null ? "—" : (s < 90 ? s + "s ago" : Math.round(s/60) + "m ago");
const fmtIn = (s) => !s ? "now" : (s < 90 ? "in " + s + "s" : "in " + Math.round(s/60) + "m");

let toastT;
function toast(msg, err) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastT); toastT = setTimeout(() => t.className = "toast", 2600);
}

async function refresh() {
  let s;
  try { s = await (await fetch("/api/status")).json(); }
  catch (e) { document.getElementById("camBadge").textContent = "disconnected"; return; }

  const [lbl, sub, tone] = STATES[s.state] || [s.state, "", "muted"];
  document.getElementById("stateBig").textContent = lbl;
  document.getElementById("stateSub").textContent = sub;
  document.getElementById("stateDot").className = "dot " + tone;

  const mb = document.getElementById("modeBadge");
  mb.textContent = s.dry_run ? "Dry run" : "Live";
  mb.className = "badge " + (s.dry_run ? "dry" : "live");
  const cb = document.getElementById("camBadge");
  cb.textContent = s.camera_offline ? "Camera offline" : "Camera ok";
  cb.className = "badge " + (s.camera_offline ? "off" : "live");

  // live verdict, humanized
  const v = s.last_verdict, vd = document.getElementById("verdict");
  if (!v) { vd.innerHTML = '<div class="who">Nothing yet</div><div class="why">No animal checked since the last feed</div>'; }
  else {
    let who;
    if (v.animal === "none") who = "No animal at the bowl";
    else if (v.animal === "unsure") who = "Not sure who that is";
    else who = `${NAMES[v.animal]||v.animal} · ${Math.round((v.confidence||0)*100)}% · ${v.at_bowl?"at the bowl":"nearby"}`;
    const extra = (v.other_animals_present||[]).length ? " · also "+v.other_animals_present.map(a=>NAMES[a]||a).join(", ") : "";
    vd.innerHTML = `<div class="who">${who}${extra}</div>` + (v.reason ? `<div class="why">${esc(v.reason)}</div>` : "");
  }

  // feeder pills
  const lp = document.getElementById("lidPill");
  lp.textContent = s.lid_open ? "lid open" : "lid closed";
  lp.className = "pill " + (s.lid_open ? "open" : "closed");
  document.getElementById("plshow").textContent = s.current_plate ? ("plate " + s.current_plate) : "plate —";
  document.getElementById("feedsPill").textContent = s.feeds_this_window + " feed" + (s.feeds_this_window==1?"":"s") + " tonight";

  // status tiles
  let motion;
  if (s.motion_source !== "camera") motion = "frame diff";
  else if (s.camera_events_ok === false) motion = "events down";
  else if (s.camera_animal) motion = "🐾 animal";
  else if (s.camera_motion) motion = "motion";
  else motion = "quiet";
  const tiles = [
    ["Window", s.active_window + (s.in_window ? " · active" : " · off")],
    ["Next feed", s.next_allowed_feed_in_s ? fmtIn(s.next_allowed_feed_in_s) : "now"],
    ["Loaded plates", (s.loaded_plates&&s.loaded_plates.length) ? s.loaded_plates.join(", ") : "none"],
    ["Camera", s.camera_age_s==null ? "no frames" : fmtAge(s.camera_age_s)],
    ["Motion", motion],
    ["Detector", {cascade:"Cascade (local + Claude)",yolo:"YOLO (local)",claude:"Claude"}[s.identifier]||s.identifier||"—"],
    ["Checks today", s.llm_calls_today + (s.llm_cost_today_usd ? " · $"+s.llm_cost_today_usd.toFixed(2) : "")],
    ["Heartbeat", fmtAge(s.heartbeat_age_s)],
  ].filter(t => t[1] !== "");
  document.getElementById("tiles").innerHTML = tiles.map(([l,val]) =>
    `<div class="tile"><div class="l">${l}</div><div class="val">${val}</div></div>`).join("");

  if (!live) document.getElementById("live").src = "/frame.jpg?t=" + Date.now();
  if (!Object.keys(plates).length) { plates = s.plates; renderPlates(); }
  renderRefs(s.reference_counts || {});
}

function esc(t){return (t||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}

function renderPlates() {
  document.getElementById("plates").innerHTML = [1,2,3].map(p =>
    `<div class="chip ${plates[p]==="loaded"?"on":""}" onclick="togglePlate(${p})">Bowl ${p}<span class="pn">${plates[p]==="loaded"?"full":"empty"}</span></div>`).join("");
}
function togglePlate(p){ plates[p] = plates[p]==="loaded" ? "empty" : "loaded"; renderPlates(); }
async function savePlates(){
  const loaded = [1,2,3].filter(p => plates[p]==="loaded");
  await fetch("/api/plates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({loaded})});
  plates = {}; toast("Loaded plates saved"); refresh();
}

function renderRefs(counts){
  document.getElementById("refs").innerHTML = ANIMALS.map(a =>
    `<div class="ref"><div class="name">${NAMES[a]||a}</div><div class="cnt">${counts[a]||0} photo${(counts[a]||0)==1?"":"s"}</div>`+
    `<input type="file" accept="image/*" multiple id="f_${a}" onchange="uploadRefs('${a}')">`+
    `<div class="btnrow"><button class="sm" onclick="grabRef('${a}')">Grab frame</button></div></div>`).join("");
}
async function uploadRefs(a){
  const el = document.getElementById("f_"+a); const n = el.files.length;
  for (const file of el.files){
    const buf = await file.arrayBuffer();
    await fetch("/api/reference/"+a+"?filename="+encodeURIComponent(file.name),
      {method:"POST",headers:{"Content-Type":"application/octet-stream"},body:buf});
  }
  el.value=""; toast(`Added ${n} photo${n==1?"":"s"} to ${NAMES[a]||a}`); refresh();
}
async function grabRef(a){
  const r = await fetch("/api/reference/"+a+"/capture",{method:"POST"});
  if (!r.ok){ toast("No camera frame to grab", true); return; }
  toast("Saved a frame to " + (NAMES[a]||a)); refresh();
}
async function reloadRefs(){ await fetch("/api/reload-references",{method:"POST"}); toast("Reloaded reference photos"); refresh(); }

async function feeder(action){
  if (!confirm(action[0].toUpperCase()+action.slice(1)+" the feeder now?")) return;
  await fetch("/api/feeder/"+action,{method:"POST"});
  toast(action[0].toUpperCase()+action.slice(1)+" requested");
  for (let i=1;i<=6;i++) setTimeout(refresh, i*700);
}
async function feedNow(){
  if (!confirm("Feed now? This opens the feeder, skipping identification.")) return;
  await fetch("/api/feed-now",{method:"POST"});
  toast("Feed requested");
  for (let i=1;i<=6;i++) setTimeout(refresh, i*700);
}

async function loadConfig(){
  const c = await (await fetch("/api/config")).json();
  let html = ""; let group = "";
  for (const [k,label,type,grp] of CONTROLS){
    if (c[k] === undefined) continue;
    if (grp !== group){ group = grp; html += `<div class="setgrp">${grp}</div>`; }
    let input;
    if (type === "bool") input = `<input type="checkbox" id="c_${k}" ${c[k]?"checked":""}>`;
    else if (type.startsWith("select:")) {
      const opts = type.slice(7).split(",").map(o => `<option ${c[k]==o?"selected":""}>${o}</option>`).join("");
      input = `<select id="c_${k}">${opts}</select>`;
    } else input = `<input type="${type}" step="any" id="c_${k}" value="${c[k]}">`;
    html += `<div class="field"><label for="c_${k}">${label}</label>${input}</div>`;
  }
  document.getElementById("controls").innerHTML = html;
}
async function saveConfig(){
  const body = {};
  for (const [k,label,type] of CONTROLS){
    const el = document.getElementById("c_"+k); if (!el) continue;
    body[k] = type === "bool" ? el.checked : el.value;
  }
  const r = await fetch("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if (!r.ok){ toast("Rejected: " + (await r.text()), true); } else { toast("Settings saved"); loadConfig(); }
}

async function loadEvents(){
  const all = document.getElementById("allEvents").checked;
  const evs = await (await fetch("/api/events?all=" + all)).json();
  if (!evs.length){ document.getElementById("events").innerHTML = '<div class="hint">No activity yet.</div>'; return; }
  document.getElementById("events").innerHTML = evs.map(e => {
    const [ico,label] = EV[e.kind] || ["•", e.kind];
    const when = e.at.replace("T"," ").slice(5,16);
    const frames = e.frames.length ? ` · <a href="/events/${e.id}">${e.frames.length} frame${e.frames.length==1?"":"s"}</a>` : "";
    return `<div class="ev"><div class="ico">${ico}</div><div class="body">`+
      `<div class="title"><a href="/events/${e.id}">${label}</a></div>`+
      `<div class="meta">${when}${e.reason?" · "+esc(e.reason):""}${frames}</div></div></div>`;
  }).join("");
}

// Live view: MJPEG while playing; a still refreshed with status while paused. Phones drop the
// stream when backgrounded and the server ends it periodically, so reconnect on error/visibility.
function startLive(){ document.getElementById("live").src = "/stream.mjpg?t=" + Date.now(); }
function toggleLive(){
  live = !live;
  document.getElementById("liveBtn").textContent = live ? "Pause" : "Resume";
  document.getElementById("liveChip").style.display = live ? "" : "none";
  if (live) startLive(); else refresh();
}
document.getElementById("live").onerror = () => { if (live) setTimeout(startLive, 3000); };
document.addEventListener("visibilitychange", () => { if (live && !document.hidden) startLive(); });
startLive(); refresh(); loadConfig(); loadEvents();
setInterval(refresh, 1500); setInterval(loadEvents, 15000);
</script></body></html>
"""

EVENT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>event {id}</title>
<style>
 :root{{--bg:#0e0f13;--surface:#171920;--border:#2a2e3a;--fg:#e7e9ef;--muted:#949aa7;--accent:#6aa9ff}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
 .wrap{{max-width:760px;margin:0 auto;padding:16px}}
 a{{color:var(--accent);text-decoration:none}}
 h2{{margin:10px 0 2px;font-size:20px}}
 .meta{{color:var(--muted);font-size:13px;margin-bottom:14px}}
 .reason{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;margin:12px 0}}
 img{{width:100%;border-radius:12px;margin:8px 0;border:1px solid var(--border)}}
 details{{margin-top:12px}} summary{{color:var(--muted);cursor:pointer;font-size:13px}}
 pre{{background:var(--surface);border:1px solid var(--border);padding:12px;border-radius:12px;
   white-space:pre-wrap;word-break:break-word;font-size:12px;color:var(--muted)}}
</style></head><body><div class="wrap">
<a href="/">&larr; Back to dashboard</a>
<h2>{kind}</h2>
<div class="meta">event #{id} · {at}</div>
<div class="reason">{reason}</div>
{images}
<details><summary>Raw data</summary><pre>{data}</pre></details>
</div></body></html>"""
