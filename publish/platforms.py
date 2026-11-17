"""Publication automatique : YouTube (vidéo longue + Short), Instagram Reels, TikTok."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def caption_text(meta, limit=2200):
    tags = " ".join("#" + t.lstrip("#").replace(" ", "") for t in meta.get("hashtags", []))
    return f"{meta['title']}\n\n{meta['description']}\n\n{tags}"[:limit]


def _duration(path):
    """Durée en secondes, ou None si le fichier est illisible (incomplet, corrompu)."""
    try:
        return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                              "-of", "csv=p=0", str(path)], stderr=subprocess.DEVNULL))
    except (subprocess.CalledProcessError, ValueError):
        return None


def light_copy(video, max_mb=60):
    """Copie allégée pour Instagram (100 Mo max) et TikTok (envoi en un bloc, 64 Mo max).
    Renvoie la vidéo d'origine si elle est déjà assez légère."""
    video = Path(video)
    limit = max_mb * 1024 * 1024
    if video.stat().st_size <= limit:
        return video
    out = video.with_name("final_social.mp4")
    dur = _duration(video)
    # copie déjà faite : on ne la réutilise que si elle est complète et lisible
    if out.exists() and out.stat().st_size <= limit and abs((_duration(out) or 0) - dur) < 2:
        return out
    kbps = int(max_mb * 8 * 1024 * 0.92 / dur) - 128      # marge de 8 %, moins la piste audio
    tmp = out.with_name("final_social.tmp.mp4")           # écrit à part, renommé seulement une fois complet
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(tmp)], check=True)
    os.replace(tmp, out)
    return out


# ---------------- YouTube ----------------
def publish_youtube(video, meta, short=True, extra=""):
    """short=True : Short vertical (#shorts) ; False : vidéo longue. extra : texte ajouté en tête de description."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    token_file = ROOT / os.environ.get("YOUTUBE_TOKEN_FILE", "state/youtube_token.json")
    creds = Credentials.from_authorized_user_file(str(token_file))
    if not creds.valid:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    yt = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": (meta["title"][:90] + " #shorts")[:100] if short else meta["title"][:100],
            "description": ((extra + "\n\n") if extra else "") + caption_text(meta, 4800),
            "tags": meta.get("hashtags", [])[:15],
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": os.environ.get("YOUTUBE_PRIVACY", "public"),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return f"https://youtube.com/shorts/{resp['id']}" if short else f"https://youtu.be/{resp['id']}"


# ---------------- Instagram ----------------
def _ig_host():
    """graph.facebook.com : connexion unifiée (applis Meta récentes, jeton du portefeuille business).
    graph.instagram.com : ancienne « connexion Instagram » (jeton de 60 jours à rafraîchir)."""
    return os.environ.get("IG_GRAPH_HOST", "graph.facebook.com")


def _ig_token():
    """Renvoie le jeton ; en mode « connexion Instagram », le rafraîchit chaque semaine dans state/."""
    if _ig_host() != "graph.instagram.com":
        return os.environ["IG_ACCESS_TOKEN"]
    state = ROOT / "state" / "ig_token.json"
    tok = json.loads(state.read_text()) if state.exists() else {"token": os.environ["IG_ACCESS_TOKEN"], "ts": 0}
    if time.time() - tok["ts"] > 7 * 86400:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": tok["token"]}, timeout=60)
        if r.ok:
            tok = {"token": r.json()["access_token"], "ts": time.time()}
            state.write_text(json.dumps(tok))
    return tok["token"]


def publish_instagram(video, meta, date, suffix=""):
    """suffix : distingue les fichiers publics du même jour (ex. "-long")."""
    video = light_copy(video)
    # 1) rendre la vidéo accessible publiquement via le serveur web du VPS
    pub_dir = Path(os.environ["PUBLIC_VIDEO_DIR"])
    pub_dir.mkdir(parents=True, exist_ok=True)
    name = f"{date}{suffix}.mp4"
    shutil.copy(video, pub_dir / name)
    url = os.environ["PUBLIC_VIDEO_BASE_URL"].rstrip("/") + "/" + name

    base = f"https://{_ig_host()}/{os.environ.get('IG_GRAPH_VERSION', 'v23.0')}"
    uid, token = os.environ["IG_USER_ID"], _ig_token()
    r = requests.post(f"{base}/{uid}/media", data={
        "media_type": "REELS", "video_url": url, "caption": caption_text(meta),
        "share_to_feed": "true", "access_token": token}, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Instagram {r.status_code} : {r.text[:400]}")
    container = r.json()["id"]
    # 2) attendre le traitement côté Meta
    for _ in range(30):
        time.sleep(30)
        info = requests.get(f"{base}/{container}", params={"fields": "status_code,status", "access_token": token},
                            timeout=60).json()
        st = info.get("status_code")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram : traitement {st} — {info.get('status', 'sans détail')}")
    else:
        raise RuntimeError("Instagram : délai de traitement dépassé")
    # 3) publier
    r = requests.post(f"{base}/{uid}/media_publish",
                      data={"creation_id": container, "access_token": token}, timeout=120)
    r.raise_for_status()
    return f"instagram media id {r.json()['id']}"


# ---------------- TikTok ----------------
TT = "https://open.tiktokapis.com/v2"


def _tiktok_token():
    f = ROOT / os.environ.get("TIKTOK_TOKEN_FILE", "state/tiktok_token.json")
    tok = json.loads(f.read_text())
    if time.time() > tok.get("expires_at", 0) - 600:
        r = requests.post(f"{TT}/oauth/token/", data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, timeout=60)
        r.raise_for_status()
        new = r.json()
        tok.update(access_token=new["access_token"], refresh_token=new["refresh_token"],
                   expires_at=time.time() + new["expires_in"])
        f.write_text(json.dumps(tok))
    return tok["access_token"]


def _tiktok_upload(h, init_url, body, video):
    """Initialise l'envoi, transfère le fichier en un bloc, renvoie le publish_id."""
    size = Path(video).stat().st_size
    body["source_info"] = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
    init = requests.post(init_url, headers=h, timeout=60, json=body).json()
    if init.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok init : {init['error']}")
    data = init["data"]
    with open(video, "rb") as fh:
        up = requests.put(data["upload_url"], data=fh.read(), timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}"})
    up.raise_for_status()
    return data["publish_id"]


