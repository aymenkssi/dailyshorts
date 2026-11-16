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
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

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
CHANNEL_NAME = os.environ.get("CHANNEL_NAME", "Forgotten Chapters")
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
    ctx.update(csrf=csrf_token(request), llm_url=f"https://{LLM_DOMAIN}", active=name,
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
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": ""})


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
            "next": next, "error": "Trop d'essais. Réessaie dans 15 minutes."}, status_code=429)
    if not (SECRET and PASSWORD_HASH and verify_password(password)):
        _failures.setdefault(ip, []).append(time.time())
        time.sleep(1)
        return templates.TemplateResponse(request, "login.html", {
            "next": next, "error": "Mot de passe incorrect."}, status_code=401)
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


@app.get("/reglages", response_class=HTMLResponse)
def settings_page(request: Request):
    return page(request, "settings", s=store.get_settings())


@app.post("/reglages")
async def settings_save(request: Request):
    form = await request.form()
    values = {k: form.get(k, "") for k in store.SCHEMA}
    for k, (_, _, kind) in store.SCHEMA.items():
        if kind == "bool":
            values[k] = k in form  # case décochée = absente du formulaire
    try:
        store.save_settings(values)
        return back("/reglages", "Réglages enregistrés. Ils sont pris en compte dans la minute.")
    except (ValueError, TypeError) as e:
        return back("/reglages", f"Réglage refusé — {e}")


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    rows, ids = [], []
    for d in store.list_days(30):
        st = store.day_status(d)
        row = {"day": d, "title": st["title"],
               "long": stats.youtube_id(st["published"].get("youtube_long")),
               "short": stats.youtube_id(st["published"].get("youtube_short"))}
        if row["long"] or row["short"]:
            rows.append(row)
            ids += [i for i in (row["long"], row["short"]) if i]
    return page(request, "stats", yt=stats.youtube(ids), ig=stats.instagram(), tt=stats.tiktok(), rows=rows)


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
