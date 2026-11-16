"""Vérifie le jeton Instagram et affiche l'identifiant du compte à mettre dans IG_USER_ID.

Sur le VPS :  docker compose run --rm shorts python tools/instagram_check.py
(lit IG_ACCESS_TOKEN, IG_GRAPH_HOST et IG_GRAPH_VERSION dans .env ; ne publie rien)
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
token = os.environ.get("IG_ACCESS_TOKEN", "")
host = os.environ.get("IG_GRAPH_HOST", "graph.facebook.com")
base = f"https://{host}/{os.environ.get('IG_GRAPH_VERSION', 'v23.0')}"
if not token or token == "...":
    sys.exit("IG_ACCESS_TOKEN est vide dans .env")


def get(path, **params):
    r = requests.get(f"{base}/{path}", params={**params, "access_token": token}, timeout=30)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


print(f"Hôte : {host}")
found = []
if host == "graph.instagram.com":
    code, d = get("me", fields="user_id,username")
    print(f"me → {code} {d}")
    if code == 200:
        found.append((d.get("user_id"), d.get("username")))
else:
    # connexion unifiée : comptes Instagram attribués au jeton du portefeuille business
    for path in ("me/assigned_instagram_accounts", "me/instagram_accounts"):
        code, d = get(path, fields="id,username")
        print(f"{path} → {code} {d if code != 200 else ''}")
        if code == 200:
            found += [(a.get("id"), a.get("username")) for a in d.get("data", [])]
        if found:
            break

if not found:
    sys.exit("Aucun compte Instagram trouvé avec ce jeton : vérifie le jeton, ses permissions et le compte attribué.")
for uid, name in found:
    print(f"\nCompte : @{name}\nÀ mettre dans .env :  IG_USER_ID={uid}")
    code, d = get(str(uid), fields="username,followers_count,media_count")
    print(f"Lecture du profil → {code} {d}")
    code, d = get(f"{uid}/content_publishing_limit", fields="quota_usage,config")
    print(f"Droit de publication → {code} {d}")
