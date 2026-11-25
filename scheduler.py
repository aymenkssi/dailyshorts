"""Planificateur du conteneur, piloté par les réglages de l'interface d'administration.

- generate_at : création des vidéos du jour, sans publier
- publish_at  : publication des vidéos du jour (si la validation est exigée : seulement une fois approuvées)
- publish_at_<plateforme> : heure de la vidéo longue sur cette plateforme (vide = publish_at)
- publish_at_<plateforme>_short : heure du Short (vide = celle de la vidéo longue)

Il relit les réglages chaque minute, rattrape une étape manquée au démarrage, réessaie en cas
d'échec (main.py reprend là où il s'était arrêté), obéit aux actions de l'admin (approuver,
refuser, refaire, publier maintenant) et fait le ménage des anciens fichiers.
Tout ce qu'il affiche est aussi écrit dans output/scheduler.log, lu par l'admin.
"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from shorts import store  # noqa: E402

TZ = ZoneInfo(os.environ.get("RUN_TZ", "Europe/Paris"))
RETRIES = int(os.environ.get("RUN_RETRIES", 2))
RETRY_WAIT = int(os.environ.get("RUN_RETRY_MINUTES", 30)) * 60
KEEP_OUTPUT_DAYS = int(os.environ.get("KEEP_OUTPUT_DAYS", 30))
KEEP_PUBLIC_DAYS = int(os.environ.get("KEEP_PUBLIC_DAYS", 7))
LOG_FILE = ROOT / "output" / "scheduler.log"
_last_note = None


def emit(line):
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def log(msg):
    emit(f"[{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {msg}")


def note(msg):
    """Message d'attente : affiché seulement quand il change, pour ne pas remplir le journal."""
    global _last_note
    if msg != _last_note:
        _last_note = msg
        log(msg)


def trim_log(keep=3000):
    try:
        lines = LOG_FILE.read_text().splitlines()
        if len(lines) > keep:
            LOG_FILE.write_text("\n".join(lines[-keep:]) + "\n")
    except OSError:
        pass


def cleanup(now):
    out = ROOT / "output"
    for d in out.iterdir() if out.exists() else []:
        if d.is_dir() and now.timestamp() - d.stat().st_mtime > KEEP_OUTPUT_DAYS * 86400:
            shutil.rmtree(d, ignore_errors=True)
            log(f"ménage : {d.name} supprimé")
    for d in (out / "infographic").iterdir() if (out / "infographic").exists() else []:
        if d.is_dir() and now.timestamp() - d.stat().st_mtime > KEEP_OUTPUT_DAYS * 86400:
            shutil.rmtree(d, ignore_errors=True)
    pub = Path(os.environ.get("PUBLIC_VIDEO_DIR", "/public"))
    for f in pub.glob("*.mp4") if pub.exists() else []:
        if now.timestamp() - f.stat().st_mtime > KEEP_PUBLIC_DAYS * 86400:
            f.unlink(missing_ok=True)
    trim_log()


