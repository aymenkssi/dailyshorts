"""État partagé entre le pipeline, le planificateur et l'interface d'administration.

Tout est dans des fichiers JSON de state/ et des fichiers-drapeaux dans output/<jour>/ :
aucune dépendance externe, pour que l'image de l'admin reste légère.
"""
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUTPUT = ROOT / "output"
SETTINGS_FILE = STATE / "settings.json"
TOPICS_FILE = STATE / "topics.json"
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# clé -> (variable d'environnement, valeur par défaut, type)
SCHEMA = {
    "generate_at": ("GENERATE_AT", "01:00", "time"),
    "publish_at": ("PUBLISH_AT", "16:47", "time"),
    # heure propre à une plateforme ; vide = l'heure de publication générale
    "publish_at_youtube": ("PUBLISH_AT_YOUTUBE", "", "timeopt"),
    "publish_at_instagram": ("PUBLISH_AT_INSTAGRAM", "", "timeopt"),
    "publish_at_tiktok": ("PUBLISH_AT_TIKTOK", "", "timeopt"),
    "publish_at_facebook": ("PUBLISH_AT_FACEBOOK", "", "timeopt"),
    # heure propre au Short sur cette plateforme ; vide = la même heure que la vidéo longue
    "publish_at_youtube_short": ("PUBLISH_AT_YOUTUBE_SHORT", "", "timeopt"),
    "publish_at_instagram_short": ("PUBLISH_AT_INSTAGRAM_SHORT", "", "timeopt"),
    "publish_at_tiktok_short": ("PUBLISH_AT_TIKTOK_SHORT", "", "timeopt"),
    "publish_at_facebook_short": ("PUBLISH_AT_FACEBOOK_SHORT", "", "timeopt"),
    "daily_infographic": ("DAILY_INFOGRAPHIC", False, "bool"),
    "infographic_at": ("INFOGRAPHIC_AT", "18:00", "time"),
    "send_instagram_image": ("SEND_INSTAGRAM_IMAGE", True, "bool"),
    "send_facebook_image": ("SEND_FACEBOOK_IMAGE", True, "bool"),
    "send_tiktok_image": ("SEND_TIKTOK_IMAGE", True, "bool"),
    "subscribe_cta": ("SUBSCRIBE_CTA", True, "bool"),
    "long_seconds": ("LONG_SECONDS", 240, "int"),
    "short_seconds": ("SHORT_SECONDS", 40, "int"),
    "publish_youtube": ("PUBLISH_YOUTUBE", False, "bool"),
    "publish_instagram": ("PUBLISH_INSTAGRAM", False, "bool"),
    "publish_tiktok": ("PUBLISH_TIKTOK", False, "bool"),
    "publish_facebook": ("PUBLISH_FACEBOOK", False, "bool"),
    "long_to_social": ("LONG_TO_SOCIAL", False, "bool"),  # ancien réglage, repris par la grille ci-dessous
    # quelle vidéo part sur quelle plateforme (en plus de l'interrupteur de la plateforme)
    "send_youtube_long": ("SEND_YOUTUBE_LONG", True, "bool"),
    "send_youtube_short": ("SEND_YOUTUBE_SHORT", True, "bool"),
    "send_instagram_long": ("SEND_INSTAGRAM_LONG", False, "bool"),
    "send_instagram_short": ("SEND_INSTAGRAM_SHORT", True, "bool"),
    "send_tiktok_long": ("SEND_TIKTOK_LONG", False, "bool"),
    "send_tiktok_short": ("SEND_TIKTOK_SHORT", True, "bool"),
    "send_facebook_long": ("SEND_FACEBOOK_LONG", True, "bool"),
    "send_facebook_short": ("SEND_FACEBOOK_SHORT", True, "bool"),
    "weekly_recap": ("WEEKLY_RECAP", True, "bool"),
    "daily_news_short": ("DAILY_NEWS_SHORT", True, "bool"),
    "news_short_seconds": ("NEWS_SHORT_SECONDS", 80, "int"),
    "recap_weekday": ("RECAP_WEEKDAY", "6", "choice:0,1,2,3,4,5,6"),
    "recap_seconds": ("RECAP_SECONDS", 360, "int"),
    "tiktok_mode": ("TIKTOK_MODE", "inbox", "choice:inbox,direct"),
    "youtube_privacy": ("YOUTUBE_PRIVACY", "private", "choice:private,unlisted,public"),
    "require_approval": ("REQUIRE_APPROVAL", False, "bool"),
    "niche": ("CHANNEL_NICHE", "", "text"),
    "use_claude": ("USE_CLAUDE", True, "bool"),
    "claude_model": ("CLAUDE_MODEL", "sonnet", "choice:sonnet,opus"),
    "character_refs": ("CHARACTER_REFS", True, "bool"),
    "particles": ("PARTICLES", True, "bool"),
    "parallax": ("PARALLAX", True, "bool"),
    "kokoro_voice": ("KOKORO_VOICE", "am_michael", "choice:am_michael,am_fenrir,am_puck,bm_george,bm_fable,af_heart,af_bella,af_nicole,af_sarah,af_sky,bf_emma,bf_isabella"),
    # voix des vidéos d'actualité (récap du dimanche et Short d'actualité) ; vide = la voix ci-dessus
    "news_voice": ("NEWS_VOICE", "", "choice:,am_michael,am_fenrir,am_puck,bm_george,bm_fable,af_heart,af_bella,af_nicole,af_sarah,af_sky,bf_emma,bf_isabella"),
    "music_volume": ("MUSIC_VOLUME", 0.10, "float"),
}
LIMITS = {"recap_seconds": (120, 600), "long_seconds": (60, 600), "short_seconds": (15, 90), "news_short_seconds": (30, 120), "music_volume": (0.0, 1.0)}


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    """Écriture atomique : jamais de fichier à moitié écrit si deux processus se croisent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _coerce(key, value):
    kind = SCHEMA[key][2]
    if kind == "bool":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        v = int(float(value))
    elif kind == "float":
        v = float(value)
    elif kind == "timeopt":
        v = str(value).strip()
        if v and not TIME_RE.match(v):
            raise ValueError(f"{key} : heure invalide (format HH:MM)")
        return v
    elif kind == "time":
        v = str(value).strip()
        if not TIME_RE.match(v):
            raise ValueError(f"{key} : heure invalide (format HH:MM)")
        return v
    elif kind.startswith("choice:"):
        v = str(value).strip()
        if v not in kind[7:].split(","):
            raise ValueError(f"{key} : valeur non autorisée")
        return v
    else:
        return str(value).strip()[:600]
    lo, hi = LIMITS.get(key, (None, None))
    if lo is not None and not lo <= v <= hi:
        raise ValueError(f"{key} : doit être entre {lo} et {hi}")
    return v


# ---------- Réglages ----------
def get_settings():
    """Valeurs du .env (ou par défaut), remplacées par celles enregistrées depuis l'admin."""
    saved = _read(SETTINGS_FILE, {})
    out = {}
    for key, (env, default, _) in SCHEMA.items():
        raw = saved.get(key, os.environ.get(env, default))
        if key == "publish_at" and key not in saved and "PUBLISH_AT" not in os.environ:
            raw = os.environ.get("RUN_AT", default)  # ancien nom
        if key in ("send_instagram_long", "send_tiktok_long") and key not in saved and "long_to_social" in saved:
            raw = saved["long_to_social"]  # reprise de l'ancien réglage « vidéo longue aussi sur Instagram et TikTok »
        try:
            out[key] = _coerce(key, raw)
        except (ValueError, TypeError):
            out[key] = default
    return out


