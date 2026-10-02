import { useState } from "react";
import { api } from "../lib/api";
import { Button, Card, useToast } from "../lib/ui";
import type { ThemeDef } from "../lib/theme";
import { TOKENS, activeThemeId, applyTheme, currentColors, previewColors } from "../lib/theme";

type Editing = { id?: string; name: string; colors: Record<string, string> };

export function Themes({ themes, refresh }: { themes: ThemeDef[]; refresh: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);

  function startNew() {
    setEditing({ name: "", colors: currentColors() }); // seed from the theme on screen
  }
  function startEdit(t: ThemeDef) {
    const colors = { ...currentColors(), ...t.colors };
    setEditing({ id: t.id, name: t.name, colors });
    previewColors(colors);
  }
  function restoreActive() {
    const id = activeThemeId();
    const def = themes.find((t) => t.id === id);
    applyTheme(id, def);
  }
  async function save() {
    if (!editing) return;
    if (!editing.name.trim()) { toast("Name required", true); return; }
    const r = await api.post("/api/themes", editing);
    if (!r.ok) { toast("Rejected: " + (await r.text()), true); return; }
    const saved: ThemeDef = await r.json();
    applyTheme(saved.id, saved); // make the saved theme the active one
    toast("Saved " + saved.name);
    setEditing(null);
    refresh();
  }
  async function del(t: ThemeDef) {
    if (!confirm(`Delete theme "${t.name}"?`)) return;
    const r = await api.del(`/api/themes/${t.id}`);
    if (!r.ok) { toast("Rejected", true); return; }
    toast("Deleted " + t.name);
    refresh();
  }

  return (
    <Card>
      <button className="h2 flex items-center gap-2 w-full text-left" onClick={() => setOpen((x) => !x)}>
        Themes <span className="text-muted ml-auto">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <>
          <div className="text-muted text-xs mb-3">
            Sage and Aurora are built in. Make your own: it seeds from the theme you’re viewing, so
            tweak a few colours and save. Switch between them from the swatches up top.
          </div>

          <div className="flex flex-wrap gap-2 mb-3">
            {themes.length === 0 && <span className="text-muted text-xs">No custom themes yet.</span>}
            {themes.map((t) => (
              <div key={t.id} className="flex items-center gap-1.5 bg-surface2 border border-edge rounded-full pl-1 pr-2 py-1">
                <span
                  className="w-5 h-5 rounded-full border border-edge"
                  style={{ background: `linear-gradient(135deg,${t.colors.title1 || t.colors.teal || "#888"},${t.colors.title2 || t.colors.beige || "#ccc"})` }}
                />
                <span className="text-xs font-bold">{t.name}</span>
                <button onClick={() => startEdit(t)} className="text-muted hover:text-ink text-xs px-1" title="edit">✎</button>
                <button onClick={() => del(t)} className="text-bad text-xs px-1" title="delete">✕</button>
              </div>
            ))}
          </div>

          {!editing ? (
            <Button sm onClick={startNew}>New theme</Button>
          ) : (
            <div className="bg-surface2 border border-edge rounded-[10px] p-3">
              <input
                className="input !max-w-none w-full mb-3"
                placeholder="Theme name"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
              <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(130px,1fr))]">
                {TOKENS.map((tok) => (
                  <label key={tok.key} className="flex items-center gap-2 text-xs">
                    <input
                      type="color"
                      value={editing.colors[tok.key] || "#000000"}
                      onChange={(e) => {
                        const colors = { ...editing.colors, [tok.key]: e.target.value };
                        setEditing({ ...editing, colors });
                        previewColors(colors);
                      }}
                      className="w-7 h-7 rounded border border-edge bg-transparent cursor-pointer p-0"
                    />
                    <span className="text-muted">{tok.label}</span>
                  </label>
                ))}
              </div>
              <div className="flex gap-2 mt-3">
                <Button sm variant="primary" onClick={save}>Save theme</Button>
                <Button sm onClick={() => { setEditing(null); restoreActive(); }}>Cancel</Button>
              </div>
              <div className="text-muted text-xs mt-2">Colours preview live as you pick them.</div>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
