"""Interface d'administration de Daily Shorts (FastAPI, pages rendues côté serveur).

Elle ne lance jamais le pipeline elle-même : elle écrit des réglages et des drapeaux que le
planificateur lit chaque minute. Elle sert aussi de portier (forwardAuth Traefik) pour le
tableau de bord FreeLLMAPI publié sur un sous-domaine.
"""
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from shorts import store  # noqa: E402
from admin import stats  # noqa: E402

TZ = ZoneInfo(os.environ.get("RUN_TZ", "Europe/Paris"))
ADMIN_DOMAIN = os.environ.get("ADMIN_DOMAIN", "daily.creationapp.academy")
LLM_DOMAIN = os.environ.get("LLM_DOMAIN", "llm.daily.creationapp.academy")
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", ADMIN_DOMAIN)
SECURE_COOKIE = os.environ.get("INSECURE_COOKIE", "") != "1"       # désactivable pour les tests locaux
SECRET = os.environ.get("SESSION_SECRET", "").encode()
PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
SESSION_HOURS = 12
COOKIE = "ds_session"
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Anime Storytelling")
# fichiers de vérification de domaine demandés par TikTok ou Google (déposés dans state/verify/)
VERIFY_RE = re.compile(r"^/(tiktok[\w-]{4,80}\.txt|google[\w-]{4,80}\.html)$")

app = FastAPI(title="Daily Shorts", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ---------------- Sécurité ----------------
def verify_password(password):
    """ADMIN_PASSWORD_HASH = scrypt$<sel hex>$<empreinte hex> (créé par tools/admin_password.py)."""
    try:
        _, salt, digest = PASSWORD_HASH.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError):
        return False


def _sign(data):
    return hmac.new(SECRET, data.encode(), hashlib.sha256).hexdigest()


def new_session():
    body = f"{int(time.time()) + SESSION_HOURS * 3600}.{secrets.token_hex(8)}"
    return f"{body}.{_sign(body)}"


def valid_session(token):
    if not SECRET or not token or token.count(".") != 2:
        return False
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body), sig):
        return False
    return int(body.split(".")[0]) > time.time()


def csrf_token(request):
    return _sign("csrf:" + request.cookies.get(COOKIE, ""))


def client_ip(request):
    # Traefik ajoute l'adresse réelle en dernier ; les valeurs envoyées par le client sont avant
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[-1].strip() if xff else (request.client.host if request.client else "?")


_failures = {}


def too_many_failures(ip):
    recent = [t for t in _failures.get(ip, []) if time.time() - t < 900]
    _failures[ip] = recent
    return len(recent) >= 5


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    public = (path in ("/login", "/auth/check", "/healthz", "/terms", "/privacy")
              or path.startswith("/static/") or VERIFY_RE.match(path))
    if not public and not valid_session(request.cookies.get(COOKIE)):
        return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
    if not public and request.method == "POST":
        # on lit le corps brut (rejouable pour la suite), pas request.form() qui le consommerait
        sent = parse_qs((await request.body()).decode("utf-8", "replace")).get("csrf", [""])[0]
        if not hmac.compare_digest(sent, csrf_token(request)):
            return Response("Requête refusée (jeton de formulaire invalide)", status_code=403)
    response = await call_next(request)
    response.headers.update({
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                                   "form-action 'self'; base-uri 'self'; object-src 'none'",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
        "Strict-Transport-Security": "max-age=31536000",
    })
    return response


def page(request, name, **ctx):
    ctx.update(channel=CHANNEL_NAME, csrf=csrf_token(request), llm_url=f"https://{LLM_DOMAIN}", active=name,
               now=dt.datetime.now(TZ), flash=request.query_params.get("msg", ""))
    return templates.TemplateResponse(request, f"{name}.html", ctx)


def back(path, msg=""):
    return RedirectResponse(path + (f"?msg={quote(msg)}" if msg else ""), status_code=303)


def today():
    return dt.datetime.now(TZ).date().isoformat()


# ---------------- Connexion ----------------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": "", "channel": CHANNEL_NAME})


def safe_next(target):
    """N'autorise que les retours vers l'admin ou le sous-domaine FreeLLMAPI (pas de redirection ouverte)."""
    u = urlparse(target)
    if not u.netloc and target.startswith("/") and not target.startswith("//"):
        return target
    if u.scheme == "https" and u.netloc in (ADMIN_DOMAIN, LLM_DOMAIN):
        return target
    return "/"


