"""Statistiques des chaînes pour l'admin : YouTube, Instagram, TikTok (lecture seule, mises en cache)."""
import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        value = fn()
    except Exception as e:  # noqa: BLE001
        value = {"error": str(e)[:300]}
    _cache[key] = (time.time(), value)
    return value


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


# ---------- YouTube ----------
def _yt_token():
    tok = _json(STATE / os.environ.get("YOUTUBE_TOKEN_FILE", "state/youtube_token.json").split("/")[-1])
    if not tok:
        raise RuntimeError("non connecté")
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": tok["client_id"], "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if not r.ok:
        raise RuntimeError(f"autorisation refusée ({r.status_code}) : relance tools/youtube_auth.py")
    return r.json()["access_token"]


def youtube(video_ids):
    def fetch():
        h = {"Authorization": f"Bearer {_yt_token()}"}
        api = "https://www.googleapis.com/youtube/v3"
        r = requests.get(f"{api}/channels", headers=h, timeout=30,
                         params={"part": "snippet,statistics", "mine": "true"})
        if r.status_code == 403:
            raise RuntimeError("droit de lecture manquant : relance tools/youtube_auth.py (nouvelle version) "
                               "puis recopie youtube_token.json sur le VPS")
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            raise RuntimeError("aucune chaîne trouvée pour ce compte")
        ch = items[0]
        out = {"name": ch["snippet"]["title"],
               "subscribers": int(ch["statistics"].get("subscriberCount", 0)),
               "views": int(ch["statistics"].get("viewCount", 0)),
               "videos": int(ch["statistics"].get("videoCount", 0)), "per_video": {}}
        ids = list(dict.fromkeys(video_ids))[:50]
        if ids:
            v = requests.get(f"{api}/videos", headers=h, timeout=30,
                             params={"part": "statistics,status", "id": ",".join(ids)})
            v.raise_for_status()
            for it in v.json().get("items", []):
                st = it["statistics"]
                out["per_video"][it["id"]] = {
                    "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                    "comments": int(st.get("commentCount", 0)), "privacy": it["status"]["privacyStatus"]}
        return out
    return cached(("yt", tuple(video_ids)), 900, fetch)


def youtube_id(result):
    """Extrait l'identifiant de vidéo d'une ligne de published.json ('OK https://youtu.be/ID')."""
    m = re.search(r"(?:youtu\.be/|shorts/)([\w-]{6,})", str(result))
    return m.group(1) if m else None


# ---------- Instagram ----------
def instagram():
    def fetch():
        uid = os.environ.get("IG_USER_ID", "")
        tok = (_json(STATE / "ig_token.json") or {}).get("token") or os.environ.get("IG_ACCESS_TOKEN", "")
        if not uid or not tok or uid == "..." or tok == "...":
            raise RuntimeError("non connecté")
        base = f"https://graph.instagram.com/{os.environ.get('IG_GRAPH_VERSION', 'v23.0')}"
        r = requests.get(f"{base}/{uid}", timeout=30, params={
            "fields": "username,followers_count,media_count", "access_token": tok})
        r.raise_for_status()
        d = r.json()
        out = {"name": d.get("username", ""), "followers": d.get("followers_count", 0),
               "media": d.get("media_count", 0), "recent": []}
        m = requests.get(f"{base}/{uid}/media", timeout=30, params={
            "fields": "id,caption,permalink,timestamp,like_count,comments_count", "limit": 10,
            "access_token": tok})
        if m.ok:
            for it in m.json().get("data", []):
                out["recent"].append({"title": (it.get("caption") or "").split("\n")[0][:80],
                                      "url": it.get("permalink"), "date": (it.get("timestamp") or "")[:10],
                                      "likes": it.get("like_count", 0), "comments": it.get("comments_count", 0)})
        return out
    return cached("ig", 900, fetch)


# ---------- TikTok ----------
def tiktok():
    def fetch():
        tok = _json(STATE / os.environ.get("TIKTOK_TOKEN_FILE", "state/tiktok_token.json").split("/")[-1])
        if not tok:
            raise RuntimeError("non connecté")
        if time.time() > tok.get("expires_at", 0):
            raise RuntimeError("jeton expiré : il sera renouvelé à la prochaine publication")
        r = requests.get("https://open.tiktokapis.com/v2/user/info/", timeout=30,
                         headers={"Authorization": f"Bearer {tok['access_token']}"},
                         params={"fields": "display_name,follower_count,likes_count,video_count"})
        d = r.json()
        if r.status_code != 200 or d.get("error", {}).get("code") not in (None, "ok"):
            raise RuntimeError("droit de lecture des statistiques manquant (scope user.info.stats) : "
                               "relance tools/tiktok_auth.py")
        u = d["data"]["user"]
        return {"name": u.get("display_name", ""), "followers": u.get("follower_count", 0),
                "likes": u.get("likes_count", 0), "videos": u.get("video_count", 0)}
    return cached("tt", 900, fetch)
