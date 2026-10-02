import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { BUILTINS, activeThemeId, applyTheme } from "./theme";
import type { ThemeDef } from "./theme";

// ---- Toasts -------------------------------------------------------------
type Toast = { id: number; msg: string; err?: boolean };
const ToastCtx = createContext<(msg: string, err?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((msg: string, err?: boolean) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, err }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed left-1/2 bottom-6 -translate-x-1/2 z-50 flex flex-col gap-2 items-center">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`px-4 py-2.5 rounded-[10px] text-sm font-semibold border bg-surface2 shadow-lg
              ${t.err ? "border-bad/60 text-bad" : "border-edge text-ink"}`}
          >
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// ---- Lightbox -----------------------------------------------------------
// Click any snapshot to view it full-size, with ← → between a set and Esc to close. When opened
// with tagging options, the enlarged view also carries tag/delete controls and auto-advances
// after each action, so a whole batch can be tagged without leaving the big view.
export type LightboxItem = { src: string; id: string };
export type LightboxOpts = {
  tags?: { to: string; label: string }[];
  onTag?: (id: string, to: string) => void | Promise<unknown>;
  onDelete?: (id: string) => void | Promise<unknown>;
};
type LightboxState = { items: LightboxItem[]; i: number; opts?: LightboxOpts } | null;
type OpenLightbox = (items: (string | LightboxItem)[], i?: number, opts?: LightboxOpts) => void;
const LightboxCtx = createContext<OpenLightbox>(() => {});
export const useLightbox = () => useContext(LightboxCtx);

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [box, setBox] = useState<LightboxState>(null);
  const [busy, setBusy] = useState(false);
  const open = useCallback<OpenLightbox>((items, i = 0, opts) => {
    const norm = items.map((it) => (typeof it === "string" ? { src: it, id: it } : it));
    if (norm.length) setBox({ items: norm, i, opts });
  }, []);
  const close = useCallback(() => setBox(null), []);
  const step = useCallback(
    (d: number) => setBox((b) => (b ? { ...b, i: (b.i + d + b.items.length) % b.items.length } : b)),
    [],
  );
  // Drop the current item from the set and stay on the same slot (now the next image), closing
  // when the batch is done — this is what makes tagging feel like a stream.
  const dropCurrent = useCallback(
    () =>
      setBox((b) => {
        if (!b) return b;
        const items = b.items.filter((_, idx) => idx !== b.i);
        return items.length ? { ...b, items, i: Math.min(b.i, items.length - 1) } : null;
      }),
    [],
  );

  const act = useCallback(
    async (fn?: (id: string) => void | Promise<unknown>) => {
      if (!box || !fn || busy) return;
      setBusy(true);
      try {
        await fn(box.items[box.i].id);
        dropCurrent();
      } finally {
        setBusy(false);
      }
    },
    [box, busy, dropCurrent],
  );

  useEffect(() => {
    if (!box) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") step(1);
      else if (e.key === "ArrowLeft") step(-1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [box, close, step]);

  const many = (box?.items.length ?? 0) > 1;
  const taggable = !!(box?.opts?.onTag || box?.opts?.onDelete);
  return (
    <LightboxCtx.Provider value={open}>
      {children}
      {box && (
        <div
          onClick={close}
          className="fixed inset-0 z-[60] bg-black/90 backdrop-blur-sm flex flex-col items-center justify-center gap-4 p-4"
        >
          <img
            src={box.items[box.i].src}
            onClick={(e) => e.stopPropagation()}
            className="max-w-full min-h-0 flex-1 object-contain rounded-lg shadow-2xl"
            alt="snapshot"
          />
          <button onClick={close} className="btn btn-sm absolute top-4 right-4 bg-black/60" title="close">✕</button>
          {many && (
            <>
              <button onClick={(e) => { e.stopPropagation(); step(-1); }} className="btn absolute left-4 top-1/2 -translate-y-1/2 bg-black/60 !px-3 text-xl" title="previous">‹</button>
              <button onClick={(e) => { e.stopPropagation(); step(1); }} className="btn absolute right-4 top-1/2 -translate-y-1/2 bg-black/60 !px-3 text-xl" title="next">›</button>
            </>
          )}
          {taggable && (
            <div
              onClick={(e) => e.stopPropagation()}
              className="shrink-0 flex flex-wrap items-center justify-center gap-2 bg-black/70 rounded-xl px-3 py-2"
            >
              <span className="text-xs text-white/70 mr-1">Tag as</span>
              {box.opts?.tags?.map((t) => (
                <button key={t.to} disabled={busy} onClick={() => act((id) => box.opts!.onTag?.(id, t.to))} className="btn btn-sm">
                  {t.label}
                </button>
              ))}
              {box.opts?.onDelete && (
                <button disabled={busy} onClick={() => act((id) => box.opts!.onDelete?.(id))} className="btn btn-sm btn-danger" title="delete">
                  ✕ Discard
                </button>
              )}
            </div>
          )}
          <div className="shrink-0 text-xs text-white/80 bg-black/60 px-3 py-1 rounded-full">
            {box.i + 1} / {box.items.length}
          </div>
        </div>
      )}
    </LightboxCtx.Provider>
  );
}

// ---- Primitives ---------------------------------------------------------
export function Card({ title, children, className = "", right }: {
  title?: string; children: ReactNode; className?: string; right?: ReactNode;
}) {
  return (
    <div className={`card ${className}`}>
      {title && (
        <div className="flex items-center justify-between">
          <div className="h2">{title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

const TONES: Record<string, string> = {
  teal: "bg-teal/15 text-teal border-teal/40",
  sage: "bg-sage/15 text-sage border-sage/40",
  pearl: "bg-pearl/15 text-pearl border-pearl/40",
  bad: "bg-bad/15 text-bad border-bad/40",
  muted: "bg-surface2 text-muted border-edge",
};

export function Badge({ tone = "muted", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={`text-xs font-bold px-2.5 py-1 rounded-full border ${TONES[tone]}`}>{children}</span>;
}

export function Button(
  props: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "danger" | "ghost"; sm?: boolean },
) {
  const { variant = "ghost", sm, className = "", ...rest } = props;
  const v = variant === "primary" ? "btn-primary" : variant === "danger" ? "btn-danger" : "";
  return <button {...rest} className={`btn ${v} ${sm ? "btn-sm" : ""} ${className}`} />;
}

const TILE_EDGE = ["border-l-teal", "border-l-sage", "border-l-pearl", "border-l-beige", "border-l-ash"];
export function Tile({ label, value, i = 0 }: { label: string; value: ReactNode; i?: number }) {
  return (
    <div className={`tile ${TILE_EDGE[i % TILE_EDGE.length]}`}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className="text-[17px] font-bold mt-0.5 break-words text-beige">{value}</div>
    </div>
  );
}

// ---- Theme switcher -----------------------------------------------------
export function ThemeToggle({ custom = [] }: { custom?: ThemeDef[] }) {
  const [active, setActive] = useState<string>(activeThemeId);
  // Re-apply a saved custom theme once its definition arrives (and after edits).
  useEffect(() => {
    const def = custom.find((t) => t.id === active);
    if (def) applyTheme(def.id, def);
  }, [custom, active]);

  const pick = (id: string, def?: ThemeDef) => { applyTheme(id, def); setActive(id); };
  const swatch = (c?: Record<string, string>) =>
    c ? `linear-gradient(135deg,${c.title1 || c.teal || "#888"},${c.title2 || c.beige || "#ccc"})` : "#888";

  return (
    <div className="flex gap-1.5 flex-wrap">
      {BUILTINS.map((t) => (
        <button
          key={t.id}
          title={t.name}
          onClick={() => pick(t.id)}
          style={{ background: t.swatch }}
          className={`w-6 h-6 rounded-full border-2 transition ${active === t.id ? "border-ink scale-110" : "border-edge"}`}
        />
      ))}
      {custom.map((t) => (
        <button
          key={t.id}
          title={t.name}
          onClick={() => pick(t.id, t)}
          style={{ background: swatch(t.colors) }}
          className={`w-6 h-6 rounded-full border-2 transition ${active === t.id ? "border-ink scale-110" : "border-edge"}`}
        />
      ))}
    </div>
  );
}
