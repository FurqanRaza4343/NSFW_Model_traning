"""Local test on the laptop (no Modal). Usage:  python local_test.py [folder_with_images]"""
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HOME", r"D:\hf_cache")
os.environ.setdefault("LOCAL_CACHE_DIR", os.path.join(os.environ["HF_HOME"], "hub"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # models are already on D:

from classifier import NSFWClassifier, ImageError  # noqa: E402

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "test_images")
exts = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".gif"}
files = sorted(p for p in folder.glob("*") if p.suffix.lower() in exts)
if not files:
    sys.exit(f"No images found in {folder.resolve()}")

clf = NSFWClassifier()
print(f"{'file':28} {'explicit':>8} {'suggest':>8}  {'decision':8} {'ms':>6}  reasons")
for f in files:
    try:
        r = clf.predict(f.read_bytes())
        reasons = ", ".join(r["reasons"])
        print(f"{f.name[:28]:28} {r['nsfw_score']:8.3f} {r['suggestive_score']:8.3f}  "
              f"{r['decision']:8} {r['latency_ms']:6.0f}  {reasons}")
    except ImageError as e:
        print(f"{f.name[:28]:28} ERROR: {e}")
