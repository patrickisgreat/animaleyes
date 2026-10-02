import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, Card, useToast } from "../lib/ui";

const FIELDS: [string, string][] = [
  ["GRRR_DESC", "Grrr"], ["BOWIE_DESC", "Bowie"], ["CAT_DESC", "Cat"],
];

export function Descriptions() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [vals, setVals] = useState<Record<string, string>>({});

  useEffect(() => {
    api.config().then((c) => setVals(Object.fromEntries(FIELDS.map(([k]) => [k, String(c[k] ?? "")]))));
  }, []);

  async function save() {
    const r = await api.post("/api/config", vals);
    if (!r.ok) toast("Rejected: " + (await r.text()), true);
    else toast("Descriptions saved");
  }

  return (
    <Card>
      <button className="h2 flex items-center gap-2 w-full text-left" onClick={() => setOpen((x) => !x)}>
        Animal descriptions (for Claude) <span className="text-muted">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <>
          <div className="text-muted text-xs mb-2">
            Short, distinguishing descriptions the model uses to tell them apart. Keep “only Grrr is fed”
            explicit. Takes effect on the next check.
          </div>
          {FIELDS.map(([k, label]) => (
            <div key={k}>
              <div className="font-bold mt-3 mb-1.5 text-sm">{label}</div>
              <textarea
                value={vals[k] ?? ""}
                onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))}
                className="w-full bg-bg text-ink border border-edge rounded-lg p-2.5 text-sm resize-y min-h-[56px]"
              />
            </div>
          ))}
          <div className="mt-3"><Button variant="primary" sm onClick={save}>Save descriptions</Button></div>
        </>
      )}
    </Card>
  );
}
