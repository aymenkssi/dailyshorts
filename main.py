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
    "long": (editing.LONG, (1920, 1088)),
    "short": (editing.SHORT, (1088, 1920)),
}


def on(var):
    return os.environ.get(var, "false").lower() in ("1", "true", "yes")


def plan_images(scripts, out):
    """Portraits de référence des personnages, puis taille des images la plus nette que permet le quota
    gratuit du jour. Le plan est gardé dans plan.json pour qu'une relance produise les mêmes formats."""
    plan_file = out / "plan.json"
    recap = scripts["long"].get("kind") == "recap"
    refs = {}
    if on("CHARACTER_REFS") and not recap and scripts["long"].get("characters"):
        print("Portraits de référence des personnages…")
        refs = media_gen.generate_character_refs(scripts["long"], out / "refs")
    if plan_file.exists():
        plan = json.loads(plan_file.read_text())
    else:
        counts = {k: sum(1 for i in range(len(scripts[k]["scenes"]))
                         if not (out / k / "images" / f"scene_{i:03d}.jpg").exists()) for k in ("long", "short")}
        n_refs = sum(1 for k in ("long", "short") for sc in scripts[k]["scenes"]
                     if any(n in refs for n in script_writer.characters_in(sc, scripts[k])))
        sizes, cost = media_gen.plan_sizes(counts, refs=n_refs,
                                           extra=media_gen.neurons((1024, 1024), len(refs)))
        plan = {"sizes": {k: list(v) for k, v in sizes.items()}, "neurons": round(cost or 0)}
        plan_file.write_text(json.dumps(plan, indent=1))
    print(f"Images : vidéo longue {plan['sizes']['long'][0]}×{plan['sizes']['long'][1]}, "
          f"Short {plan['sizes']['short'][0]}×{plan['sizes']['short'][1]}"
          + (f", ~{plan['neurons']} neurons" if plan.get("neurons") else "")
          + (f", personnages de référence : {', '.join(refs)}" if refs else ""))
    return {k: tuple(v) for k, v in plan["sizes"].items()}, refs


def produce(kind, script, out, size=None, refs=None):
    """Voix, images et montage d'une vidéo ; renvoie le chemin du fichier final."""
    layout, default_size = FORMATS[kind]
    size = size or default_size
    refs = refs or {}
    out.mkdir(parents=True, exist_ok=True)
    final = out / "final.mp4"
    if final.exists():
        return final
    scenes = script["scenes"]
    print(f"[{kind}] {script['title']} — {len(scenes)} plans")
    print(f"[{kind}] voix off…")
    narration = " ".join(s["narration"].strip() for s in scenes)
    news_voice = os.environ.get("NEWS_VOICE", "") if script.get("kind") == "recap" else ""
    voice, words = media_gen.synthesize_voice(narration, out, voice=news_voice or None)
    print(f"[{kind}] images…")
    recap = script.get("kind") == "recap"
    prompts, plain, scene_refs = [], [], []
    for s in scenes:
        names = [n for n in script_writer.characters_in(s, script) if n in refs][:4]
        idx = {n: k for k, n in enumerate(names)}
        prompts.append(script_writer.full_image_prompt(s, kind, recap=recap, script=script, refs=idx))
        plain.append(script_writer.full_image_prompt(s, kind, recap=recap, script=script))
        scene_refs.append([refs[n] for n in names])
    # secours pour le Short si le quota d'images est épuisé : les images de la vidéo longue
    spare = sorted((out.parent / "long" / "images").glob("scene_*.jpg")) if kind == "short" and not recap else ()
    images = media_gen.generate_images(prompts, out / "images", size, spare=spare, refs=scene_refs, plain=plain)
    print(f"[{kind}] montage…")
    final = editing.assemble(scenes, images, {}, voice, words, out,
                             music_dir=ROOT / "assets" / "music",
                             music_volume=float(os.environ.get("MUSIC_VOLUME", 0.1)),
                             layout=layout, particles=on("PARTICLES") and not recap,
                             parallax=on("PARALLAX"))
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


