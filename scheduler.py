"""Planificateur du conteneur, piloté par les réglages de l'interface d'administration.

- generate_at : création des vidéos du jour, sans publier
- publish_at  : publication des vidéos du jour (si la validation est exigée : seulement une fois approuvées)
- publish_at_<plateforme> : heure propre à YouTube, Instagram, TikTok ou Facebook (vide = publish_at)

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
    pub = Path(os.environ.get("PUBLIC_VIDEO_DIR", "/public"))
    for f in pub.glob("*.mp4") if pub.exists() else []:
        if now.timestamp() - f.stat().st_mtime > KEEP_PUBLIC_DAYS * 86400:
            f.unlink(missing_ok=True)
    trim_log()


def run_step(day, label, extra_args):
    """Lance main.py pour ce jour, avec nouveaux essais. Renvoie True si tout a réussi."""
    store.set_flag(day, "running")
    try:
        for attempt in range(1, RETRIES + 2):
            log(f"{label} du {day} (essai {attempt})")
            proc = subprocess.Popen([sys.executable, str(ROOT / "main.py"), "--date", day, *extra_args],
                                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                emit(line.rstrip("\n"))
            code = proc.wait()
            while code == 3:  # une commande manuelle traite déjà cette journée : on attend, sans compter d'essai
                time.sleep(120)
                code = subprocess.call([sys.executable, str(ROOT / "main.py"), "--date", day, *extra_args], cwd=ROOT)
            if code == 0:
                log(f"{label} du {day} : terminé")
                return True
            log(f"{label} du {day} : échec (code {code})")
            if attempt <= RETRIES and store.day_dir(day).exists():
                log(f"nouvel essai dans {RETRY_WAIT // 60} min")
                time.sleep(RETRY_WAIT)
        return False
    finally:
        if store.day_dir(day).exists():
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


def platform_times(s, d):
    """Heure de publication de chaque plateforme activée (l'heure générale si elle n'a pas la sienne)."""
    return {p: at(d, s[f"publish_at_{p}"] or s["publish_at"]) for p in store.PLATFORMS if s[f"publish_{p}"]}


def settled(day, platform, ok_only=False):
    """Une plateforme est réglée quand un résultat est enregistré pour elle (ok_only : réussi seulement)."""
    res = store._read(store.day_dir(day) / "published.json", {})
    return any(k.split("_")[0] == platform and v and (not ok_only or str(v).startswith("OK"))
               for k, v in res.items())


def tick():
    """Une décision du planificateur. Renvoie True si une étape a été exécutée."""
    s = store.get_settings()
    now = dt.datetime.now(TZ)
    day = now.date().isoformat()
    has = lambda name: store.has_flag(day, name)  # noqa: E731

    if has("send_tiktok") and store.day_dir(day).exists():
        # renvoi manuel du Short sur TikTok (bouton de l'admin), même pour une journée déjà publiée
        run_step(day, "envoi TikTok", ["--platforms", "tiktok", "--force"])
        store.set_flag(day, "send_tiktok", False)
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
    # « maintenant » reprend aussi les plateformes en échec ; l'heure prévue ne reprend que celles pas encore tentées
    due = [p for p, t in times.items() if (forced and not settled(day, p, True)) or (now >= t and not settled(day, p))]
    if not due:
        pending = sorted(t for p, t in times.items() if not settled(day, p))
        note(f"prochaine étape : {(gen_time if now < gen_time else pending[0] if pending else pub_time):%Y-%m-%d %H:%M}")
        return False

    ok = run_step(day, "publication (" + ", ".join(due) + ")", ["--platforms", ",".join(due)])
    if store.day_dir(day).exists():
        if not ok and not all(settled(day, p) for p in due):
            # la création ou le lancement a échoué avant tout résultat : on s'arrête là pour ne pas boucler
            log(f"abandon pour le {day} : relance depuis l'admin ou avec "
                f"`docker compose exec shorts python main.py --date {day}`")
            store.set_flag(day, "failed")
        elif all(settled(day, p) for p in times):
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
