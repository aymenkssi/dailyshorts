"""Vérifie le jeton Facebook et affiche l'identifiant de la Page à mettre dans FB_PAGE_ID.

Sur le VPS :  docker compose run --rm shorts python tools/facebook_check.py
(lit FB_ACCESS_TOKEN, ou à défaut IG_ACCESS_TOKEN, dans .env ; ne publie rien)
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
token = os.environ.get("FB_ACCESS_TOKEN") or os.environ.get("IG_ACCESS_TOKEN", "")
base = f"https://graph.facebook.com/{os.environ.get('FB_GRAPH_VERSION') or os.environ.get('IG_GRAPH_VERSION', 'v23.0')}"
if not token or token == "...":
    sys.exit("FB_ACCESS_TOKEN (ou IG_ACCESS_TOKEN) est vide dans .env")
NEEDED = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}


def get(path, **params):
    r = requests.get(f"{base}/{path}", params={**params, "access_token": token}, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


code, d = get("me/permissions")
if code == 200:
    granted = {p["permission"] for p in d.get("data", []) if p.get("status") == "granted"}
    missing = NEEDED - granted
    print("Permissions accordées :", ", ".join(sorted(granted)) or "aucune")
    if missing:
        print("À AJOUTER au jeton :", ", ".join(sorted(missing)))
    if "read_insights" not in granted:
        print("Conseillé pour les vues dans les statistiques : read_insights")
else:
    print(f"me/permissions → {code} (normal pour un jeton de Page)")

code, d = get("me/accounts", fields="id,name,tasks")
pages = d.get("data", []) if code == 200 else []
if not pages:
    code2, me = get("me", fields="id,name")
    if code2 == 200 and os.environ.get("FB_PAGE_ID") == me.get("id"):
        pages = [me]
if not pages:
    sys.exit(f"me/accounts → {code} {d}\nAucune Page trouvée : dans le portefeuille business, attribue la Page "
             "à l'utilisateur système (contrôle total), puis génère un nouveau jeton avec les permissions des Pages.")

for p in pages:
    print(f"\nPage : {p.get('name')}\nÀ mettre dans .env :  FB_PAGE_ID={p.get('id')}")
    if p.get("tasks"):
        ok = "CREATE_CONTENT" in p["tasks"]
        print(f"Droit de publier (CREATE_CONTENT) : {'oui' if ok else 'NON'}")
    c, pt = get(p["id"], fields="access_token")
    page_token = pt.get("access_token") if c == 200 else None
    print("Jeton de Page :", "obtenu" if page_token else f"non obtenu ({c})")
    if page_token:
        r = requests.get(f"{base}/{p['id']}", timeout=30,
                         params={"fields": "name,followers_count", "access_token": page_token})
        print(f"Lecture de la Page → {r.status_code} {r.json()}")
