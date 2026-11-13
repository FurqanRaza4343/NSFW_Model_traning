"""Calibrate thresholds on YOUR labelled images (runs on the laptop, no Modal needed).

Folder layout:
    eval_data/safe/      images that should be approved
    eval_data/unsafe/    images that must NOT be approved (nudity, explicit, ...)

Usage:  python evaluate.py [eval_data]
It scores every image once, then searches threshold combinations and prints the ones with
ZERO false negatives (no unsafe image approved) and the fewest false positives.
Use at least 50-100 varied images per folder before trusting the result.
"""
import itertools
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\hf_cache")
os.environ.setdefault("LOCAL_CACHE_DIR", os.path.join(os.environ["HF_HOME"], "hub"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import config  # noqa: E402
from classifier import ImageError, NSFWClassifier  # noqa: E402

root = Path(sys.argv[1] if len(sys.argv) > 1 else "eval_data")
EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".gif"}


def load(label):
    return sorted(p for p in (root / label).glob("*") if p.suffix.lower() in EXTS)


safe, unsafe = load("safe"), load("unsafe")
if not safe or not unsafe:
    sys.exit(f"Need images in {root}/safe and {root}/unsafe (found {len(safe)} safe, {len(unsafe)} unsafe)")

clf = NSFWClassifier()
rows = []  # (label, name, explicit, suggestive)
for label, files in (("safe", safe), ("unsafe", unsafe)):
    for f in files:
        try:
            r = clf.predict(f.read_bytes())
            rows.append((label, f.name, r["nsfw_score"], r["suggestive_score"]))
            print(f"{label:6} {f.name[:40]:40} explicit={r['nsfw_score']:.3f} suggestive={r['suggestive_score']:.3f}")
        except ImageError as e:
            print(f"{label:6} {f.name[:40]:40} SKIPPED ({e})")


def evaluate(reject_t, review_t, sugg_t):
    fp = fn = 0
    for label, _, nsfw, sugg in rows:
        d = config.decide(nsfw, sugg, reject_t, review_t, sugg_t)
        if label == "safe" and d != "approve":
            fp += 1
        if label == "unsafe" and d == "approve":
            fn += 1
    return fp, fn


grid = [round(x * 0.05, 2) for x in range(1, 20)]
results = []
for review_t, sugg_t in itertools.product(grid, grid):
    for reject_t in grid:
        if reject_t < review_t:
            continue
        fp, fn = evaluate(reject_t, review_t, sugg_t)
        results.append((fn, fp, reject_t, review_t, sugg_t))
results.sort(key=lambda t: (t[0], t[1], t[2] + t[3] + t[4]))  # fewest FN, then FP, then the STRICTEST thresholds (safer)

n_safe = sum(1 for r in rows if r[0] == "safe")
n_unsafe = sum(1 for r in rows if r[0] == "unsafe")
print(f"\nImages scored: {n_safe} safe, {n_unsafe} unsafe")
if min(n_safe, n_unsafe) < 50:
    print("WARNING: fewer than 50 images in a folder - these numbers are NOT reliable yet.")
print(f"Current config: {evaluate(config.REJECT_THRESHOLD, config.REVIEW_THRESHOLD, config.SUGGESTIVE_THRESHOLD)} "
      f"(false positives, false negatives) with reject={config.REJECT_THRESHOLD} review={config.REVIEW_THRESHOLD} "
      f"suggestive={config.SUGGESTIVE_THRESHOLD}")
print("\nBest combinations (fewest false negatives first, then fewest false positives):")
for fn, fp, rj, rv, sg in results[:5]:
    print(f"  false_neg={fn} false_pos={fp}  REJECT={rj} REVIEW={rv} SUGGESTIVE={sg}")
