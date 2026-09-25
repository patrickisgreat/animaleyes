"""Run identification on saved frames and print the verdicts, to tune without the animals.

Usage: python tools/replay.py <frames dir or files...> [--window 3] [--feeding]
Frames are sorted by name and sent in sliding windows of --window, like the live loop.
Uses the real reference photos in data/reference and the real API; each call is logged to a
throwaway SQLite database and its cost printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animaleyes.camera import Frame  # noqa: E402
from animaleyes.config import ConfigStore  # noqa: E402
from animaleyes.store import Store  # noqa: E402
from animaleyes.vision import ClaudeIdentifier  # noqa: E402

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(p for p in sorted(path.iterdir()) if p.suffix.lower() in IMAGE_SUFFIXES)
        else:
            files.append(path)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--window", type=int, default=3, help="frames per LLM call")
    parser.add_argument(
        "--step", type=int, default=0, help="frames to advance per call (default: window)"
    )
    parser.add_argument("--feeding", action="store_true", help="ask the FEEDING question instead")
    parser.add_argument("--reference", type=Path, default=Path("data/reference"))
    args = parser.parse_args()

    load_dotenv()
    files = collect(args.paths)
    if not files:
        raise SystemExit("no frames found")
    step = args.step or args.window
    store = Store(Path(tempfile.mkdtemp()) / "replay.sqlite")
    settings = ConfigStore().load()
    llm = ClaudeIdentifier(args.reference, store, model=lambda: settings.LLM_MODEL)
    print(f"model={settings.LLM_MODEL} references={llm.reference_counts} frames={len(files)}")

    total = 0.0
    for start in range(0, len(files), step):
        chunk = files[start : start + args.window]
        frames = [Frame(jpeg=p.read_bytes(), at=datetime.now()) for p in chunk]
        verdict = llm.feeding_check(frames) if args.feeding else llm.identify(frames)
        _, cost = store.llm_totals_since(datetime(2000, 1, 1))
        print(f"{chunk[0].name} .. {chunk[-1].name}: {json.dumps(verdict.to_dict())}")
        total = cost
    calls, _ = store.llm_totals_since(datetime(2000, 1, 1))
    print(f"{calls} calls, estimated ${total:.4f}")


if __name__ == "__main__":
    main()
