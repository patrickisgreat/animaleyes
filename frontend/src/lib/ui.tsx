import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

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
// Click any snapshot to view it full-size, with ← → between a set and Esc to close.
type LightboxState = { srcs: string[]; i: number } | null;
const LightboxCtx = createContext<(srcs: string[], i?: number) => void>(() => {});
export const useLightbox = () => useContext(LightboxCtx);

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [box, setBox] = useState<LightboxState>(null);
  const open = useCallback((srcs: string[], i = 0) => srcs.length && setBox({ srcs, i }), []);
  const close = useCallback(() => setBox(null), []);
  const step = useCallback(
    (d: number) => setBox((b) => (b ? { ...b, i: (b.i + d + b.srcs.length) % b.srcs.length } : b)),
    [],
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

  const many = (box?.srcs.length ?? 0) > 1;
  return (
    <LightboxCtx.Provider value={open}>
      {children}
      {box && (
        <div
          onClick={close}
          className="fixed inset-0 z-[60] bg-black/90 backdrop-blur-sm grid place-items-center p-4"
        >
          <img
            src={box.srcs[box.i]}
            onClick={(e) => e.stopPropagation()}
            className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
            alt="snapshot"
          />
          <button onClick={close} className="btn btn-sm absolute top-4 right-4 bg-black/60" title="close">✕</button>
          {many && (
            <>
              <button onClick={(e) => { e.stopPropagation(); step(-1); }} className="btn absolute left-4 top-1/2 -translate-y-1/2 bg-black/60 !px-3 text-xl" title="previous">‹</button>
              <button onClick={(e) => { e.stopPropagation(); step(1); }} className="btn absolute right-4 top-1/2 -translate-y-1/2 bg-black/60 !px-3 text-xl" title="next">›</button>
              <div className="absolute bottom-4 left-1/2 -translate-x-1/2 text-xs text-white/80 bg-black/60 px-3 py-1 rounded-full">
                {box.i + 1} / {box.srcs.length}
              </div>
            </>
          )}
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
const THEMES: { id: string; css: string }[] = [
  { id: "sage", css: "linear-gradient(135deg,#93c0a4,#dce2bd)" },
  { id: "aurora", css: "linear-gradient(135deg,#7400b8,#80ffdb)" },
];
export function ThemeToggle() {
  const [theme, setTheme] = useState<string>(
    () => document.documentElement.dataset.theme || "sage",
  );
  const pick = (t: string) => {
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("theme", t); } catch { /* ignore */ }
    setTheme(t);
  };
  return (
    <div className="flex gap-1.5">
      {THEMES.map((t) => (
        <button
          key={t.id}
          title={t.id}
          onClick={() => pick(t.id)}
          style={{ background: t.css }}
          className={`w-6 h-6 rounded-full border-2 transition ${theme === t.id ? "border-ink scale-110" : "border-edge"}`}
        />
      ))}
    </div>
  );
}
