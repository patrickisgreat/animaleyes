import { useEffect, useState } from "react";
import type { Persona } from "../lib/api";
import { api, nameMap } from "../lib/api";
import { Button, Card, useLightbox, useToast } from "../lib/ui";

function PhotoGrid({
  set,
  animal,
  reload,
  roster,
}: {
  set: string;
  animal: string;
  reload: number;
  roster: Persona[];
}) {
  const toast = useToast();
  const lightbox = useLightbox();
  const [data, setData] = useState<{ total: number; files: string[] }>({ total: 0, files: [] });
  const load = () => api.photos(set, animal).then(setData).catch(() => {});
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [animal, reload]);

  const names = nameMap(roster);
  const src = (n: string) => `/photos/${set}/${animal}/${encodeURIComponent(n)}`;
  const others = roster.map((p) => p.key).filter((k) => k !== animal);
  async function del(n: string) { await api.del(`/api/photos/${set}/${animal}/${encodeURIComponent(n)}`); toast("Deleted"); load(); }
  async function retag(n: string, to: string) {
    await api.post(`/api/photos/${set}/${animal}/${encodeURIComponent(n)}/retag?to=${to}`);
    toast("Re‑tagged as " + (names[to] || to)); load();
  }
  // Open the enlarged viewer with tag/delete controls so a batch can be tagged in the big view.
  const openAt = (i: number) =>
    lightbox(data.files.map((n) => ({ src: src(n), id: n })), i, {
      tags: others.map((o) => ({ to: o, label: names[o] || o })),
      onTag: (n, to) => retag(n, to),
      onDelete: (n) => del(n),
    });

  if (!data.files.length) return <div className="text-muted text-xs">none yet</div>;
  return (
    <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(92px,1fr))]">
      {data.files.map((n, i) => (
        <div key={n} className="relative rounded-lg overflow-hidden border border-edge aspect-square bg-black group">
          <img
            loading="lazy"
            src={src(n)}
            onClick={() => openAt(i)}
            className="w-full h-full object-cover cursor-zoom-in"
            title="click to enlarge & tag"
          />
          <div className="absolute inset-x-0 bottom-0 flex flex-wrap justify-center gap-1 p-1 bg-black/75
                          opacity-100 md:opacity-0 md:group-hover:opacity-100 transition">
            <button title="delete" onClick={() => del(n)} className="btn btn-sm !min-h-0 !px-1.5 !py-1 text-[10px] text-bad border-bad/40">✕</button>
            {others.map((o) => (
              <button key={o} title={`re-tag as ${names[o] || o}`} onClick={() => retag(n, o)}
                className="btn btn-sm !min-h-0 !px-1.5 !py-1 text-[10px]">{(names[o] || o)[0]}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function ReferencePhotos({
  personas,
  counts,
  refresh,
}: {
  personas: Persona[];
  counts: Record<string, number>;
  refresh: () => void;
}) {
  const toast = useToast();
  const [reload, setReload] = useState(0);

  async function upload(a: string, files: FileList | null) {
    if (!files?.length) return;
    for (const f of Array.from(files)) await api.uploadPhoto(a, f.name, await f.arrayBuffer());
    toast(`Added ${files.length} photo${files.length === 1 ? "" : "s"}`);
    setReload((x) => x + 1); refresh();
  }
  async function grab(a: string) {
    const r = await api.post(`/api/reference/${a}/capture`);
    if (!r.ok) { toast("No camera frame to grab", true); return; }
    toast("Saved a frame"); setReload((x) => x + 1); refresh();
  }

  return (
    <Card title="Reference photos">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(220px,1fr))]">
        {personas.map((p) => (
          <div key={p.key} className="bg-surface2 border border-edge rounded-[10px] p-3">
            <div className="font-bold">{p.name}{p.feedable && <span className="text-teal text-xs ml-1">• fed</span>}</div>
            <div className="text-muted text-xs mb-2">{counts[p.key] || 0} photo{(counts[p.key] || 0) === 1 ? "" : "s"}</div>
            <input type="file" accept="image/*" multiple onChange={(e) => upload(p.key, e.target.files)} className="text-xs w-full text-muted" />
            <div className="mt-2 mb-2"><Button sm onClick={() => grab(p.key)}>Grab frame</Button></div>
            <PhotoGrid set="reference" animal={p.key} reload={reload} roster={personas} />
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

export function TrainingGallery({
  personas,
  counts,
  captureMode,
}: {
  personas: Persona[];
  counts: Record<string, number>;
  captureMode?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const unlabeled = counts["unlabeled"] || 0;
  return (
    <Card>
      <button className="h2 flex items-center gap-2 w-full text-left" onClick={() => setOpen((x) => !x)}>
        Training data — review &amp; tag frames
        {unlabeled > 0 && <span className="text-xs font-bold px-2 py-0.5 rounded-full bg-teal/15 text-teal border border-teal/40">{unlabeled} to tag</span>}
        <span className="text-muted ml-auto">{open ? "▴" : "▾"}</span>
      </button>
      <div className="text-muted text-xs mb-3">
        Frames auto‑saved when Claude confirmed a dog, pre‑labelled. Cull the bad ones and fix any wrong
        label (re‑tag) — this becomes the dataset for a local model, so Claude can be dropped.
      </div>
      {open && (
        <>
          <div className="mb-4 rounded-[10px] border border-teal/40 bg-teal/5 p-3">
            <div className="font-bold mb-1 flex items-center gap-2">
              Unlabeled
              <span className="text-muted font-normal text-sm">{unlabeled} frame{unlabeled === 1 ? "" : "s"}</span>
              <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full border ${captureMode ? "bg-teal/15 text-teal border-teal/40" : "bg-surface2 text-muted border-edge"}`}>
                capture mode {captureMode ? "on" : "off"}
              </span>
            </div>
            <div className="text-muted text-xs mb-2">
              {captureMode
                ? "Capture mode is saving every animal it sees here. Tap a letter on a frame to file it under an animal, or ✕ to discard."
                : "Turn on capture mode in Settings to collect frames here; then tag each one for training."}
            </div>
            <PhotoGrid set="training" animal="unlabeled" reload={open ? unlabeled : 0} roster={personas} />
          </div>
          {personas.map((p) => (
            <div key={p.key} className="mb-3.5">
              <div className="font-bold mb-2">
                {p.name} <span className="text-muted font-normal text-sm">{counts[p.key] || 0} frames</span>
              </div>
              <PhotoGrid set="training" animal={p.key} reload={open ? 1 : 0} roster={personas} />
            </div>
          ))}
        </>
      )}
    </Card>
  );
}
