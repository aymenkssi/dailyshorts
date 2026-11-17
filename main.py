"""Génère et publie les vidéos du jour : une vidéo longue (horizontale) + un Short d'accroche (vertical).

Usage :
  python main.py                     # génère + publie (date du jour)
  python main.py --no-publish        # génère seulement (pour tester)
  python main.py --date 2026-11-02   # reprend/relance un jour donné (les étapes déjà faites sont sautées)
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from shorts import editing, media_gen, news, script_writer, store  # noqa: E402
from publish import platforms  # noqa: E402

store.apply_env()  # les réglages enregistrés depuis l'admin priment sur le .env

HISTORY = ROOT / "state" / "history.json"
FORMATS = {
    # nom : (mise en page, taille des images demandées)
    "long": (editing.LONG, (1344, 768)),
    "short": (editing.SHORT, (768, 1344)),
}


def on(var):
    return os.environ.get(var, "false").lower() in ("1", "true", "yes")


def produce(kind, script, out):
    """Voix, images et montage d'une vidéo ; renvoie le chemin du fichier final."""
    layout, size = FORMATS[kind]
    out.mkdir(parents=True, exist_ok=True)
    final = out / "final.mp4"
    if final.exists():
        return final
    scenes = script["scenes"]
    print(f"[{kind}] {script['title']} — {len(scenes)} plans")
    print(f"[{kind}] voix off…")
    narration = " ".join(s["narration"].strip() for s in scenes)
    voice, words = media_gen.synthesize_voice(narration, out)
    print(f"[{kind}] images…")
    recap = script.get("kind") == "recap"
    prompts = [script_writer.full_image_prompt(s, kind, recap=recap) for s in scenes]
    # secours pour le Short si le quota d'images est épuisé : les images de la vidéo longue
    spare = sorted((out.parent / "long" / "images").glob("scene_*.jpg")) if kind == "short" else ()
    images = media_gen.generate_images(prompts, out / "images", size, spare=spare)
    print(f"[{kind}] montage…")
    final = editing.assemble(scenes, images, {}, voice, words, out,
                             music_dir=ROOT / "assets" / "music",
                             music_volume=float(os.environ.get("MUSIC_VOLUME", 0.1)),
                             layout=layout)
    print(f"[{kind}] vidéo : {final} ({editing.media_duration(final):.0f} s)")
    return final