def platform_active(s, platform):
    """Plateforme activée et avec au moins une vidéo à envoyer."""
    return bool(s[f"publish_{platform}"] and (s[f"send_{platform}_long"] or s[f"send_{platform}_short"]))


def save_settings(values):
    """Valide et enregistre ; lève ValueError avec un message lisible si une valeur est refusée."""
    current = _read(SETTINGS_FILE, {})
    for key, value in values.items():
        if key in SCHEMA:
            current[key] = _coerce(key, value)
    _write(SETTINGS_FILE, current)
    return get_settings()


def apply_env():
    """Reporte les réglages dans os.environ pour le code qui lit encore l'environnement."""
    for key, value in get_settings().items():
        env = SCHEMA[key][0]
        os.environ[env] = ("true" if value else "false") if isinstance(value, bool) else str(value)


# ---------- File de sujets ----------
def get_topics():
    return [t for t in _read(TOPICS_FILE, []) if isinstance(t, dict) and t.get("topic")]


def add_topic(text):
    text = " ".join(str(text).split())[:300]
    if not text:
        raise ValueError("Sujet vide")
    topics = get_topics()
    topics.append({"topic": text, "added": dt.date.today().isoformat()})
    _write(TOPICS_FILE, topics)


def remove_topic(index):
    topics = get_topics()
    if 0 <= index < len(topics):
        topics.pop(index)
        _write(TOPICS_FILE, topics)


