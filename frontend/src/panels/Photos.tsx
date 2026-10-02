import { useEffect, useState } from "react";
import { ANIMALS, NAMES, api } from "../lib/api";
import { Button, Card, useToast } from "../lib/ui";

function PhotoGrid({ set, animal, reload }: { set: string; animal: string; reload: number }) {
  const toast = useToast();
  const [data, setData] = useState<{ total: number; files: string[] }>({ total: 0, files: [] });
  const load = () => api.photos(set, animal).then(setData).catch(() => {});
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [animal, reload]);

  const others = ANIMALS.filter((a) => a !== animal);
  async function del(n: string) { await api.del(`/api/photos/${set}/${animal}/${encodeURIComponent(n)}`); toast("Deleted"); load(); }
  async function retag(n: string, to: string) {
    await api.post(`/api/photos/${set}/${animal}/${encodeURIComponent(n)}/retag?to=${to}`);
    toast("Re‑tagged as " + (NAMES[to] || to)); load();
  }

  if (!data.files.length) return <div className="text-muted text-xs">none yet</div>;
  return (
    <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(92px,1fr))]">
      {data.files.map((n) => (
        <div key={n} className="relative rounded-lg overflow-hidden border border-edge aspect-square bg-black group">
          <img loading="lazy" src={`/photos/${set}/${animal}/${encodeURIComponent(n)}`} className="w-full h-full object-cover" />
          <div className="absolute inset-x-0 bottom-0 flex justify-center gap-1 p-1 bg-black/75
                          opacity-100 md:opacity-0 md:group-hover:opacity-100 transition">
            <button title="delete" onClick={() => del(n)} className="btn btn-sm !min-h-0 !px-1.5 !py-1 text-[10px] text-bad border-bad/40">✕</button>
            {others.map((o) => (
              <button key={o} title={`re-tag as ${NAMES[o] || o}`} onClick={() => retag(n, o)}
                className="btn btn-sm !min-h-0 !px-1.5 !py-1 text-[10px]">{(NAMES[o] || o)[0]}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function ReferencePhotos({ counts, refresh }: { counts: Record<string, number>; refresh: () => void }) {
  const toast = useToast();
  const [reload, setReload] = useState(0);

  async function upload(a: string, files: FileList | null) {
    if (!files?.length) return;
    for (const f of Array.from(files)) await api.uploadPhoto(a, f.name, await f.arrayBuffer());
    toast(`Added ${files.length} photo${files.length === 1 ? "" : "s"} to ${NAMES[a] || a}`);
    setReload((x) => x + 1); refresh();
  }
  async function grab(a: string) {
    const r = await api.post(`/api/reference/${a}/capture`);
    if (!r.ok) { toast("No camera frame to grab", true); return; }
    toast("Saved a frame to " + (NAMES[a] || a)); setReload((x) => x + 1); refresh();
  }

  return (
    <Card title="Reference photos">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
        {ANIMALS.map((a) => (
          <div key={a} className="bg-surface2 border border-edge rounded-[10px] p-3">
            <div className="font-bold capitalize">{NAMES[a] || a}</div>
            <div className="text-muted text-xs mb-2">{counts[a] || 0} photo{(counts[a] || 0) === 1 ? "" : "s"}</div>
            <input type="file" accept="image/*" multiple onChange={(e) => upload(a, e.target.files)} className="text-xs w-full text-muted" />
            <div className="mt-2 mb-2"><Button sm onClick={() => grab(a)}>Grab frame</Button></div>
            <PhotoGrid set="reference" animal={a} reload={reload} />
          </div>
        ))}
      </div>
      <div className="text-muted text-xs mt-3">
        What the detector learns each animal from. At night the camera is infra‑red, so a black coat is
        invisible — “Grab frame” captures real IR shots to teach it size and shape.
      </div>
    </Card>
  );
}

export function TrainingGallery({ counts }: { counts: Record<string, number> }) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <button className="h2 flex items-center gap-2 w-full text-left" onClick={() => setOpen((x) => !x)}>
        Training data — review &amp; tag frames <span className="text-muted">{open ? "▴" : "▾"}</span>
      </button>
      <div className="text-muted text-xs mb-3">
        Frames auto‑saved when Claude confirmed a dog, pre‑labelled. Cull the bad ones and fix any wrong
        label (re‑tag) — this becomes the dataset for a local Grrr‑vs‑Bowie model, so Claude can be dropped.
      </div>
      {open &&
        ANIMALS.map((a) => (
          <div key={a} className="mb-3.5">
            <div className="font-bold capitalize mb-2">
              {NAMES[a] || a} <span className="text-muted font-normal text-sm">{counts[a] || 0} frames</span>
            </div>
            <PhotoGrid set="training" animal={a} reload={open ? 1 : 0} />
          </div>
        ))}
    </Card>
  );
}