def news_short(day_iso):
    """Lundi à samedi : le Short du jour est l'actualité des deux jours précédents (None si la source manque)."""
    day = dt.date.fromisoformat(day_iso)
    try:
        print("Short d'actualité : lecture des deux derniers jours (Wikipédia, Current events)…")
        digest, covered = news.week_digest(day, days=2)
        if not covered:
            print("  aucune actualité récupérée : Short d'accroche à la place")
            return None
        sec = int(os.environ.get("NEWS_SHORT_SECONDS", 90))
        short = script_writer.fact_check_recap(
            script_writer.write_news_short(digest, covered[0], covered[-1], seconds=sec), digest)
        short["description"] = short.get("description", "").rstrip() + "\n\n" + news.SOURCE_CREDIT
        short["category_id"] = "25"
        short["kind"] = "recap"
        return short
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        print(f"  Short d'actualité impossible ({e}) : Short d'accroche à la place")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--platforms", default="", help="ne publier que sur ces plateformes (youtube,instagram,tiktok,facebook)")
    ap.add_argument("--force", action="store_true",
                    help="avec --platforms : renvoyer même si c'est déjà publié ou si la plateforme est coupée")
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
            teaser = news_short(args.date) if on("DAILY_NEWS_SHORT") else None
            if teaser is None:
                teaser = script_writer.write_teaser(long_script, seconds=int(os.environ.get("SHORT_SECONDS", 40)))
            scripts = {"long": long_script, "short": teaser}
        if on("SUBSCRIBE_CTA"):
            script_writer.add_subscribe_cta(scripts)
        script_file.write_text(json.dumps(scripts, indent=2, ensure_ascii=False))
        if imposed:
            store.pop_topic()  # retiré de la file seulement une fois le script écrit
    print(f"Sujet : {scripts['long']['topic']}")
    if scripts["long"].get("kind") == "recap":
        print("Format : récapitulatif de l'actualité de la semaine")

    # 2. Production des deux vidéos ; l'échec de l'une n'empêche pas l'autre
    finals, missing = {}, []
    try:
        sizes, refs = plan_images(scripts, out)
    except Exception:  # noqa: BLE001  -> formats par défaut, sans référence
        traceback.print_exc()
        sizes, refs = {k: v[1] for k, v in FORMATS.items()}, {}
    for kind in ("long", "short"):
        try:
            finals[kind] = produce(kind, scripts[kind], out / kind, size=sizes[kind], refs=refs)
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
        label = "Today's story" if (scripts["short"].get("kind") == "recap" and scripts["long"].get("kind") != "recap") else "Full story"
        return f"▶ {label}: {r[3:]}" if r.startswith("OK ") else ""

    def fb_link():
        link = platforms.facebook_link(results.get("facebook_long", ""))
        label = "Today's story" if (scripts["short"].get("kind") == "recap" and scripts["long"].get("kind") != "recap") else "Full story"
        return f"▶ {label}: {link}" if link and str(results["facebook_long"]).startswith("OK") else ""

    def go(platform, kind):
        """Plateforme activée ET cette vidéo (longue ou Short) autorisée pour elle dans l'admin."""
        return on(f"PUBLISH_{platform.upper()}") and on(f"SEND_{platform.upper()}_{kind.upper()}")

    targets = [  # (nom, activé, vidéo nécessaire, action)
        ("youtube_long", go("youtube", "long"), "long",
         lambda: platforms.publish_youtube(finals["long"], scripts["long"], short=False)),
        ("youtube_short", go("youtube", "short"), "short",
         lambda: platforms.publish_youtube(finals["short"], scripts["short"], short=True, extra=long_link())),
        ("instagram", go("instagram", "short"), "short",
         lambda: platforms.publish_instagram(finals["short"], scripts["short"], args.date)),
        ("tiktok", go("tiktok", "short"), "short",
         lambda: platforms.publish_tiktok(finals["short"], scripts["short"])),
        # Facebook : la vidéo longue sur la Page, puis le Short en Reel avec le lien vers la longue
        ("facebook_long", go("facebook", "long"), "long",
         lambda: platforms.publish_facebook_video(finals["long"], scripts["long"], args.date)),
        ("facebook", go("facebook", "short"), "short",
         lambda: platforms.publish_facebook_reel(finals["short"], scripts["short"], extra=fb_link())),
        ("instagram_long", go("instagram", "long"), "long",
         lambda: platforms.publish_instagram(finals["long"], scripts["long"], args.date, suffix="-long")),
        ("tiktok_long", go("tiktok", "long"), "long",
         lambda: platforms.publish_tiktok(finals["long"], scripts["long"])),
    ]
    failed = bool(missing)  # une vidéo manquante = journée incomplète, on publie quand même ce qui existe
    for name, enabled, kind, fn in targets:
        if only and name.split("_")[0] not in only:
            continue
        if only and args.force:
            if kind != "short":
                continue  # renvoi manuel : uniquement le Short (jamais la vidéo longue en double)
            enabled = True  # même si la plateforme ou le Short y sont coupés dans les réglages
        elif str(results.get(name, "")).startswith("OK"):
            continue
        if not enabled or kind not in finals:
            continue
        try:
            results[name] = "OK " + fn()
        except Exception as e:  # noqa: BLE001
            failed = True
            results[name] = f"ERREUR {e}"
            traceback.print_exc()
        print(f"{name} : {results[name]}")
        results_file.write_text(json.dumps(results, indent=1))
    if only:
        # publication partielle (heure propre à une plateforme) : le planificateur décide de la suite
        sys.exit(1 if failed else 0)
    if not failed:
        # toutes les publications de la journée ont réussi (y compris après une relance manuelle)
        store.set_flag(args.date, "failed", False)
        store.set_flag(args.date, "done")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
