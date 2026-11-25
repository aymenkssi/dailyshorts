"""Infographie du jour : crée l'image (données du Census Bureau) puis la publie sur les plateformes choisies.

  python post_image.py --date 2026-11-26            # crée si besoin, publie ce qui n'est pas encore publié
  python post_image.py --date 2026-11-26 --regen    # refait l'image
  python post_image.py --date 2026-11-26 --no-publish
Résultats : output/infographic/<date>/ (infographic.jpg, meta.json, published.json).
"""
import argparse
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from publish import platforms  # noqa: E402
from shorts import infographic, store  # noqa: E402

store.apply_env()
OUT = ROOT / "output" / "infographic"


def on(var):
    return os.environ.get(var, "false").lower() in ("1", "true", "yes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--regen", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--force", action="store_true", help="republier même si déjà publié")
    args = ap.parse_args()
    if not store.valid_day(args.date):
        sys.exit("date invalide")
    d = OUT / args.date
    d.mkdir(parents=True, exist_ok=True)
    if args.regen or not (d / "infographic.jpg").exists():
        meta = infographic.generate(args.date, d)
        print(f"infographie : {meta['dataset']} ({meta['style']}, ACS {meta['year']})")
    meta = json.loads((d / "meta.json").read_text())
    if args.no_publish:
        return
    jpg = d / "infographic.jpg"
    rf = d / "published.json"
    results = json.loads(rf.read_text()) if rf.exists() else {}
    targets = [
        ("instagram", lambda: platforms.publish_instagram_image(jpg, meta, args.date)),
        ("facebook", lambda: platforms.publish_facebook_photo(jpg, meta)),
        ("tiktok", lambda: platforms.publish_tiktok_photo(jpg, meta, args.date)),
    ]
    failed = False
    for name, fn in targets:
        if not (on(f"PUBLISH_{name.upper()}") and on(f"SEND_{name.upper()}_IMAGE")):
            continue
        if str(results.get(name, "")).startswith("OK") and not args.force:
            continue
        try:
            results[name] = "OK " + fn()
        except Exception as e:  # noqa: BLE001
            failed = True
            results[name] = f"ERREUR {e}"
            traceback.print_exc()
        print(f"image {name} : {results[name]}")
        rf.write_text(json.dumps(results, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