@app.post("/login")
def login(request: Request, password: str = Form(...), next: str = Form("/")):
    ip = client_ip(request)
    if too_many_failures(ip):
        return templates.TemplateResponse(request, "login.html", {
            "next": next, "channel": CHANNEL_NAME, "error": "Trop d'essais. Réessaie dans 15 minutes."}, status_code=429)
    if not (SECRET and PASSWORD_HASH and verify_password(password)):
        _failures.setdefault(ip, []).append(time.time())
        time.sleep(1)
        return templates.TemplateResponse(request, "login.html", {
            "next": next, "channel": CHANNEL_NAME, "error": "Mot de passe incorrect."}, status_code=401)
    resp = RedirectResponse(safe_next(next), status_code=303)
    resp.set_cookie(COOKIE, new_session(), max_age=SESSION_HOURS * 3600, httponly=True, secure=SECURE_COOKIE,
                    samesite="lax", domain=COOKIE_DOMAIN if SECURE_COOKIE else None, path="/")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE, domain=COOKIE_DOMAIN if SECURE_COOKIE else None, path="/")
    return resp


@app.get("/auth/check")
def auth_check(request: Request):
    """Appelé par Traefik avant chaque requête vers FreeLLMAPI : 200 si connecté à l'admin."""
    if valid_session(request.cookies.get(COOKIE)):
        return Response(status_code=200)
    host = request.headers.get("x-forwarded-host", LLM_DOMAIN)
    uri = request.headers.get("x-forwarded-uri", "/")
    if request.headers.get("x-forwarded-method", "GET") != "GET":
        return Response("Connexion à l'administration requise", status_code=401)
    target = safe_next(f"https://{host}{uri}")
    return RedirectResponse(f"https://{ADMIN_DOMAIN}/login?next={quote(target, safe='')}", status_code=302)


# ---------------- Pages publiques (exigées par TikTok et Google pour valider une appli) ----------------
@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return templates.TemplateResponse(request, "legal.html", {
        "kind": "terms", "channel": CHANNEL_NAME, "email": CONTACT_EMAIL, "domain": ADMIN_DOMAIN})


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "legal.html", {
        "kind": "privacy", "channel": CHANNEL_NAME, "email": CONTACT_EMAIL, "domain": ADMIN_DOMAIN})


# ---------------- Pages ----------------
def log_tail(n=200):
    try:
        return (store.OUTPUT / "scheduler.log").read_text(errors="replace").splitlines()[-n:]
    except OSError:
        return []


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    days = store.list_days(8)
    return page(request, "dashboard", s=store.get_settings(), today=store.day_status(today()),
                recent=[store.day_status(d) for d in days if d != today()][:7],
                topics=store.get_topics(), log=log_tail(12))


@app.get("/videos", response_class=HTMLResponse)
def videos(request: Request):
    return page(request, "videos", days=[store.day_status(d) for d in store.list_days(90)], today_day=today())


@app.get("/videos/{day}", response_class=HTMLResponse)
def video_day(request: Request, day: str):
    if not store.valid_day(day):
        raise HTTPException(404)
    return page(request, "day", d=store.day_status(day), is_today=day == today(), s=store.get_settings())


@app.get("/media/{day}/{kind}.mp4")
def media(day: str, kind: str):
    if not store.valid_day(day) or kind not in ("long", "short"):
        raise HTTPException(404)
    f = store.day_dir(day) / kind / "final.mp4"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="video/mp4")


@app.post("/videos/{day}/{action}")
def video_action(day: str, action: str):
    if not store.valid_day(day):
        raise HTTPException(404)
    st = store.day_status(day)
    if st["flags"]["running"]:
        return back(f"/videos/{day}", "Une création ou une publication est en cours : réessaie quand elle est finie.")
    is_today = day == today()
    if action == "delete":
        shutil.rmtree(store.day_dir(day), ignore_errors=True)
        return back("/videos", f"Journée {day} supprimée.")
    if not is_today:
        return back(f"/videos/{day}", "Cette action n'est possible que pour la journée en cours.")
    if action == "approve" and all(st["videos"].values()):
        store.set_flag(day, "rejected", False)
        store.set_flag(day, "approved")
        msg = "Vidéos approuvées : elles seront publiées à l'heure prévue."
    elif action == "reject":
        store.set_flag(day, "approved", False)
        store.set_flag(day, "rejected")
        msg = "Vidéos refusées : rien ne sera publié aujourd'hui."
    elif action == "redo":
        shutil.rmtree(store.day_dir(day), ignore_errors=True)
        return back("/", "Vidéos supprimées : une nouvelle création démarre dans la minute.")
    elif action.startswith("send_") and action[5:] in store.PLATFORMS and st["videos"]["short"]:
        store.set_flag(day, action)
        msg = f"Envoi du Short sur {action[5:].capitalize()} demandé : il démarre dans la minute (suivi dans le journal)."
    elif action == "publish_now" and all(st["videos"].values()) and not st["flags"]["done"]:
        for name in ("rejected", "failed"):
            store.set_flag(day, name, False)
        store.set_flag(day, "approved")
        store.set_flag(day, "publish_now")
        msg = "Publication demandée : elle démarre dans la minute."
    else:
        msg = "Action impossible dans l'état actuel."
    return back(f"/videos/{day}", msg)