def recap_scripts(day_iso):
    """Le jour du récapitulatif : scripts de l'actualité de la semaine, ou None (vidéo d'histoire)."""
    day = dt.date.fromisoformat(day_iso)
    if not on("WEEKLY_RECAP") or day.weekday() != int(os.environ.get("RECAP_WEEKDAY", 6)):
        return None
    print("Récapitulatif de la semaine : lecture de l'actualité (Wikipédia, Current events)…")
    digest, covered = news.week_digest(day, days=int(os.environ.get("RECAP_DAYS", 6)))
    if len(covered) < 3:
        print(f"  seulement {len(covered)} jour(s) d'actualité récupéré(s) : vidéo d'histoire à la place")
        return None
    start, end = covered[0], covered[-1]
    recap = script_writer.fact_check_recap(script_writer.write_recap(
        digest, start, end, seconds=int(os.environ.get("RECAP_SECONDS", 360))), digest)
    recap["description"] = recap.get("description", "").rstrip() + "\n\n" + news.SOURCE_CREDIT
    recap["category_id"] = "25"  # YouTube : Actualités et politique
    teaser = script_writer.write_teaser(recap, seconds=int(os.environ.get("SHORT_SECONDS", 40)), recap=True)
    teaser["kind"] = "recap"
    teaser["description"] = teaser.get("description", "").rstrip() + "\n\n" + news.SOURCE_CREDIT
    teaser["category_id"] = "25"
    return {"long": recap, "short": teaser}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    out = ROOT / "output" / args.date
    out.mkdir(parents=True, exist_ok=True)
    # un seul passage à la fois pour une même journée (planificateur et commande manuelle)
    lock = open(out / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"Un autre passage est déjà en cours pour le {args.date} : attends qu'il se termine "
              "(suivi : docker compose logs -f shorts).")
        sys.exit(3)
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []

    # 1. Scripts : vidéo longue (écrite puis vérifiée), puis Short d'accroche tiré de la même histoire
    script_file = out / "scripts.json"
    if script_file.exists():
        scripts = json.loads(script_file.read_text())
    else:
        print("Scripts…")
        imposed = None
        scripts = recap_scripts(args.date)  # le jour du récapitulatif ; sinon None
        if scripts is None:
            queue = store.get_topics()  # sujets de la file de l'admin, sinon l'IA choisit
            imposed = queue[0]["topic"] if queue else None
            if imposed:
                print(f"Sujet imposé : {imposed}")
            long_script = script_writer.fact_check(script_writer.write_long(
                [h["topic"] for h in history], seconds=int(os.environ.get("LONG_SECONDS", 240)),
                topic=imposed, niche=os.environ.get("CHANNEL_NICHE", "")))
            teaser = script_writer.write_teaser(long_script, seconds=int(os.environ.get("SHORT_SECONDS", 40)))
            scripts = {"long": long_script, "short": teaser}
        script_file.write_text(json.dumps(scripts, indent=2, ensure_ascii=False))
        if imposed:
            store.pop_topic()  # retiré de la file seulement une fois le script écrit
    print(f"Sujet : {scripts['long']['topic']}")
    if scripts["long"].get("kind") == "recap":
        print("Format : récapitulatif de l'actualité de la semaine")

    # 2. Production des deux vidéos ; l'échec de l'une n'empêche pas l'autre
    finals, missing = {}, []
    for kind in ("long", "short"):
        try:
            finals[kind] = produce(kind, scripts[kind], out / kind)
        except Exception as e:  # noqa: BLE001
            missing.append(kind)
            print(f"[{kind}] ÉCHEC : {e}")
            traceback.print_exc()

    if "long" in finals and not any(h["date"] == args.date for h in history):
        history.append({"date": args.date, "topic": scripts["long"]["topic"], "title": scripts["long"]["title"]})
        HISTORY.write_text(json.dumps(history, indent=1, ensure_ascii=False))

    if args.no_publish:
        sys.exit(1 if missing else 0)  # code 1 : le planificateur réessaiera ce qui manque

    # 3. Publication (chaque cible indépendante ; déjà publié = sauté)
    results_file = out / "published.json"
    results = json.loads(results_file.read_text()) if results_file.exists() else {}

    def long_link():
        """Lien de la vidéo longue, ajouté à la description du Short une fois celle-ci publiée."""
        r = str(results.get("youtube_long", ""))
        return f"▶ Full story: {r[3:]}" if r.startswith("OK ") else ""

    def fb_link():
        link = platforms.facebook_link(results.get("facebook_long", ""))
        return f"▶ Full story: {link}" if link and str(results["facebook_long"]).startswith("OK") else ""

    yt = on("PUBLISH_YOUTUBE")
    targets = [  # (nom, activé, vidéo nécessaire, action)
        ("youtube_long", yt, "long",
         lambda: platforms.publish_youtube(finals["long"], scripts["long"], short=False)),
        ("youtube_short", yt, "short",
         lambda: platforms.publish_youtube(finals["short"], scripts["short"], short=True, extra=long_link())),
        ("instagram", on("PUBLISH_INSTAGRAM"), "short",
         lambda: platforms.publish_instagram(finals["short"], scripts["short"], args.date)),
        ("tiktok", on("PUBLISH_TIKTOK"), "short",
         lambda: platforms.publish_tiktok(finals["short"], scripts["short"])),
        # Facebook : la vidéo longue sur la Page, puis le Short en Reel avec le lien vers la longue
        ("facebook_long", on("PUBLISH_FACEBOOK"), "long",
         lambda: platforms.publish_facebook_video(finals["long"], scripts["long"], args.date)),
        ("facebook", on("PUBLISH_FACEBOOK"), "short",
         lambda: platforms.publish_facebook_reel(finals["short"], scripts["short"], extra=fb_link())),
        # option : la vidéo longue aussi sur Instagram et TikTok
        ("instagram_long", on("PUBLISH_INSTAGRAM") and on("LONG_TO_SOCIAL"), "long",
         lambda: platforms.publish_instagram(finals["long"], scripts["long"], args.date, suffix="-long")),
        ("tiktok_long", on("PUBLISH_TIKTOK") and on("LONG_TO_SOCIAL"), "long",
         lambda: platforms.publish_tiktok(finals["long"], scripts["long"])),
    ]
    failed = bool(missing)  # une vidéo manquante = journée incomplète, on publie quand même ce qui existe
    for name, enabled, kind, fn in targets:
        if not enabled or kind not in finals or str(results.get(name, "")).startswith("OK"):
            continue
        try:
            results[name] = "OK " + fn()
        except Exception as e:  # noqa: BLE001
            failed = True
            results[name] = f"ERREUR {e}"
            traceback.print_exc()
        print(f"{name} : {results[name]}")
        results_file.write_text(json.dumps(results, indent=1))
    if not failed:
        # toutes les publications de la journée ont réussi (y compris après une relance manuelle)
        store.set_flag(args.date, "failed", False)
        store.set_flag(args.date, "done")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
