import { useEffect, useState } from "react";
import type { Status } from "../lib/api";
import { api } from "../lib/api";
import { Button, Card, useToast } from "../lib/ui";

type Preset = { token: string; name: string };

export function CameraPtz({ s }: { s: Status | null }) {
  const toast = useToast();
  const [presets, setPresets] = useState<Preset[]>([]);
  const loadPresets = () =>
    fetch("/api/ptz/presets").then((r) => r.json()).then((d) => setPresets(d.presets || [])).catch(() => {});
  useEffect(() => { if (s?.ptz_available) loadPresets(); /* eslint-disable-next-line */ }, [s?.ptz_available]);

  if (!s?.ptz_available) return null;

  const nudge = (dir: string) => api.post(`/api/ptz/nudge?dir=${dir}`);
  async function savePreset() {
    const name = prompt("Name this camera position (e.g. “Feeder view”):");
    if (!name) return;
    await api.post("/api/ptz/preset", { name });
    toast("Saved position"); loadPresets();
  }
  async function goto(p: Preset) { await api.post(`/api/ptz/goto?token=${encodeURIComponent(p.token)}`); toast("Moving to " + p.name); }

  const Pad = ({ dir, label }: { dir: string; label: string }) => (
    <button
      onClick={() => nudge(dir)}
      className="btn !min-h-0 w-11 h-11 text-lg grid place-items-center"
      title={`pan/tilt ${dir}`}
    >
      {label}
    </button>
  );

  return (
    <Card title="Camera angle">
      <div className="flex flex-wrap items-start gap-5">
        <div className="grid grid-cols-3 gap-1.5 w-max">
          <span />
          <Pad dir="up" label="▲" />
          <span />
          <Pad dir="left" label="◀" />
          <button onClick={() => api.post("/api/ptz/stop")} className="btn !min-h-0 w-11 h-11 text-xs" title="stop">■</button>
          <Pad dir="right" label="▶" />
          <span />
          <Pad dir="down" label="▼" />
          <span />
        </div>
        <div className="flex-1 min-w-[160px]">
          <div className="flex flex-wrap gap-2 mb-2">
            {presets.map((p) => (
              <button key={p.token} onClick={() => goto(p)} className="btn btn-sm">{p.name}</button>
            ))}
            {!presets.length && <span className="text-muted text-xs">No saved positions yet.</span>}
          </div>
          <Button sm onClick={savePreset}>Save current position</Button>
          <div className="text-muted text-xs mt-2.5">
            Tap an arrow to nudge the camera; ■ stops it. Save the feeder framing as a position to
            recall it with one tap.
          </div>
        </div>
      </div>
    </Card>
  );
}
