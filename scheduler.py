"""Planificateur du conteneur : lance main.py chaque jour à RUN_AT (fuseau RUN_TZ).

- rattrape le passage du jour si le conteneur démarre après l'heure prévue
- réessaie en cas d'échec (main.py reprend là où il s'était arrêté)
- fait le ménage : dossiers de travail > 30 jours, vidéos publiques > 7 jours
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
RUN_AT = dt.time.fromisoformat(os.environ.get("RUN_AT", "16:47"))
RETRIES = int(os.environ.get("RUN_RETRIES", 2))
RETRY_WAIT = int(os.environ.get("RUN_RETRY_MINUTES", 30)) * 60
KEEP_OUTPUT_DAYS = int(os.environ.get("KEEP_OUTPUT_DAYS", 30))
KEEP_PUBLIC_DAYS = int(os.environ.get("KEEP_PUBLIC_DAYS", 7))


def log(msg):
    print(f"[{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def marker(day):
    return ROOT / "output" / day / ".done"


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


def run_day(day):
    for attempt in range(1, RETRIES + 2):
        log(f"lancement de la vidéo du {day} (essai {attempt})")
        code = subprocess.call([sys.executable, str(ROOT / "main.py"), "--date", day], cwd=ROOT)
        if code == 0:
            marker(day).parent.mkdir(parents=True, exist_ok=True)
            marker(day).touch()
            log(f"vidéo du {day} terminée")
            return
        log(f"échec (code {code})")
        if attempt <= RETRIES:
            log(f"nouvel essai dans {RETRY_WAIT // 60} min")
            time.sleep(RETRY_WAIT)
    log(f"abandon pour le {day} : relance à la main avec `docker compose exec shorts python main.py --date {day}`")
    marker(day).parent.mkdir(parents=True, exist_ok=True)
    (marker(day).parent / ".failed").touch()


def next_run(now):
    today_run = dt.datetime.combine(now.date(), RUN_AT, TZ)
    return today_run if now < today_run else today_run + dt.timedelta(days=1)


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


def main():
    log(f"planificateur démarré : chaque jour à {RUN_AT:%H:%M} ({TZ.key})")
    while True:
        if not writable():
            time.sleep(600)
            continue
        now = dt.datetime.now(TZ)
        day = now.date().isoformat()
        due = now >= dt.datetime.combine(now.date(), RUN_AT, TZ)
        already = marker(day).exists() or (marker(day).parent / ".failed").exists()
        if due and not already:
            cleanup(now)
            run_day(day)
            continue
        nxt = next_run(now)
        log(f"prochaine vidéo : {nxt:%Y-%m-%d %H:%M}")
        # réveil au plus tard toutes les heures (changement d'heure, horloge ajustée…)
        time.sleep(max(30, min(3600, (nxt - now).total_seconds())))


if __name__ == "__main__":
    main()
