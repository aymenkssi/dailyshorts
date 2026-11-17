"""À lancer UNE fois : autorise ton app TikTok et enregistre state/tiktok_token.json.

L'URL de redirection doit être déclarée dans ton app TikTok (ex. https://aymendridi.com/tiktok-callback) ;
la page n'a pas besoin d'exister : tu copies simplement l'URL affichée par le navigateur après autorisation.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
redirect = sys.argv[1] if len(sys.argv) > 1 else "https://aymendridi.com/tiktok-callback"
key, secret = os.environ["TIKTOK_CLIENT_KEY"], os.environ["TIKTOK_CLIENT_SECRET"]

url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
    "client_key": key, "scope": "user.info.basic,user.info.stats,video.list,video.publish", "response_type": "code",
    "redirect_uri": redirect, "state": secrets.token_urlsafe(8)})
print("1) Ouvre cette URL et autorise l'app :\n", url)
back = input("2) Colle ici l'URL complète vers laquelle tu as été redirigé : ").strip()
code = parse_qs(urlparse(back).query)["code"][0]

r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", data={
    "client_key": key, "client_secret": secret, "code": code,
    "grant_type": "authorization_code", "redirect_uri": redirect}, timeout=60)
r.raise_for_status()
d = r.json()
tok = {"access_token": d["access_token"], "refresh_token": d["refresh_token"],
       "expires_at": time.time() + d["expires_in"], "open_id": d.get("open_id")}
out = ROOT / os.environ.get("TIKTOK_TOKEN_FILE", "state/tiktok_token.json")
out.write_text(json.dumps(tok, indent=1))
print(f"Jeton enregistré : {out}")
