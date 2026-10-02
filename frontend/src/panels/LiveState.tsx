import { useEffect, useRef, useState } from "react";
import type { Status } from "../lib/api";
import { Card } from "../lib/ui";
import { STATES, verdictText } from "../lib/labels";

const DOT: Record<string, string> = {
  teal: "bg-teal", sage: "bg-sage", pearl: "bg-pearl", muted: "bg-muted", bad: "bg-bad",
};

export function StateBanner({ s }: { s: Status | null }) {
  const [label, sub, tone] = (s && STATES[s.state]) || ["…", "", "muted"];
  return (
    <div className="card bg-gradient-to-br from-teal/10 to-pearl/5">
      <div className="flex items-center gap-3">
        <span className={`w-3 h-3 rounded-full shrink-0 ${DOT[tone]} shadow-[0_0_0_4px_rgba(255,255,255,0.04)]`} />
        <div>
          <div className="text-[22px] font-extrabold leading-none">{label}</div>
          <div className="text-muted text-[13px] mt-1">{sub}</div>
        </div>
      </div>
    </div>
  );
}

export function LiveView({ s }: { s: Status | null }) {
  const img = useRef<HTMLImageElement>(null);
  const [live, setLive] = useState(true);

  const start = () => { if (img.current) img.current.src = "/stream.mjpg?t=" + Date.now(); };
  useEffect(() => {
    if (live) start();
    const onVis = () => { if (live && !document.hidden) start(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [live]);
  // while paused, refresh a still alongside status
  useEffect(() => {
    if (live || !img.current) return;
    img.current.src = "/frame.jpg?t=" + Date.now();
  }, [s, live]);

  const v = s ? verdictText(s) : { who: "Waiting…", why: "" };
  return (
    <Card>
      <div className="relative rounded-xl overflow-hidden bg-black aspect-video">
        <img
          ref={img}
          alt="live camera"
          className="w-full h-full object-cover block"
          onError={() => live && setTimeout(start, 3000)}
        />
        {live && (
          <span className="absolute top-2.5 left-2.5 text-xs font-bold text-teal bg-black/70 backdrop-blur px-2.5 py-1 rounded-full">
            ● LIVE
          </span>
        )}
        <button
          onClick={() => setLive((x) => !x)}
          className="btn btn-sm absolute top-2.5 right-2.5 bg-black/60 backdrop-blur"
        >
          {live ? "Pause" : "Resume"}
        </button>
        <div className="absolute left-2.5 right-2.5 bottom-2.5 bg-black/70 backdrop-blur px-3 py-2 rounded-[10px]">
          <div className="font-bold text-sm">{v.who}</div>
          {v.why && <div className="text-muted text-xs mt-0.5">{v.why}</div>}
        </div>
      </div>
    </Card>
  );
}
