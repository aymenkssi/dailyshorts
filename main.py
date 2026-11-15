"""Génère et publie la vidéo du jour.

Usage :
  python main.py                 # génère + publie (date du jour)
  python main.py --no-publish    # génère seulement (pour tester)
  python main.py --date 2026-11-02   # reprend/relance un jour donné (les étapes déjà faites sont sautées)
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

from shorts import editing, media_gen, script_writer  # noqa: E402
from publish import platforms  # noqa: E402

HISTORY = ROOT / "state" / "history.json"


def on(var):
    return os.environ.get(var, "false").lower() in ("1", "true", "yes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    out = ROOT / "output" / args.date
    out.mkdir(parents=True, exist_ok=True)
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []

    # 1. Script
    script_file = out / "script.json"
    if script_file.exists():
        script = json.loads(script_file.read_text())
    else:
        print("1/4 Script…")
        draft = script_writer.write_script(
            [h["topic"] for h in history],
            target_seconds=int(os.environ.get("TARGET_SECONDS", 120)),
        )
        script = script_writer.fact_check(draft)
        script_file.write_text(json.dumps(script, indent=2, ensure_ascii=False))
    scenes = script["scenes"]
    print(f"   Sujet : {script['topic']} — {len(scenes)} plans")

    # 2. Voix
    print("2/4 Voix off…")
    narration = " ".join(s["narration"].strip() for s in scenes)
    voice, words = media_gen.synthesize_voice(narration, out)

    # 3. Images
    print("3/4 Images…")
    images = media_gen.generate_images([script_writer.full_image_prompt(s) for s in scenes], out / "images")

    # 4. Montage (zoom/pan fluide sur chaque image)
    print("4/4 Montage…")
    final = editing.assemble(scenes, images, {}, voice, words, out,
                             music_dir=ROOT / "assets" / "music",
                             music_volume=float(os.environ.get("MUSIC_VOLUME", 0.1)))
    print(f"   Vidéo : {final} ({editing.media_duration(final):.0f} s)")

    if not any(h["date"] == args.date for h in history):
        history.append({"date": args.date, "topic": script["topic"], "title": script["title"]})
        HISTORY.write_text(json.dumps(history, indent=1, ensure_ascii=False))

    if args.no_publish:
        return

    # Publication (chaque plateforme indépendante ; déjà publié = sauté)
    results_file = out / "published.json"
    results = json.loads(results_file.read_text()) if results_file.exists() else {}
    targets = {
        "youtube": (on("PUBLISH_YOUTUBE"), lambda: platforms.publish_youtube(final, script)),
        "instagram": (on("PUBLISH_INSTAGRAM"), lambda: platforms.publish_instagram(final, script, args.date)),
        "tiktok": (on("PUBLISH_TIKTOK"), lambda: platforms.publish_tiktok(final, script)),
    }
    failed = False
    for name, (enabled, fn) in targets.items():
        if not enabled or str(results.get(name, "")).startswith("OK"):
            continue
        try:
            results[name] = "OK " + fn()
        except Exception as e:  # noqa: BLE001
            failed = True
            results[name] = f"ERREUR {e}"
            traceback.print_exc()
        print(f"   {name} : {results[name]}")
        results_file.write_text(json.dumps(results, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
