import { useEffect, useRef, useState } from "react";

export type Verdict = {
  animal: string;
  confidence: number;
  at_bowl: boolean;
  other_animals_present: string[];
  reason: string;
};

export type Status = {
  state: string;
  enabled: boolean;
  dry_run: boolean;
  active_window: string;
  in_window: boolean;
  plates: Record<string, string>;
  loaded_plates: number[];
  feeds_this_window: number;
  last_open_at: string | null;
  lid_open: boolean;
  current_plate: number | null;
  next_allowed_feed_in_s: number;
  heartbeat_age_s: number | null;
  camera_age_s: number | null;
  camera_offline: boolean;
  motion_fraction: number;
  motion_source: string;
  camera_motion: boolean | null;
  camera_animal: boolean | null;
  camera_events_ok: boolean | null;
  llm_calls_today: number;
  llm_cost_today_usd: number;
  llm_model: string;
  identifier: string;
  ptz_available: boolean;
  last_verdict: Verdict | null;
  last_llm_at: string | null;
  reference_counts: Record<string, number>;
  training_counts: Record<string, number>;
  capture_mode: boolean;
  feeding_since: string | null;
};

export type EventItem = {
  id: number;
  at: string;
  kind: string;
  reason: string;
  data: unknown;
  frames: string[];
};

export const ANIMALS = ["grrr", "bowie", "cat"] as const;
export type Animal = (typeof ANIMALS)[number];
export const NAMES: Record<string, string> = { grrr: "Grrr", bowie: "Bowie", cat: "the cat" };

async function j<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

export const api = {
  status: () => j<Status>("/api/status"),
  config: () => j<Record<string, unknown>>("/api/config"),
  events: (all: boolean) => j<EventItem[]>(`/api/events?all=${all}`),
  photos: (set: string, a: string) => j<{ total: number; files: string[] }>(`/api/photos/${set}/${a}`),
  post: (url: string, body?: unknown) =>
    fetch(url, body === undefined
      ? { method: "POST" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  del: (url: string) => fetch(url, { method: "DELETE" }),
  uploadPhoto: (a: string, name: string, buf: ArrayBuffer) =>
    fetch(`/api/reference/${a}?filename=${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: buf,
    }),
};

/** Poll a fetcher on an interval; returns the latest value (or null until first load). */
export function usePoll<T>(fetcher: () => Promise<T>, ms: number): [T | null, () => void] {
  const [val, setVal] = useState<T | null>(null);
  const f = useRef(fetcher);
  f.current = fetcher;
  const run = () => f.current().then(setVal).catch(() => {});
  useEffect(() => {
    run();
    const id = setInterval(run, ms);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms]);
  return [val, run];
}