@app.get("/sujets", response_class=HTMLResponse)
def topics_page(request: Request):
    return page(request, "topics", topics=store.get_topics())


@app.post("/sujets/add")
def topic_add(topic: str = Form("")):
    try:
        store.add_topic(topic)
        return back("/sujets", "Sujet ajouté à la file.")
    except ValueError as e:
        return back("/sujets", str(e))


@app.post("/sujets/{index}/{action}")
def topic_action(index: int, action: str):
    if action == "delete":
        store.remove_topic(index)
    elif action in ("up", "down"):
        store.move_topic(index, -1 if action == "up" else 1)
    return back("/sujets")


# cases à cocher présentes dans la page Réglages (la formulaire les liste dans « _bools »)
BOOL_KEYS = [k for k, (_, _, kind) in store.SCHEMA.items() if kind == "bool" and k != "long_to_social"]


@app.get("/reglages", response_class=HTMLResponse)
def settings_page(request: Request):
    return page(request, "settings", s=store.get_settings(), bool_keys=",".join(BOOL_KEYS))


@app.post("/reglages")
async def settings_save(request: Request):
    form = await request.form()
    values = {k: form.get(k, "") for k in store.SCHEMA}
    # une case décochée est absente du formulaire : on ne la lit que si la page envoyée la contenait
    # (une page ouverte avant une mise à jour n'a pas les nouvelles cases : elle ne doit pas les décocher)
    shown = set(str(form.get("_bools", "")).split(","))
    for k, (_, _, kind) in store.SCHEMA.items():
        if kind == "bool":
            if k in shown:
                values[k] = k in form
            else:
                values.pop(k, None)
        elif k not in form:
            values.pop(k, None)  # champ absent d'une page périmée : valeur conservée
    try:
        store.save_settings(values)
        return back("/reglages", "Réglages enregistrés. Ils sont pris en compte dans la minute.")
    except (ValueError, TypeError) as e:
        return back("/reglages", f"Réglage refusé — {e}")


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    """Une ligne par vidéo publiée, avec son format (Longue / Short), d'après published.json."""
    yt_rows, ig_rows, fb_rows = [], [], []
    for d in store.list_days(30):
        st = store.day_status(d)
        pub = st["published"]
        for key, fmt in (("youtube_long", "Longue"), ("youtube_short", "Short")):
            vid = stats.youtube_id(pub.get(key))
            if vid:
                yt_rows.append({"day": d, "format": fmt, "id": vid,
                                "title": (st["scripts"].get("long" if fmt == "Longue" else "short") or {}).get("title")
                                or st["title"]})
        for key, fmt in (("instagram_long", "Longue"), ("instagram", "Short")):
            mid = stats.instagram_id(pub.get(key))
            if mid:
                ig_rows.append({"day": d, "format": fmt, "id": mid,
                                "title": (st["scripts"].get("long" if fmt == "Longue" else "short") or {}).get("title")
                                or st["title"]})
        for key, fmt in (("facebook_long", "Longue"), ("facebook", "Short")):
            vid = stats.facebook_id(pub.get(key))
            if vid:
                fb_rows.append({"day": d, "format": fmt, "id": vid,
                                "title": (st["scripts"].get("long" if fmt == "Longue" else "short") or {}).get("title")
                                or st["title"]})
    return page(request, "stats", yt=stats.youtube([r["id"] for r in yt_rows]),
                ig=stats.instagram([r["id"] for r in ig_rows]), tt=stats.tiktok(),
                fb=stats.facebook([r["id"] for r in fb_rows]),
                yt_rows=yt_rows, ig_rows=ig_rows, fb_rows=fb_rows)


# ---------------- Comptes connectés (connexion TikTok depuis l'admin) ----------------
TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_API = "https://open.tiktokapis.com/v2"
TT_SCOPES = os.environ.get("TIKTOK_SCOPES", "user.info.basic,user.info.stats,video.list,video.upload")
STATE_DIR = ROOT / "state"
TT_TOKEN = STATE_DIR / os.environ.get("TIKTOK_TOKEN_FILE", "state/tiktok_token.json").split("/")[-1]
TT_STATE_COOKIE = "ds_tt_state"


