"""Planificateur du conteneur, en deux temps (fuseau RUN_TZ) :

- GENERATE_AT : création des vidéos du jour, sans publier (par défaut 01:00)
- PUBLISH_AT  : publication des vidéos du jour (par défaut 16:47 ; ancien nom : RUN_AT)

Il rattrape une étape manquée si le conteneur démarre après l'heure prévue, réessaie en cas
d'échec (main.py reprend là où il s'était arrêté) et fait le ménage : dossiers de travail de
plus de 30 jours, vidéos publiques de plus de 7 jours. Si les vidéos ne sont pas prêtes à
l'heure de publication, elles sont créées à ce moment-là, puis publiées.
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
TZ = ZoneInfo(os.environ.get("RUN_TZ", "Europe/Paris"))
PUBLISH_AT = dt.time.fromisoformat(os.environ.get("PUBLISH_AT") or os.environ.get("RUN_AT") or "16:47")
_gen = os.environ.get("GENERATE_AT", "01:00").strip()
GENERATE_AT = dt.time.fromisoformat(_gen) if _gen else None      # vide : tout se fait à PUBLISH_AT
RETRIES = int(os.environ.get("RUN_RETRIES", 2))
RETRY_WAIT = int(os.environ.get("RUN_RETRY_MINUTES", 30)) * 60
KEEP_OUTPUT_DAYS = int(os.environ.get("KEEP_OUTPUT_DAYS", 30))
KEEP_PUBLIC_DAYS = int(os.environ.get("KEEP_PUBLIC_DAYS", 7))


def log(msg):
    print(f"[{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def flag(day, name):
    return ROOT / "output" / day / name


def set_flag(day, name):
    flag(day, name).parent.mkdir(parents=True, exist_ok=True)
    flag(day, name).touch()


def cleanup(now):
    out = ROOT / "output"
    if out.exists():
        for d in out.iterdir():
            if d.is_dir() and now.timestamp() - d.stat().st_mtime > KEEP_OUTPUT_DAYS * 86400:
                shutil.rmtree(d, ignore_errors=True)
                log(f"ménage : {d.name} supprimé")
    pub = Path(os.environ.get("PUBLIC_VIDEO_DIR", "/public"))
    if pub.exists():
        for f in pub.glob("*.mp4"):
            if now.timestamp() - f.stat().st_mtime > KEEP_PUBLIC_DAYS * 86400:
                f.unlink(missing_ok=True)


def run_step(day, label, extra_args):
    """Lance main.py pour ce jour, avec nouveaux essais. Renvoie True si tout a réussi."""
    for attempt in range(1, RETRIES + 2):
        log(f"{label} du {day} (essai {attempt})")
        code = subprocess.call([sys.executable, str(ROOT / "main.py"), "--date", day, *extra_args], cwd=ROOT)
        if code == 0:
            log(f"{label} du {day} : terminé")
            return True
        log(f"{label} du {day} : échec (code {code})")
        if attempt <= RETRIES:
            log(f"nouvel essai dans {RETRY_WAIT // 60} min")
            time.sleep(RETRY_WAIT)
    return False


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
        log(f"output/ n'est pas accessible en écriture ({e}). Sur le VPS : chown -R 1000:1000 state output public")
        return False


def at(day_date, t):
    return dt.datetime.combine(day_date, t, TZ)


def next_event(now):
    times = [t for t in (GENERATE_AT, PUBLISH_AT) if t]
    upcoming = [at(now.date() + dt.timedelta(days=d), t) for d in (0, 1) for t in times]
    return min(x for x in upcoming if x > now)


def main():
    gen = f"création à {GENERATE_AT:%H:%M}, " if GENERATE_AT else ""
    log(f"planificateur démarré : {gen}publication à {PUBLISH_AT:%H:%M} ({TZ.key})")
    while True:
        if not writable():
            time.sleep(600)
            continue
        now = dt.datetime.now(TZ)
        day = now.date().isoformat()
        published = flag(day, ".done").exists() or flag(day, ".failed").exists()
        generated = published or flag(day, ".generated").exists() or flag(day, ".gen_tried").exists()

        if GENERATE_AT and now >= at(now.date(), GENERATE_AT) and not generated:
            cleanup(now)
            ok = run_step(day, "création des vidéos", ["--no-publish"])
            # en cas d'échec, l'étape de publication retentera la création
            set_flag(day, ".generated" if ok else ".gen_tried")
            continue

        if now >= at(now.date(), PUBLISH_AT) and not published:
            if not GENERATE_AT:
                cleanup(now)
            if run_step(day, "publication", []):
                set_flag(day, ".done")
            else:
                log(f"abandon pour le {day} : relance à la main avec "
                    f"`docker compose exec shorts python main.py --date {day}`")
                set_flag(day, ".failed")
            continue

        nxt = next_event(now)
        log(f"prochaine étape : {nxt:%Y-%m-%d %H:%M}")
        # réveil au plus tard toutes les heures (changement d'heure, horloge ajustée…)
        time.sleep(max(30, min(3600, (nxt - now).total_seconds())))


if __name__ == "__main__":
    main()