def run_step(day, label, extra_args, script="main.py", retries=None):
    """Lance main.py pour ce jour, avec nouveaux essais. Renvoie True si tout a réussi."""
    track = script == "main.py"  # l'infographie ne touche pas à l'état de la journée vidéo
    if track:
        store.set_flag(day, "running")
    try:
        retries = RETRIES if retries is None else retries
        for attempt in range(1, retries + 2):
            log(f"{label} du {day} (essai {attempt})")
            proc = subprocess.Popen([sys.executable, str(ROOT / script), "--date", day, *extra_args],
                                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                emit(line.rstrip("\n"))
            code = proc.wait()
            while code == 3:  # une commande manuelle traite déjà cette journée : on attend, sans compter d'essai
                time.sleep(120)
                code = subprocess.call([sys.executable, str(ROOT / script), "--date", day, *extra_args], cwd=ROOT)
            if code == 0:
                log(f"{label} du {day} : terminé")
                return True
            log(f"{label} du {day} : échec (code {code})")
            if attempt <= retries and (not track or store.day_dir(day).exists()):
                log(f"nouvel essai dans {RETRY_WAIT // 60} min")
                time.sleep(RETRY_WAIT)
        return False
    finally:
        if track and store.day_dir(day).exists():
            store.set_flag(day, "running", False)


def writable():
    """Vérifie que le dossier output/ est accessible en écriture (droits du volume monté)."""
    try:
        out = ROOT / "output"
        out.mkdir(exist_ok=True)
        probe = out / ".write_test"
        probe.touch()
        probe.unlink()
        return True
    except OSError as e:
        print(f"output/ n'est pas accessible en écriture ({e}). "
              "Sur le VPS : chown -R 1000:1000 state output public", flush=True)
        return False


def at(day_date, hhmm):
    return dt.datetime.combine(day_date, dt.time.fromisoformat(hhmm), TZ)


def result_key(platform, kind):
    """Clé du résultat dans published.json."""
    if kind == "long":
        return f"{platform}_long"
    return "youtube_short" if platform == "youtube" else platform


def platform_times(s, d):
    """Heure de chaque envoi (plateforme, long|short) activé.
    Longue : publish_at_<p> (sinon l'heure générale). Short : publish_at_<p>_short (sinon l'heure de la longue)."""
    out = {}
    for p in store.PLATFORMS:
        if not s[f"publish_{p}"]:
            continue
        long_t = s[f"publish_at_{p}"] or s["publish_at"]
        for kind in ("long", "short"):
            if s[f"send_{p}_{kind}"]:
                out[(p, kind)] = at(d, long_t if kind == "long" else (s[f"publish_at_{p}_short"] or long_t))
    return out


def settled(day, slot, ok_only=False):
    """Un envoi (plateforme, long|short) est réglé quand un résultat est enregistré (ok_only : réussi seulement)."""
    v = store._read(store.day_dir(day) / "published.json", {}).get(result_key(*slot))
    return bool(v) and (not ok_only or str(v).startswith("OK"))


def infographic_step(s, now, day):
    """Infographie quotidienne : indépendante des vidéos (dossier output/infographic/<jour>/).
    Un seul essai automatique ; en cas d'échec, le bouton de l'admin (fichier .now) relance."""
    d = ROOT / "output" / "infographic" / day
    manual = (d / ".now").exists()
    if not manual and not (s["daily_infographic"] and now >= at(now.date(), s["infographic_at"])
                           and not (d / ".done").exists() and not (d / ".failed").exists()):
        return False
    d.mkdir(parents=True, exist_ok=True)
    ok = run_step(day, "infographie", [], script="post_image.py", retries=0)
    (d / ".now").unlink(missing_ok=True)
    (d / ".failed").unlink(missing_ok=True)
    if ok:
        (d / ".done").touch()
    else:
        (d / ".failed").touch()
        log(f"infographie du {day} en échec : bouton « Infographie » de l'admin pour relancer")
    return True


def tick():
    """Une décision du planificateur. Renvoie True si une étape a été exécutée."""
    s = store.get_settings()
    now = dt.datetime.now(TZ)
    day = now.date().isoformat()
    has = lambda name: store.has_flag(day, name)  # noqa: E731

    # renvoi manuel du Short sur une plateforme (boutons de l'admin), même pour une journée déjà publiée
    manual = [p for p in store.PLATFORMS if has(f"send_{p}")]
    if manual and store.day_dir(day).exists():
        run_step(day, "envoi du Short (" + ", ".join(manual) + ")", ["--platforms", ",".join(manual), "--force"])
        for p in manual:
            store.set_flag(day, f"send_{p}", False)
        return True

    if infographic_step(s, now, day):
        return True

    published = has("done") or has("failed")
    generated = published or has("generated") or has("gen_tried")
    gen_time, pub_time = at(now.date(), s["generate_at"]), at(now.date(), s["publish_at"])

    if now >= gen_time and not generated and not has("rejected"):
        cleanup(now)
        ok = run_step(day, "création des vidéos", ["--no-publish"])
        if store.day_dir(day).exists():  # l'admin a pu demander de tout refaire entre-temps
            store.set_flag(day, "generated" if ok else "gen_tried")  # échec : la publication retentera
        return True

    if published or has("rejected"):
        note(f"journée {day} terminée ; prochaine création : "
             f"{at(now.date() + dt.timedelta(days=1), s['generate_at']):%Y-%m-%d %H:%M}")
        return False

    forced = has("publish_now")
    times = platform_times(s, now.date())
    if s["require_approval"] and not (has("approved") or forced):
        note(f"vidéos du {day} en attente de validation dans l'interface d'administration")
        return False
    if not times:  # aucune plateforme activée
        store.set_flag(day, "done")
        return True
    # « maintenant » reprend aussi les envois en échec ; l'heure prévue ne reprend que ceux pas encore tentés
    due = [k for k, t in times.items() if (forced and not settled(day, k, True)) or (now >= t and not settled(day, k))]
    if not due:
        pending = sorted(t for k, t in times.items() if not settled(day, k))
        note(f"prochaine étape : {(gen_time if now < gen_time else pending[0] if pending else pub_time):%Y-%m-%d %H:%M}")
        return False

    ok = True
    for kind in ("long", "short"):  # un passage par type de vidéo : leurs heures peuvent différer
        group = [k for k in due if k[1] == kind]
        if not group:
            continue
        plats = sorted({p for p, _ in group})
        ok = run_step(day, f"publication {'vidéo longue' if kind == 'long' else 'Short'} (" + ", ".join(plats) + ")",
                      ["--platforms", ",".join(plats), "--kind", kind]) and ok
        if not store.day_dir(day).exists():
            break
    if store.day_dir(day).exists():
        if not ok and not all(settled(day, k) for k in due):
            # la création ou le lancement a échoué avant tout résultat : on s'arrête là pour ne pas boucler
            log(f"abandon pour le {day} : relance depuis l'admin ou avec "
                f"`docker compose exec shorts python main.py --date {day}`")
            store.set_flag(day, "failed")
        elif all(settled(day, k) for k in times):
            res = store._read(store.day_dir(day) / "published.json", {})
            if all(str(v).startswith("OK") for v in res.values()):
                store.set_flag(day, "failed", False)
                store.set_flag(day, "done")
            else:
                log(f"journée {day} close avec des échecs : relance depuis l'admin ou "
                    f"`docker compose exec shorts python main.py --date {day}`")
                store.set_flag(day, "failed")
        store.set_flag(day, "publish_now", False)
    return True


def main():
    s = store.get_settings()
    log(f"planificateur démarré : création à {s['generate_at']}, publication à {s['publish_at']} ({TZ.key})"
        + (", validation exigée" if s["require_approval"] else ""))
    while True:
        if not writable():
            time.sleep(600)
            continue
        if not tick():
            time.sleep(int(os.environ.get("TICK_SECONDS", 60)))


if __name__ == "__main__":
    main()
