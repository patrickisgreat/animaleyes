import { useState } from "react";
import { api, usePoll } from "../lib/api";
import type { EventItem } from "../lib/api";
import { Card, useLightbox } from "../lib/ui";
import { EVENTS } from "../lib/labels";

export function Activity() {
  const [all, setAll] = useState(false);
  const [evs] = usePoll<EventItem[]>(() => api.events(all), 15000);
  const lightbox = useLightbox();

  return (
    <Card
      title="Activity"
      right={
        <label className="flex items-center gap-1.5 text-muted text-xs font-semibold cursor-pointer">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> show all
        </label>
      }
    >
      <div className="flex flex-col max-h-[460px] overflow-y-auto -mx-1 px-1">
        {!evs?.length && <div className="text-muted text-xs">No activity yet.</div>}
        {evs?.map((e) => {
          const [ico, label] = EVENTS[e.kind] || ["•", e.kind];
          const when = e.at.replace("T", " ").slice(5, 16);
          return (
            <div key={e.id} className="flex gap-3 py-2.5 border-b border-edge last:border-0 items-start">
              <div className="w-[26px] h-[26px] rounded-[7px] shrink-0 grid place-items-center bg-surface2 text-sm">
                {ico}
              </div>
              <div className="min-w-0 flex-1">
                <div className="font-bold text-sm">
                  <a className="text-teal no-underline hover:underline" href={`/events/${e.id}`}>{label}</a>
                </div>
                <div className="text-muted text-xs mt-0.5 break-words">
                  {when}
                  {e.reason ? " · " + e.reason : ""}
                </div>
                {e.frames.length > 0 && (
                  <div className="flex gap-1.5 mt-2 flex-wrap">
                    {e.frames.map((f, i) => (
                      <img
                        key={f}
                        loading="lazy"
                        src={`/frames/${encodeURIComponent(f)}`}
                        onClick={() => lightbox(e.frames.map((n) => `/frames/${encodeURIComponent(n)}`), i)}
                        className="w-16 h-16 object-cover rounded-md border border-edge cursor-zoom-in bg-black"
                        title="click to enlarge"
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