def move_topic(index, delta):
    topics = get_topics()
    j = index + delta
    if 0 <= index < len(topics) and 0 <= j < len(topics):
        topics[index], topics[j] = topics[j], topics[index]
        _write(TOPICS_FILE, topics)


def pop_topic():
    """Retire et renvoie le prochain sujet imposé, ou None si la file est vide."""
    topics = get_topics()
    if not topics:
        return None
    first = topics.pop(0)
    _write(TOPICS_FILE, topics)
    return first["topic"]


# ---------- État d'une journée ----------
PLATFORMS = ("youtube", "instagram", "tiktok", "facebook")
FLAGS = ("send_youtube", "send_instagram", "send_tiktok", "send_facebook", "generated", "gen_tried", "approved", "rejected", "publish_now", "done", "failed", "running")


def valid_day(day):
    return bool(DAY_RE.match(str(day)))


def day_dir(day):
    if not valid_day(day):
        raise ValueError("date invalide")
    return OUTPUT / day


def flag(day, name):
    return day_dir(day) / f".{name}"


def has_flag(day, name):
    return flag(day, name).exists()


def set_flag(day, name, on=True):
    f = flag(day, name)
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
    else:
        f.unlink(missing_ok=True)


def day_status(day):
    """Résumé d'une journée pour l'admin et le planificateur."""
    d = day_dir(day)
    flags = {n: (d / f".{n}").exists() for n in FLAGS}
    videos = {k: (d / k / "final.mp4").exists() for k in ("long", "short")}
    scripts = _read(d / "scripts.json", {})
    published = _read(d / "published.json", {})
    tried = [v for v in published.values() if v]
    all_ok = bool(tried) and all(str(v).startswith("OK") for v in tried) and all(videos.values())
    if flags["done"] or (flags["failed"] and all_ok):  # échec rattrapé par une relance
        state = "publiée"
    elif flags["rejected"]:
        state = "refusée"
    elif flags["failed"]:
        state = "échec"
    elif flags["running"]:
        state = "en cours"
    elif all(videos.values()):
        state = "approuvée" if flags["approved"] else "prête"
    elif flags["gen_tried"]:
        state = "création échouée"
    else:
        state = "à créer"
    return {"day": day, "state": state, "flags": flags, "videos": videos,
            "scripts": scripts, "published": published,
            "title": (scripts.get("long") or {}).get("title", ""),
            "topic": (scripts.get("long") or {}).get("topic", "")}


def list_days(limit=60):
    if not OUTPUT.exists():
        return []
    days = sorted((p.name for p in OUTPUT.iterdir() if p.is_dir() and valid_day(p.name)), reverse=True)
    return days[:limit]