def _tiktok_status(h, publish_id, done):
    """Attend que TikTok ait traité la vidéo ; done : statuts considérés comme réussis."""
    for _ in range(40):
        time.sleep(15)
        d = requests.post(f"{TT}/post/publish/status/fetch/", headers=h, timeout=60,
                          json={"publish_id": publish_id}).json().get("data", {})
        st = d.get("status")
        if st in done:
            return st
        if st == "FAILED":
            raise RuntimeError(f"TikTok : échec du traitement ({d.get('fail_reason', 'sans détail')})")
    return "en traitement"


def publish_tiktok(video, meta):
    """TIKTOK_MODE=inbox (par défaut) : la vidéo arrive en brouillon dans la boîte de réception TikTok ;
    tu la publies depuis l'appli (légende, visibilité). Fonctionne sans validation de l'appli par TikTok.
    TIKTOK_MODE=direct : publication directe ; tant que l'appli n'est pas validée par TikTok, elle n'est
    acceptée que sur un compte privé et reste visible par toi seul."""
    token = _tiktok_token()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    video = light_copy(video)

    if os.environ.get("TIKTOK_MODE", "inbox") != "direct":
        pid = _tiktok_upload(h, f"{TT}/post/publish/inbox/video/init/", {}, video)
        st = _tiktok_status(h, pid, {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"})
        return f"tiktok brouillon envoyé dans ta boîte de réception TikTok ({st}, publish_id {pid})"

    info = requests.post(f"{TT}/post/publish/creator_info/query/", headers=h, timeout=60).json()["data"]
    options = info.get("privacy_level_options", [])
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else options[0]
    max_s = info.get("max_video_post_duration_sec")
    dur = _duration(video) or 0
    if max_s and dur > max_s:
        raise RuntimeError(f"TikTok : vidéo de {dur:.0f} s, mais ce compte est limité à {max_s} s")
    pid = _tiktok_upload(h, f"{TT}/post/publish/video/init/", {
        "post_info": {"title": caption_text(meta), "privacy_level": privacy, "is_aigc": True,
                      "disable_comment": False, "disable_duet": False, "disable_stitch": False}}, video)
    st = _tiktok_status(h, pid, {"PUBLISH_COMPLETE"})
    return f"tiktok publish_id {pid} ({privacy}, {st})"
