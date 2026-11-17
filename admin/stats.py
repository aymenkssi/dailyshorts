"""Statistiques des chaînes pour l'admin : YouTube, Instagram, Facebook, TikTok (lecture seule, mises en cache)."""
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
def instagram_id(result):
    """Identifiant de publication d'une ligne de published.json ('OK instagram media id 1792…')."""
    m = re.search(r"media id (\d+)", str(result))
    return m.group(1) if m else None


def instagram(media_ids=()):
    def fetch():
        uid = os.environ.get("IG_USER_ID", "")
        tok = (_json(STATE / "ig_token.json") or {}).get("token") or os.environ.get("IG_ACCESS_TOKEN", "")
        if not uid or not tok or uid == "..." or tok == "...":
            raise RuntimeError("non connecté")
        host = os.environ.get("IG_GRAPH_HOST", "graph.facebook.com")
        base = f"https://{host}/{os.environ.get('IG_GRAPH_VERSION', 'v23.0')}"
        r = requests.get(f"{base}/{uid}", timeout=30, params={
            "fields": "username,followers_count,media_count", "access_token": tok})
        if not r.ok:
            raise RuntimeError(f"Instagram {r.status_code} : {r.text[:200]}")
        d = r.json()
        out = {"name": d.get("username", ""), "followers": d.get("followers_count", 0),
               "media": d.get("media_count", 0), "per_media": {}, "views_note": ""}
        views_ok = True
        for mid in list(dict.fromkeys(media_ids))[:60]:
            m = requests.get(f"{base}/{mid}", timeout=30, params={
                "fields": "like_count,comments_count,permalink,timestamp", "access_token": tok})
            if not m.ok:
                out["per_media"][mid] = {"missing": True}
                continue
            it = m.json()
            row = {"likes": it.get("like_count", 0), "comments": it.get("comments_count", 0),
                   "url": it.get("permalink"), "views": None}
            if views_ok:  # les vues demandent la permission de lecture des statistiques (insights)
                v = requests.get(f"{base}/{mid}/insights", timeout=30,
                                 params={"metric": "views", "access_token": tok})
                if v.ok:
                    data = v.json().get("data", [])
                    row["views"] = (data[0].get("values") or [{}])[0].get("value") if data else None
                else:
                    views_ok = False
                    out["views_note"] = ("Vues indisponibles : ajoute la permission instagram_manage_insights "
                                         "à la configuration Meta, puis génère un nouveau jeton.")
            out["per_media"][mid] = row
        return out
    return cached(("ig", tuple(media_ids)), 900, fetch)


# ---------- Facebook ----------
FB_VIEW_METRICS = ("fb_reels_total_plays", "blue_reels_play_count", "total_video_views", "post_video_views")


def facebook_id(result):
    """Identifiant de vidéo d'une ligne de published.json ('OK facebook video id 1234…')."""
    m = re.search(r"facebook video id (\d+)", str(result))
    return m.group(1) if m else None


def _fb_page_token():
    """Même logique que le pipeline : jeton de Page déduit du jeton d'utilisateur système."""
    page = os.environ.get("FB_PAGE_ID", "")
    tok = os.environ.get("FB_ACCESS_TOKEN") or os.environ.get("IG_ACCESS_TOKEN", "")
    if not page or page == "..." or not tok or tok == "...":
        raise RuntimeError("non connecté")
    base = f"https://graph.facebook.com/{os.environ.get('FB_GRAPH_VERSION') or os.environ.get('IG_GRAPH_VERSION', 'v23.0')}"
    r = requests.get(f"{base}/{page}", params={"fields": "access_token", "access_token": tok}, timeout=30)
    if r.ok and r.json().get("access_token"):
        tok = r.json()["access_token"]
    return base, page, tok


