"""Train the animal classifier from the tagged frames in data/training/<animal>/.

The COCO detector only knows "dog" and "cat"; this learns WHICH dog or cat from the frames the
human tagged. It classifies the detected animal's crop (not the whole frame), so it works whether
the animal is tiny in the distance or filling the picture. Run on the box inside the container:

    docker exec animaleyes python tools/train_classifier.py

Writes data/models/animals-cls.pt (+ .json with the held-out accuracy and class list). Nothing
reads the model until YOLO_CLASSIFIER points at it, so training never changes live behaviour.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from animaleyes.yolo import CAT, DOG, crop_box  # noqa: E402

IMG_EXT = (".jpg", ".jpeg", ".png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training-dir", default="data/training")
    ap.add_argument("--out", default="data/models")
    ap.add_argument("--detector", default="yolo11n.pt")
    ap.add_argument("--base", default="yolo11n-cls.pt")
    ap.add_argument("--min-images", type=int, default=20, help="skip classes with fewer frames")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=224)
    ap.add_argument("--val-fraction", type=float, default=0.2)
    args = ap.parse_args()

    from PIL import Image
    from ultralytics import YOLO

    training = Path(args.training_dir)
    out = Path(args.out)
    dataset = out / "dataset"
    if dataset.exists():
        shutil.rmtree(dataset)

    classes = sorted(
        d.name
        for d in training.iterdir()
        if d.is_dir()
        and d.name != "unlabeled"
        and sum(1 for p in d.iterdir() if p.suffix.lower() in IMG_EXT) >= args.min_images
    )
    skipped = sorted(
        d.name for d in training.iterdir() if d.is_dir() and d.name not in classes and d.name != "unlabeled"
    )
    if len(classes) < 2:
        raise SystemExit(f"need at least two classes with >= {args.min_images} frames; have {classes}")
    print(f"classes: {classes}  (skipped, too few frames: {skipped or 'none'})")

    # Crop the animal out of every tagged frame with the same detector + crop used at runtime.
    detector = YOLO(args.detector)
    counts: dict[str, Counter] = defaultdict(Counter)
    for cls in classes:
        frames = sorted(p for p in (training / cls).iterdir() if p.suffix.lower() in IMG_EXT)
        for p in frames:
            # Deterministic split by filename hash, so re-runs hold out the same frames.
            split = "val" if int(hashlib.md5(p.name.encode()).hexdigest(), 16) % 100 < args.val_fraction * 100 else "train"
            img = Image.open(p).convert("RGB")
            res = detector.predict(img, verbose=False, conf=0.25)[0]
            boxes = [
                (float(b.conf[0]), tuple(float(v) for v in b.xyxy[0]))
                for b in res.boxes
                if res.names[int(b.cls[0])] in (DOG, CAT)
            ]
            if not boxes:
                counts[cls]["no_animal_found"] += 1
                continue
            _, box = max(boxes)
            crop = crop_box(img, box)
            dest = dataset / split / cls
            dest.mkdir(parents=True, exist_ok=True)
            crop.save(dest / p.with_suffix(".jpg").name, quality=92)
            counts[cls][split] += 1
    for cls in classes:
        c = counts[cls]
        print(f"  {cls:10s} train {c['train']:4d}  val {c['val']:4d}  no animal found {c['no_animal_found']:3d}")

    model = YOLO(args.base)
    model.train(
        data=str(dataset),
        epochs=args.epochs,
        imgsz=args.imgsz,
        device="cpu",
        project=str(out),
        name="cls-run",
        exist_ok=True,
        verbose=False,
        plots=False,
        workers=2,
    )
    best = out / "cls-run" / "weights" / "best.pt"
    final = out / "animals-cls.pt"
    shutil.copy(best, final)

    # Held-out accuracy and confusion, per class, so the number is honest before anyone trusts it.
    clf = YOLO(str(final))
    names = clf.names
    confusion: dict[str, Counter] = defaultdict(Counter)
    for cls in classes:
        for p in sorted((dataset / "val" / cls).glob("*.jpg")):
            r = clf.predict(Image.open(p), verbose=False, imgsz=args.imgsz)[0]
            confusion[cls][names[int(r.probs.top1)]] += 1
    total = sum(sum(c.values()) for c in confusion.values())
    correct = sum(confusion[c][c] for c in classes)
    print(f"\nheld-out accuracy: {correct}/{total} = {correct / max(1, total):.1%}")
    print("confusion (rows = truth, cols = predicted):")
    print("  " + " " * 10 + "".join(f"{c:>10s}" for c in classes))
    for cls in classes:
        print(f"  {cls:10s}" + "".join(f"{confusion[cls][c]:>10d}" for c in classes))
    meta = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "classes": classes,
        "skipped": skipped,
        "counts": {c: dict(counts[c]) for c in classes},
        "held_out_accuracy": correct / max(1, total),
        "confusion": {c: dict(confusion[c]) for c in classes},
        "epochs": args.epochs,
        "imgsz": args.imgsz,
    }
    (out / "animals-cls.json").write_text(json.dumps(meta, indent=2))
    print(f"\nwrote {final} and {final.with_suffix('.json')}")


if __name__ == "__main__":
    main()
