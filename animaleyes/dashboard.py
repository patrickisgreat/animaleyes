"""Single-page dashboard. No build step; the page polls JSON and a JPEG every 3 seconds.

Every route requires ?token=DASH_TOKEN. Controls write config.toml or set flags the state
machine consumes on its next tick; the dashboard never talks to the feeder itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .config import ConfigStore, Secrets
from .machine import Machine
from .store import Store

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
)


def create_app(
    machine: Machine,
    store: Store,
    config: ConfigStore,
    secrets: Secrets,
    frames_dir: Path,
    reload_references=None,
) -> FastAPI:
    app = FastAPI(title="animaleyes", docs_url=None, redoc_url=None)

    def auth(token: str = Query(default="")) -> None:
        if not secrets.dash_token or token != secrets.dash_token:
            raise HTTPException(status_code=401, detail="bad token")

    guarded = [Depends(auth)]

    @app.get("/", response_class=HTMLResponse, dependencies=guarded)
    def index(token: str) -> str:
        return PAGE.replace("__TOKEN__", token)

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
            "last_verdict": verdict,
            "last_llm_at": machine.last_llm_at.isoformat(timespec="seconds")
            if machine.last_llm_at
            else None,
            "reference_counts": getattr(machine.llm, "reference_counts", {}),
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

    @app.post("/api/reload-references", dependencies=guarded)
    def reload_refs() -> dict[str, Any]:
        if reload_references:
            reload_references()
        return {"reference_counts": getattr(machine.llm, "reference_counts", {})}

    @app.get("/frame.jpg", dependencies=guarded)
    def frame() -> Response:
        frames = machine.frames.latest(1)
        if not frames:
            raise HTTPException(status_code=404, detail="no frame yet")
        return Response(
            content=frames[0].jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/events", dependencies=guarded)
    def events(all: bool = False, limit: int = 100) -> JSONResponse:
        rows = store.events(limit=limit, kinds=None if all else MAIN_EVENT_KINDS)
        return JSONResponse([_event_json(e) for e in rows])

    @app.get("/events/{event_id}", response_class=HTMLResponse, dependencies=guarded)
    def event_page(event_id: int, token: str) -> str:
        event = store.event(event_id)
        if not event:
            raise HTTPException(status_code=404)
        images = "".join(
            f'<img src="/frames/{name}?token={token}" alt="{name}">' for name in event.frames
        )
        return EVENT_PAGE.format(
            id=event.id,
            at=event.at.strftime("%Y-%m-%d %H:%M:%S"),
            kind=event.kind,
            reason=_escape(event.reason),
            data=_escape(json.dumps(event.data, indent=2)),
            images=images,
            token=token,
        )

    @app.get("/frames/{name}", dependencies=guarded)
    def frame_file(name: str) -> FileResponse:
        path = (frames_dir / Path(name).name).resolve()
        if not path.is_file() or frames_dir.resolve() not in path.parents:
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="image/jpeg")

    return app


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
 :root{--bg:#111;--fg:#eee;--muted:#999;--card:#1c1c1c;--ok:#4caf50;--warn:#ff9800;--bad:#f44336;--accent:#6aa9ff}
 body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,sans-serif;padding:12px}
 h1{font-size:20px;margin:0 0 12px}
 .card{background:var(--card);border-radius:10px;padding:12px;margin-bottom:12px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px}
 .k{color:var(--muted);font-size:13px}
 .v{font-weight:600;word-break:break-word}
 .state{font-size:28px;font-weight:800}
 .ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
 img#live{width:100%;border-radius:8px;background:#000;min-height:120px}
 .overlay{position:relative}
 .overlay pre{position:absolute;left:8px;bottom:8px;margin:0;background:rgba(0,0,0,.65);color:#fff;
   padding:6px 8px;border-radius:6px;font-size:12px;max-width:90%;white-space:pre-wrap}
 label{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #2a2a2a}
 input[type=text],input[type=number]{width:110px;background:#222;color:#fff;border:1px solid #444;border-radius:6px;padding:6px;font-size:16px}
 button{background:var(--accent);color:#000;border:0;border-radius:8px;padding:10px 14px;font-weight:700;font-size:15px;margin:6px 6px 0 0}
 button.danger{background:var(--bad);color:#fff}
 button.plain{background:#333;color:#fff}
 .ev{border-bottom:1px solid #2a2a2a;padding:8px 0}
 .ev a{color:var(--accent);text-decoration:none}
 .ev .kind{font-weight:700}
 .ev .when{color:var(--muted);font-size:12px}
 .plates button{margin-right:6px}
 .plates .on{background:var(--ok)}
 small{color:var(--muted)}
</style></head><body>
<h1>animaleyes <small id="mode"></small></h1>

<div class="card">
  <div class="state" id="state">…</div>
  <div class="grid" id="status"></div>
</div>

<div class="card overlay">
  <img id="live" alt="latest frame">
  <pre id="verdict"></pre>
</div>

<div class="card">
  <div class="k">Plates loaded (tap to toggle, then Save — resets counters and returns to IDLE)</div>
  <div class="plates" id="plates"></div>
  <button onclick="savePlates()">Save plates</button>
  <button class="plain" onclick="reloadRefs()">Reload reference photos</button>
  <button class="danger" onclick="feedNow()">Feed now</button>
</div>

<div class="card">
  <div class="k">Controls (saved to config.toml, applied on the next loop)</div>
  <div id="controls"></div>
  <button onclick="saveConfig()">Save settings</button>
</div>

<div class="card">
  <div class="k">Events <label style="display:inline;border:0"><input type="checkbox" id="allEvents" onchange="loadEvents()"> include transitions</label></div>
  <div id="events"></div>
</div>

<script>
const T = "__TOKEN__";
const q = (p) => p + (p.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(T);
const CONTROLS = [
  ["ENABLED","bool"],["DRY_RUN","bool"],["ACTIVE_START","text"],["ACTIVE_END","text"],
  ["MIN_GAP_MIN","number"],["LEAVE_TIMEOUT_S","number"],["FEEDING_MAX_MIN","number"],
  ["FEEDING_POLL_S","number"],["CONFIRMATIONS_REQUIRED","number"],["GRRR_MIN_CONF","number"],
  ["MOTION_PIXEL_FRACTION","number"],["MOTION_HOLD_S","number"],["LLM_MIN_INTERVAL_S","number"],
  ["HEARTBEAT_MIN","number"],["LLM_MODEL","text"]];
let plates = {};
const fmt = (s) => s == null ? "—" : (s < 90 ? s + "s" : Math.round(s/60) + "m");

async function refresh() {
  const s = await (await fetch(q("/api/status"))).json();
  const st = document.getElementById("state");
  st.textContent = s.state;
  st.className = "state " + (s.state === "FEEDING" ? "ok" : (s.camera_offline ? "bad" : ""));
  document.getElementById("mode").textContent = (s.dry_run ? "DRY RUN" : "LIVE") + (s.enabled ? "" : " · DISABLED");
  const rows = [
    ["Window", s.active_window + (s.in_window ? " (active)" : " (outside)")],
    ["Plates", Object.entries(s.plates).map(([p,v]) => p + ":" + v).join(" ")],
    ["Feeds this window", s.feeds_this_window],
    ["Next feed allowed", s.next_allowed_feed_in_s ? "in " + fmt(s.next_allowed_feed_in_s) : "now"],
    ["Heartbeat age", fmt(s.heartbeat_age_s)],
    ["Camera frame age", s.camera_age_s == null ? "no frames" : fmt(s.camera_age_s)],
    ["Motion", s.motion_fraction],
    ["LLM today", s.llm_calls_today + " calls · $" + s.llm_cost_today_usd.toFixed(3)],
    ["Model", s.llm_model],
    ["Reference photos", Object.entries(s.reference_counts).map(([a,n]) => a + ":" + n).join(" ")],
    ["Feeding since", s.feeding_since || "—"],
  ];
  document.getElementById("status").innerHTML = rows.map(([k,v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join("");
  document.getElementById("verdict").textContent = s.last_verdict
    ? (s.last_llm_at || "") + "\\n" + JSON.stringify(s.last_verdict) : "no LLM verdict yet";
  document.getElementById("live").src = q("/frame.jpg") + "&t=" + Date.now();
  if (!Object.keys(plates).length) { plates = s.plates; renderPlates(); }
}
function renderPlates() {
  document.getElementById("plates").innerHTML = [1,2,3].map(p =>
    `<button class="${plates[p] === "loaded" ? "on" : "plain"}" onclick="togglePlate(${p})">Plate ${p}: ${plates[p]}</button>`).join("");
}
function togglePlate(p) { plates[p] = plates[p] === "loaded" ? "empty" : "loaded"; renderPlates(); }
async function savePlates() {
  const loaded = [1,2,3].filter(p => plates[p] === "loaded");
  await fetch(q("/api/plates"), {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({loaded})});
  plates = {}; refresh();
}
async function feedNow() {
  if (!confirm("Open the feeder now? This ignores MIN_GAP and identification.")) return;
  const r = await (await fetch(q("/api/feed-now"), {method:"POST"})).json(); alert(r.ok);
}
async function reloadRefs() {
  const r = await (await fetch(q("/api/reload-references"), {method:"POST"})).json();
  alert("reference photos: " + JSON.stringify(r.reference_counts));
}
async function loadConfig() {
  const c = await (await fetch(q("/api/config"))).json();
  document.getElementById("controls").innerHTML = CONTROLS.map(([k,t]) => t === "bool"
    ? `<label>${k}<input type="checkbox" id="c_${k}" ${c[k] ? "checked" : ""}></label>`
    : `<label>${k}<input type="${t}" step="any" id="c_${k}" value="${c[k]}"></label>`).join("");
}
async function saveConfig() {
  const body = {};
  for (const [k,t] of CONTROLS) { const el = document.getElementById("c_"+k); body[k] = t === "bool" ? el.checked : el.value; }
  const r = await fetch(q("/api/config"), {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  if (!r.ok) alert("rejected: " + (await r.text())); else loadConfig();
}
async function loadEvents() {
  const all = document.getElementById("allEvents").checked;
  const evs = await (await fetch(q("/api/events?all=" + all))).json();
  document.getElementById("events").innerHTML = evs.map(e =>
    `<div class="ev"><span class="when">${e.at.replace("T"," ")}</span> <a href="${q("/events/"+e.id)}"><span class="kind">${e.kind}</span></a> ${e.reason}` +
    (e.frames.length ? ` <a href="${q("/events/"+e.id)}">[${e.frames.length} frames]</a>` : "") + `</div>`).join("") || "<small>no events yet</small>";
}
refresh(); loadConfig(); loadEvents();
setInterval(refresh, 3000); setInterval(loadEvents, 15000);
</script></body></html>
"""

EVENT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>event {id}</title>
<style>body{{background:#111;color:#eee;font:16px system-ui,sans-serif;padding:12px}}
img{{width:100%;border-radius:8px;margin:6px 0}}pre{{background:#1c1c1c;padding:10px;border-radius:8px;white-space:pre-wrap}}
a{{color:#6aa9ff}}</style></head><body>
<a href="/?token={token}">&larr; dashboard</a>
<h2>{kind} <small>#{id} · {at}</small></h2>
<p>{reason}</p>
<pre>{data}</pre>
{images}
</body></html>"""
