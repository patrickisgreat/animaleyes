import { useState } from "react";
import type { Persona } from "../lib/api";
import { api } from "../lib/api";
import { Button, Card, useToast } from "../lib/ui";

export function Personas({ personas, refresh }: { personas: Persona[]; refresh: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, { name: string; description: string }>>({});
  const [adding, setAdding] = useState({ name: "", description: "" });

  const edit = (key: string, patch: Partial<{ name: string; description: string }>, base: Persona) =>
    setDraft((d) => {
      const cur = d[key] || { name: base.name, description: base.description };
      return { ...d, [key]: { ...cur, ...patch } };
    });

  async function save(p: Persona) {
    const d = draft[p.key] || { name: p.name, description: p.description };
    const r = await api.post("/api/personas", { key: p.key, name: d.name, description: d.description });
    if (!r.ok) { toast("Rejected: " + (await r.text()), true); return; }
    toast("Saved " + d.name); refresh();
  }
  async function add() {
    if (!adding.name.trim()) { toast("Name required", true); return; }
    const r = await api.post("/api/personas", adding);
    if (!r.ok) { toast("Rejected: " + (await r.text()), true); return; }
    toast("Added " + adding.name); setAdding({ name: "", description: "" }); refresh();
  }
  async function del(p: Persona) {
    if (!confirm(`Delete ${p.name}? Its reference & training photos are removed too.`)) return;
    const r = await api.del(`/api/personas/${p.key}`);
    if (!r.ok) { toast("Rejected: " + (await r.text()), true); return; }
    toast("Deleted " + p.name); refresh();
  }

  return (
    <Card>
      <button className="h2 flex items-center gap-2 w-full text-left" onClick={() => setOpen((x) => !x)}>
        Animals &amp; personas <span className="text-muted ml-auto">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <>
          <div className="text-muted text-xs mb-3">
            The roster the camera recognises. Edit a name or its description (what Claude uses to tell them
            apart), add another pet, or remove one. Only the animal marked <b className="text-teal">fed</b> is
            ever fed — that’s locked and can’t be moved.
          </div>
          {personas.map((p) => {
            const d = draft[p.key] || { name: p.name, description: p.description };
            return (
              <div key={p.key} className="border-b border-edge py-3">
                <div className="flex items-center gap-2 mb-1.5">
                  <input
                    className="input !max-w-none flex-1 font-bold"
                    value={d.name}
                    onChange={(e) => edit(p.key, { name: e.target.value }, p)}
                  />
                  {p.feedable ? (
                    <span className="text-teal text-xs font-bold px-2 py-1 rounded-full bg-teal/15 border border-teal/40">fed</span>
                  ) : (
                    <Button sm variant="danger" onClick={() => del(p)} title="delete">✕</Button>
                  )}
                </div>
                <textarea
                  value={d.description}
                  onChange={(e) => edit(p.key, { description: e.target.value }, p)}
                  className="w-full bg-bg text-ink border border-edge rounded-lg p-2.5 text-sm resize-y min-h-[52px]"
                />
                <div className="mt-2"><Button sm variant="primary" onClick={() => save(p)}>Save</Button></div>
              </div>
            );
          })}
          <div className="mt-3 bg-surface2 border border-edge rounded-[10px] p-3">
            <div className="font-bold text-sm mb-2">Add an animal</div>
            <input
              className="input !max-w-none w-full mb-2"
              placeholder="Name (e.g. another cat)"
              value={adding.name}
              onChange={(e) => setAdding((a) => ({ ...a, name: e.target.value }))}
            />
            <textarea
              placeholder="Description to help Claude recognise it (size, coat, ears…). Never fed."
              value={adding.description}
              onChange={(e) => setAdding((a) => ({ ...a, description: e.target.value }))}
              className="w-full bg-bg text-ink border border-edge rounded-lg p-2.5 text-sm resize-y min-h-[52px]"
            />
            <div className="mt-2"><Button sm onClick={add}>Add animal</Button></div>
          </div>
        </>
      )}
    </Card>
  );
}