def tiktok_redirect_uri():
    return os.environ.get("TIKTOK_REDIRECT_URI", f"https://{ADMIN_DOMAIN}/tiktok/callback")


@app.get("/comptes", response_class=HTMLResponse)
def accounts_page(request: Request):
    yt = (STATE_DIR / os.environ.get("YOUTUBE_TOKEN_FILE", "state/youtube_token.json").split("/")[-1]).exists()
    ig = os.environ.get("IG_USER_ID", "") not in ("", "...")
    fb = os.environ.get("FB_PAGE_ID", "") not in ("", "...")
    tt = stats.tiktok() if TT_TOKEN.exists() else {"error": "non connecté"}
    return page(request, "accounts", yt=yt, ig=ig, fb=fb, tt=tt, tt_connected=TT_TOKEN.exists(),
                tt_ready=bool(os.environ.get("TIKTOK_CLIENT_KEY")) and os.environ.get("TIKTOK_CLIENT_KEY") != "...",
                tt_scopes=TT_SCOPES.split(","), tt_redirect=tiktok_redirect_uri())


@app.get("/tiktok/connect")
def tiktok_connect():
    """Étape 1 : envoie vers l'écran d'autorisation de TikTok (Login Kit)."""
    key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    if not key or key == "...":
        return back("/comptes", "TIKTOK_CLIENT_KEY manque dans .env.")
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(TT_AUTH + "?" + urlencode({
        "client_key": key, "scope": TT_SCOPES, "response_type": "code",
        "redirect_uri": tiktok_redirect_uri(), "state": state}), status_code=303)
    resp.set_cookie(TT_STATE_COOKIE, state, max_age=600, httponly=True, secure=SECURE_COOKIE, samesite="lax", path="/tiktok")
    return resp


@app.get("/tiktok/callback")
def tiktok_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    """Étape 2 : TikTok revient ici avec un code, échangé contre le jeton (enregistré dans state/)."""
    expected = request.cookies.get(TT_STATE_COOKIE, "")
    if error:
        return back("/comptes", f"Connexion TikTok annulée : {error_description or error}")
    if not code or not expected or not hmac.compare_digest(state, expected):
        return back("/comptes", "Connexion TikTok refusée : lien expiré ou invalide, recommence.")
    r = requests.post(f"{TT_API}/oauth/token/", timeout=60, headers={"Content-Type": "application/x-www-form-urlencoded"},
                      data={"client_key": os.environ["TIKTOK_CLIENT_KEY"],
                            "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET", ""), "code": code,
                            "grant_type": "authorization_code", "redirect_uri": tiktok_redirect_uri()})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or "access_token" not in d:
        return back("/comptes", f"Échec de l'échange du code TikTok : {d.get('error_description') or r.text[:200]}")
    tok = {"access_token": d["access_token"], "refresh_token": d["refresh_token"],
           "expires_at": time.time() + d["expires_in"], "open_id": d.get("open_id"), "scope": d.get("scope", "")}
    store._write(TT_TOKEN, tok)
    stats._cache.clear()
    resp = back("/comptes", "Compte TikTok connecté.")
    resp.delete_cookie(TT_STATE_COOKIE, path="/tiktok")
    return resp


@app.post("/tiktok/disconnect")
def tiktok_disconnect():
    """Révoque l'autorisation chez TikTok et supprime le jeton du serveur."""
    try:
        tok = store._read(TT_TOKEN, {})
        if tok.get("access_token"):
            requests.post(f"{TT_API}/oauth/revoke/", timeout=30,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={"client_key": os.environ.get("TIKTOK_CLIENT_KEY", ""),
                                "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET", ""),
                                "token": tok["access_token"]})
    except requests.RequestException:
        pass
    TT_TOKEN.unlink(missing_ok=True)
    stats._cache.clear()
    return back("/comptes", "Compte TikTok déconnecté et autorisation révoquée.")


@app.get("/journal", response_class=HTMLResponse)
def journal(request: Request):
    return page(request, "journal", log=log_tail(400))


# Déclarée en dernier : cette route attrape tout chemin à un seul segment
@app.get("/{name}", include_in_schema=False)
def verification_file(name: str):
    if not VERIFY_RE.match("/" + name):
        raise HTTPException(404)
    f = store.STATE / "verify" / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="text/html" if name.endswith(".html") else "text/plain")
