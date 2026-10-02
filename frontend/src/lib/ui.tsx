import { createContext, useCallback, useContext, useState } from "react";
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
