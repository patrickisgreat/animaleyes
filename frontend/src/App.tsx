import { api, usePoll } from "./lib/api";
import type { Status } from "./lib/api";
import { Badge, ThemeToggle } from "./lib/ui";
import { LiveView, StateBanner } from "./panels/LiveState";
import { Feeder, Plates } from "./panels/Feeder";
import { StatusTiles } from "./panels/Status";
import { Activity } from "./panels/Activity";
import { ReferencePhotos, TrainingGallery } from "./panels/Photos";
import { Descriptions } from "./panels/Descriptions";
import { Settings } from "./panels/Settings";

export function App() {
  const [s, refresh] = usePoll<Status>(() => api.status(), 1500);

  return (
    <div className="max-w-[1080px] mx-auto p-4">
      <header className="flex items-center gap-3 flex-wrap mb-4">
        <h1 className="text-[19px] font-black tracking-tight">
          <span>🐾</span> <span className="brand">animaleyes</span>
        </h1>
        <span className="flex-1" />
        {s && <Badge tone={s.dry_run ? "pearl" : "teal"}>{s.dry_run ? "Dry run" : "Live"}</Badge>}
        {s && <Badge tone={s.camera_offline ? "bad" : "teal"}>{s.camera_offline ? "Camera offline" : "Camera ok"}</Badge>}
        <ThemeToggle />
      </header>

      <div className="grid gap-4 md:[grid-template-columns:1.25fr_1fr] md:items-start">
        <div className="grid gap-4 min-w-0">
          <StateBanner s={s} />
          <LiveView s={s} />
          <Feeder s={s} refresh={refresh} />
          <Plates s={s} refresh={refresh} />
        </div>
        <div className="grid gap-4 min-w-0">
          <StatusTiles s={s} />
          <Activity />
        </div>
      </div>

      <div className="grid gap-4 mt-4">
        <ReferencePhotos counts={s?.reference_counts || {}} refresh={refresh} />
        <Descriptions />
        <TrainingGallery counts={s?.training_counts || {}} />
        <Settings />
      </div>
    </div>
  );
}
