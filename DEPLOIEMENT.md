# Déploiement sur le VPS

Cible : Ubuntu 22.04 ou 24.04, 2 Go de RAM minimum, Docker installé. Tout tourne sur le même VPS ; FreeLLMAPI n'écoute que sur 127.0.0.1.

## Étape 1 — Installer FreeLLMAPI (routeur de LLM gratuits)

```bash
cd /opt
sudo git clone https://github.com/tashfeenahmed/freellmapi.git
cd freellmapi
printf "ENCRYPTION_KEY=%s\nPORT=3001\n" "$(openssl rand -hex 32)" | sudo tee .env >/dev/null
sudo chmod 600 .env
sudo docker compose up -d
sudo docker compose logs freellmapi | grep -i "setup code"     # code de première connexion
```

Garde une copie de `ENCRYPTION_KEY` hors du VPS : sans elle, les clés enregistrées sont illisibles.
Ne mets pas `HOST_BIND=0.0.0.0` : le port 3001 doit rester local.

## Étape 2 — Configurer FreeLLMAPI depuis ton PC

1. Tunnel SSH depuis ton PC : `ssh -L 3001:127.0.0.1:3001 user@ton-vps`
2. Ouvre http://localhost:3001, crée le compte admin (code de l'étape 1).
3. Page **Keys** : ajoute les clés gratuites que tu veux (plus il y en a, plus c'est fiable). Les plus utiles pour écrire en anglais : Google AI Studio (Gemini), Groq, Cerebras, Mistral, OpenRouter, NVIDIA (sert aussi les images Flux de secours), Cloudflare.
4. Copie la **clé unifiée** `freellmapi-…` affichée en haut de la page Keys.
5. Facultatif : page **Playground**, teste `auto:smart` et `fusion`. Page **Fusion**, choisis 3 modèles solides + un juge.

## Étape 3 — Installer le pipeline vidéo

```bash
sudo apt install -y ffmpeg espeak-ng python3-venv
sudo git clone https://github.com/aymenkssi/dailyshorts.git /opt/daily_shorts
sudo chown -R $USER /opt/daily_shorts
cd /opt/daily_shorts
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt
mkdir -p state && cp .env.example .env && chmod 600 .env && nano .env
```

Dans `.env` : `LLM_API_KEY` (clé unifiée), `CF_ACCOUNT_ID`, `CF_API_TOKEN` (dash.cloudflare.com → AI → Workers AI), puis les clés de publication (étape 5).

## Étape 4 — Vérifier puis générer une vidéo de test

```bash
.venv/bin/python tools/check_services.py     # chaque ligne doit être [OK]
.venv/bin/python main.py --no-publish        # → output/<date>/final.mp4
```

Le premier lancement télécharge le modèle de voix Kokoro (~330 Mo). Récupère la vidéo pour la regarder :
`scp user@ton-vps:/opt/daily_shorts/output/<date>/final.mp4 .`

## Étape 5 — Brancher les réseaux sociaux

- **YouTube** : Google Cloud Console → activer *YouTube Data API v3* → client OAuth « Application de bureau » → JSON dans `state/youtube_client_secret.json`. Écran de consentement en **« En production »** (sinon le jeton expire tous les 7 jours). Sur ton PC : `python tools/youtube_auth.py`, puis copie `state/youtube_token.json` sur le VPS.
- **Instagram** : compte Pro/Créateur + app Meta « API Instagram avec connexion Instagram » (permission `instagram_business_content_publish`) → `IG_USER_ID`, `IG_ACCESS_TOKEN`. Dans la config nginx de aymendridi.com :
  ```nginx
  location /shorts/ { alias /var/www/aymendridi.com/shorts/; }
  ```
  puis `sudo mkdir -p /var/www/aymendridi.com/shorts && sudo chown $USER /var/www/aymendridi.com/shorts && sudo nginx -t && sudo systemctl reload nginx`.
- **TikTok** : app sur developers.tiktok.com, *Content Posting API* (Direct Post), scope `video.publish`, URL de redirection déclarée → `.venv/bin/python tools/tiktok_auth.py`. Demande l'audit tout de suite : sans lui, les vidéos restent privées.

Désactive une plateforme pas encore prête avec `PUBLISH_xxx=false`.

## Étape 6 — Publication automatique chaque jour

```bash
.venv/bin/python main.py          # premier passage réel (YouTube en privé par défaut)
crontab -e
```

```
CRON_TZ=Europe/Paris
47 16 * * * cd /opt/daily_shorts && .venv/bin/python main.py >> output/cron.log 2>&1
```

Après quelques jours de relecture, passe `YOUTUBE_PRIVACY=public`.

## Entretien

- Suivi : `tail -f /opt/daily_shorts/output/cron.log` ; quel modèle a écrit quoi : lignes « texte généré via … ».
- Relancer un jour raté : `.venv/bin/python main.py --date AAAA-MM-JJ` (les étapes faites sont sautées).
- Mettre à jour le pipeline : `cd /opt/daily_shorts && git pull && .venv/bin/pip install -r requirements.txt`.
- Mettre à jour FreeLLMAPI : `cd /opt/freellmapi && sudo docker compose pull && sudo docker compose up -d`.
- Si FreeLLMAPI est arrêté, le pipeline bascule seul sur Cloudflare pour le texte.