def facebook(video_ids=()):
    def fetch():
        base, page, tok = _fb_page_token()
        r = requests.get(f"{base}/{page}", timeout=30, params={
            "fields": "name,followers_count,fan_count", "access_token": tok})
        if not r.ok:
            raise RuntimeError(f"Facebook {r.status_code} : {r.text[:200]}")
        d = r.json()
        out = {"name": d.get("name", ""), "followers": d.get("followers_count", d.get("fan_count", 0)),
               "per_video": {}, "views_note": ""}
        views_ok = True
        for vid in list(dict.fromkeys(video_ids))[:60]:
            v = requests.get(f"{base}/{vid}", timeout=30, params={
                "fields": "permalink_url,likes.summary(true).limit(0),comments.summary(true).limit(0)",
                "access_token": tok})
            if not v.ok:
                out["per_video"][vid] = {"missing": True}
                continue
            it = v.json()
            link = it.get("permalink_url") or ""
            row = {"likes": ((it.get("likes") or {}).get("summary") or {}).get("total_count", 0),
                   "comments": ((it.get("comments") or {}).get("summary") or {}).get("total_count", 0),
                   "url": link if link.startswith("http") else f"https://www.facebook.com{link}" if link
                   else f"https://www.facebook.com/watch/?v={vid}", "views": None}
            if views_ok:  # les vues demandent la permission read_insights
                ins = requests.get(f"{base}/{vid}/video_insights", timeout=30, params={"access_token": tok})
                if ins.ok:
                    got = {m.get("name"): (m.get("values") or [{}])[0].get("value") for m in ins.json().get("data", [])}
                    row["views"] = next((got[k] for k in FB_VIEW_METRICS if isinstance(got.get(k), (int, float))), None)
                else:
                    views_ok = False
                    out["views_note"] = ("Vues indisponibles : ajoute la permission read_insights au jeton "
                                         "(utilisateur système), puis génère un nouveau jeton.")
            out["per_video"][vid] = row
        return out
    return cached(("fb", tuple(video_ids)), 900, fetch)


# ---------- TikTok ----------
SHORT_MAX_SECONDS = 90


def tiktok():
    def fetch():
        tok = _json(STATE / os.environ.get("TIKTOK_TOKEN_FILE", "state/tiktok_token.json").split("/")[-1])
        if not tok:
            raise RuntimeError("non connecté")
        if time.time() > tok.get("expires_at", 0):
            raise RuntimeError("jeton expiré : il sera renouvelé à la prochaine publication")
        h = {"Authorization": f"Bearer {tok['access_token']}"}
        r = requests.get("https://open.tiktokapis.com/v2/user/info/", timeout=30, headers=h,
                         params={"fields": "display_name,follower_count,likes_count,video_count"})
        d = r.json()
        if r.status_code != 200 or d.get("error", {}).get("code") not in (None, "ok"):
            raise RuntimeError("droit de lecture des statistiques manquant (scope user.info.stats) : "
                               "relance tools/tiktok_auth.py")
        u = d["data"]["user"]
        out = {"name": u.get("display_name", ""), "followers": u.get("follower_count", 0),
               "likes": u.get("likes_count", 0), "videos": u.get("video_count", 0),
               "list": [], "list_note": ""}
        v = requests.post("https://open.tiktokapis.com/v2/video/list/", timeout=30,
                          headers={**h, "Content-Type": "application/json"}, json={"max_count": 20},
                          params={"fields": "id,title,video_description,duration,create_time,share_url,"
                                            "view_count,like_count,comment_count,share_count"})
        vd = v.json() if v.headers.get("content-type", "").startswith("application/json") else {}
        if v.status_code != 200 or vd.get("error", {}).get("code") not in (None, "ok"):
            out["list_note"] = ("Détail par vidéo indisponible : ajoute le scope video.list à ton appli TikTok, "
                                "puis relance tools/tiktok_auth.py.")
            return out
        for it in vd.get("data", {}).get("videos", []):
            dur = it.get("duration") or 0
            out["list"].append({
                "date": time.strftime("%Y-%m-%d", time.localtime(it.get("create_time", 0))),
                "title": (it.get("title") or it.get("video_description") or "").split("\n")[0][:90],
                "format": "Short" if dur and dur <= SHORT_MAX_SECONDS else ("Longue" if dur else "—"),
                "url": it.get("share_url"), "views": it.get("view_count", 0), "likes": it.get("like_count", 0),
                "comments": it.get("comment_count", 0), "shares": it.get("share_count", 0)})
        if not out["list"]:
            out["list_note"] = ("Aucune vidéo renvoyée : TikTok ne liste que les vidéos publiques "
                                "(compte privé tant que l'appli n'est pas validée).")
        return out
    return cached("tt", 900, fetch)
