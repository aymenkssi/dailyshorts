# Guide de déploiement — Docker + Traefik (comme Waselni)

Daily Shorts se déploie comme Waselni : une pile **Docker Compose** derrière le **Traefik** déjà en place sur le VPS (réseau externe `web`, entrée `websecure`, certificats `letsencrypt`).

| Conteneur | Rôle | Exposition |
|---|---|---|
| `dailyshorts-freellmapi` | routeur de LLM gratuits | `llm.daily.creationapp.academy` (après connexion à l'admin) et `127.0.0.1:3001` |
| `dailyshorts-pipeline` | crée chaque nuit à 1 h une vidéo longue (4 min) et un Short d'accroche, puis les publie à 16 h 47 (heure de Paris) (heure de Paris) | aucune |
| `dailyshorts-admin` | interface d'administration sur `daily.creationapp.academy` | via Traefik, mot de passe |
| `dailyshorts-media` | sert `https://aymendridi.com/shorts/*.mp4` pour Instagram | via Traefik |

Les commandes se lancent sur le VPS, sauf les blocs marqués **(PC)**. `user@IP_VPS` = ton accès SSH.

---

## 1. Vérifier le VPS

```bash
docker compose version                    # Compose v2
docker network ls | grep -w web           # le réseau de Traefik utilisé par Waselni
docker ps --format '{{.Names}}' | grep -i traefik
free -h                                   # 4 Go conseillés (voix + build)
df -h /                                   # ~10 Go libres (image ~2 Go + vidéos)
```

S'il manque de la RAM (moins de 4 Go), ajoute du swap :

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Le domaine `aymendridi.com` doit déjà être routé par Traefik (portfolio). Sinon, ajoute un enregistrement DNS A vers le VPS : Traefik obtiendra le certificat tout seul au premier accès.

---

## 2. Récupérer le projet et préparer les secrets

```bash
sudo git clone https://github.com/aymenkssi/dailyshorts.git /opt/dailyshorts
sudo chown -R $USER:$USER /opt/dailyshorts
cd /opt/dailyshorts

# Dossiers montés dans le conteneur (il tourne avec l'UID 1000, pas en root)
mkdir -p state output public
sudo chown -R 1000:1000 state output public

# Clé de chiffrement de FreeLLMAPI
printf "ENCRYPTION_KEY=%s\n" "$(openssl rand -hex 32)" > freellmapi.env
chmod 600 freellmapi.env
cat freellmapi.env        # ⚠️ copie la clé dans ton gestionnaire de mots de passe

# Configuration du pipeline
cp .env.example .env && chmod 600 .env
```

Comme pour Waselni, les secrets sont dans des fichiers `.env` à côté du `docker-compose.yml` : ils sont exclus de Git, ne les committe jamais.

---

## 3. Démarrer FreeLLMAPI et le configurer

```bash
docker compose up -d freellmapi
docker compose ps                                          # "healthy" après ~30 s
docker compose logs freellmapi | grep -i "setup code"      # code de première connexion
```

### 3a. Créer des clés gratuites (navigateur)

| Fournisseur | Où créer la clé |
|---|---|
| Groq | console.groq.com/keys |
| Cerebras | cloud.cerebras.ai → API Keys |
| Mistral | console.mistral.ai → API Keys (plan gratuit « Experiment ») |
| OpenRouter | openrouter.ai/settings/keys |
| NVIDIA (LLM + images de secours) | build.nvidia.com → Get API Key |
| Google AI Studio (Gemini) | aistudio.google.com/apikey — l'offre gratuite est restreinte pour les services destinés à l'UE, à toi de juger |

### 3b. Ouvrir le tableau de bord

**(PC)** — laisse la fenêtre ouverte :

```bash
ssh -L 3001:127.0.0.1:3001 user@IP_VPS
```

Puis http://localhost:3001 :

1. Crée le compte admin avec le *setup code*.
2. Page **Keys** : colle chaque clé fournisseur.
3. Copie la **clé unifiée** `freellmapi-…` (en haut de la page Keys).
4. **Playground** : teste `auto:smart`.
5. **Fusion** : choisis 3 modèles de fournisseurs différents + un juge.

---

## 4. Cloudflare Workers AI (images)

dash.cloudflare.com → **AI → Workers AI → Use REST API** : copie l'**Account ID**, puis **Create a Workers AI API Token** (affiché une seule fois). Gratuit, sans carte bancaire.

---

## 5. Remplir `.env`

```bash
nano /opt/dailyshorts/.env
```

| Variable | Valeur |
|---|---|
| `SHORTS_DOMAIN` | `aymendridi.com` |
| `LLM_API_KEY` | clé unifiée de l'étape 3b |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | étape 4 |
| `GENERATE_AT` | `01:00` : création des vidéos (heure de Paris) |
| `PUBLISH_AT` | `16:47` : publication le même jour |
| `PUBLISH_*` | `false` pour l'instant |

`LLM_BASE_URL` et `PUBLIC_VIDEO_DIR` sont fixés par `docker-compose.yml`, ne les ajoute pas.

---

## 6. Construire et démarrer la pile

```bash
cd /opt/dailyshorts
docker compose up -d --build        # 1er build : 5 à 15 min (torch, Kokoro)
docker compose ps                   # 3 conteneurs "running"
docker compose logs -f shorts       # "planificateur démarré … prochaine vidéo : …" — Ctrl+C pour quitter
```

Vérifier que la route Traefik répond :

```bash
echo test | sudo tee public/test.mp4 >/dev/null
curl -I https://aymendridi.com/shorts/test.mp4    # 200 + Content-Type: video/mp4
sudo rm public/test.mp4
```

---

## 7. Tester sans publier

```bash
docker compose exec shorts python tools/check_services.py     # toutes les lignes [OK]
docker compose exec shorts python main.py --no-publish        # 20 à 40 min (vidéo longue + Short)
```

Le premier passage télécharge le modèle de voix (~330 Mo, gardé dans le volume `kokoro_cache`).

**(PC)** Récupère la vidéo :

```bash
scp user@IP_VPS:/opt/dailyshorts/output/<date>/long/final.mp4 longue.mp4
scp user@IP_VPS:/opt/dailyshorts/output/<date>/short/final.mp4 short.mp4
```

Vérifie la synchronisation voix/sous-titres, les images et les faits (`output/<date>/scripts.json`). Pour refaire le même jour : `sudo rm -rf output/<date>`.

---

## 8. YouTube

**Navigateur** — console.cloud.google.com :

1. **Nouveau projet** → **API et services → Bibliothèque** → *YouTube Data API v3* → **Activer**.
2. **Google Auth Platform → Commencer** : nom, e-mail, audience **Externe**.
3. **Accès aux données → Ajouter des champs d'application** → `.../auth/youtube.upload`.
4. **Audience → Publier l'application** (« En production » ; sinon le jeton expire tous les 7 jours).
5. **Clients → Créer un client → Application de bureau** → **Télécharger le JSON**.

**(PC)** — une seule fois, il faut Python :

```bash
git clone https://github.com/aymenkssi/dailyshorts.git && cd dailyshorts
python -m pip install google-auth-oauthlib
python tools/youtube_auth.py CHEMIN/VERS/client_secret_xxx.json
```

Choisis le compte **de la chaîne** → « Google n'a pas validé cette application » → **Paramètres avancés → Accéder à … → Continuer**. Puis :

```bash
scp state/youtube_token.json user@IP_VPS:/tmp/
```

**VPS** :

```bash
sudo install -o 1000 -g 1000 -m 600 /tmp/youtube_token.json /opt/dailyshorts/state/ && rm /tmp/youtube_token.json
```

---

## 9. Instagram

1. Appli Instagram : **Paramètres → Type de compte et outils** → compte **professionnel**, compte **public**.
2. developers.facebook.com → **Créer une application** → cas d'utilisation **Autre** → type **Entreprise**.
3. Produit **Instagram → Configurer** → **Générer des jetons d'accès → Ajouter un compte** → connecte ton compte, accepte la publication de contenu → **Générer un jeton**.
4. Identifiant du compte :

   ```bash
   curl -s "https://graph.instagram.com/v23.0/me?fields=user_id,username&access_token=TON_JETON"
   ```

5. Dans `.env` : `IG_ACCESS_TOKEN`, `IG_USER_ID` (= `user_id`), `PUBLIC_VIDEO_BASE_URL=https://aymendridi.com/shorts`.

Le pipeline rafraîchit le jeton chaque semaine. Si Meta exige l'*App Review* pour publier, soumets la permission `instagram_business_content_publish`.

---

## 10. TikTok

**Navigateur** — developers.tiktok.com → **Manage apps → Connect an app** :

1. Icône, nom, catégorie, description ; plateforme **Web** : `https://aymendridi.com`. Pour l'audit, prévois une page de conditions d'utilisation et une page de confidentialité sur ton site.
2. **Login Kit** → Redirect URI `https://aymendridi.com/tiktok-callback` (la page n'a pas besoin d'exister).
3. **Content Posting API** → active **Direct Post**.
4. Scopes : `user.info.basic`, `video.publish`.
5. **Client key / Client secret** → `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` dans `.env`, puis `docker compose up -d shorts`.
6. En mode **Sandbox**, ajoute ton compte TikTok aux utilisateurs de test.
7. **Mode de publication** (page **Réglages** de l'admin) :
   - **Brouillon** (par défaut, scope `video.upload`) : chaque vidéo arrive dans ta boîte de réception TikTok ; tu la publies en public depuis l'appli, en collant la légende affichée sur la page de la journée dans l'admin. Fonctionne sans validation de l'appli. TikTok limite à 5 brouillons en attente par 24 h.
   - **Direct** (scope `video.publish`) : sans validation de l'appli par TikTok, accepté seulement sur un compte privé, et les vidéos restent visibles par toi seul, même après la validation. La validation exige un écran de relecture avant chaque publication.

**VPS** — autoriser ton compte :

```bash
docker compose run --rm shorts python tools/tiktok_auth.py
```

Ouvre l'URL affichée, autorise, puis colle **vite** l'URL complète de la page `tiktok-callback?code=…` (le code expire en quelques minutes).

---

## 11. Activer la publication

Dans `.env` : `PUBLISH_YOUTUBE=true` (et les autres quand ils sont prêts), puis :

```bash
docker compose up -d shorts                               # recharge le .env
docker compose exec shorts python main.py                 # premier passage réel
cat output/$(TZ=Europe/Paris date +%F)/published.json     # youtube_long, youtube_short, instagram, tiktok : "OK …"
```

En cas d'erreur sur une plateforme : corrige et relance la même commande (la vidéo n'est pas regénérée, les plateformes réussies sont sautées).

À partir de là, le conteneur crée les vidéos **chaque nuit à `GENERATE_AT`** et les publie **à `PUBLISH_AT`**. Il rattrape une étape manquée s'il redémarre après l'heure prévue, réessaie deux fois en cas d'échec (toutes les 30 min), supprime les dossiers de travail de plus de 30 jours et les vidéos publiques de plus de 7 jours.

Après quelques jours de relecture : `YOUTUBE_PRIVACY=public`, puis `docker compose up -d shorts`.

---

## Interface d'administration

Deux adresses, servies par Traefik :

| Adresse | Contenu | Protection |
|---|---|---|
| `https://daily.creationapp.academy` | tableau de bord, vidéos, validation, file de sujets, réglages, statistiques, journal | mot de passe de l'admin |
| `https://llm.daily.creationapp.academy` | tableau de bord FreeLLMAPI complet (clés des fournisseurs) | connexion à l'admin **puis** mot de passe FreeLLMAPI |

### Mise en place

1. **DNS** : crée deux enregistrements A vers l'adresse IP du VPS : `daily.creationapp.academy` et `llm.daily.creationapp.academy`. Vérifie avec `dig +short daily.creationapp.academy`.
2. **Mot de passe** (12 caractères minimum, seule son empreinte est stockée) :

   ```bash
   cd /opt/dailyshorts
   git pull
   python3 tools/admin_password.py        # crée admin.env
   ```

3. **Démarrage** :

   ```bash
   docker compose up -d --build
   docker compose ps                      # 4 conteneurs : freellmapi, shorts, admin, media
   ```

4. Ouvre `https://daily.creationapp.academy` (le certificat est obtenu au premier accès, compte quelques secondes).

### Ce que pilote l'admin

- **Réglages** : heures de création et de publication, durées, plateformes actives, visibilité YouTube, validation obligatoire, ligne éditoriale, voix. Ils sont enregistrés dans `state/settings.json`, pris en compte dans la minute, et **priment sur le `.env`**.
- **Sujets** : file de sujets imposés ; file vide = l'IA choisit.
- **Récapitulatif de l'actualité** : chaque dimanche (réglable), la vidéo d'histoire est remplacée par un récapitulatif mondial de 6 minutes et son Short, à partir des pages « Current events » de Wikipédia des 6 jours précédents. Le serveur doit pouvoir joindre `en.wikipedia.org`.
- **Validation** : si elle est exigée, les vidéos créées la nuit attendent ton approbation ; sans approbation le jour même, rien n'est publié. Boutons : Approuver, Publier maintenant, Refuser, Refaire.
- **Statistiques** : une ligne par vidéo publiée, avec son format (Longue / Short). YouTube demande l'autorisation avec le droit de lecture (`tools/youtube_auth.py`). TikTok demande les scopes `user.info.stats` (totaux) et `video.list` (détail par vidéo, vidéos publiques uniquement) dans ton appli TikTok, puis `tools/tiktok_auth.py`. Pour les vues Instagram, ajoute la permission `instagram_manage_insights` à la configuration Meta et génère un nouveau jeton ; sans elle, les J'aime et commentaires s'affichent quand même.

### Sécurité

- Changer le mot de passe : relance `python3 tools/admin_password.py` puis `docker compose up -d admin`.
- 5 mots de passe faux bloquent l'adresse IP pendant 15 minutes ; la session dure 12 heures.
- FreeLLMAPI en ligne expose l'outil qui détient tes clés de fournisseurs : garde un mot de passe FreeLLMAPI différent de celui de l'admin. Pour le retirer d'Internet, supprime le bloc `labels` du service `freellmapi` dans `docker-compose.yml` ; le tunnel SSH (`ssh -L 3001:127.0.0.1:3001 …`) continue de fonctionner.

---

## Entretien

| Action | Commande (dans `/opt/dailyshorts`) |
|---|---|
| Suivre le pipeline | `docker compose logs -f shorts` |
| État des conteneurs | `docker compose ps` |
| Relancer un jour | `docker compose exec shorts python main.py --date AAAA-MM-JJ` |
| Changer les heures, les plateformes, la voix | page **Réglages** de l'admin |
| Mettre en pause | `docker compose stop shorts` |
| Mettre à jour le pipeline | `git pull && docker compose up -d --build shorts` |
| Mettre à jour FreeLLMAPI | `docker compose pull freellmapi && docker compose up -d freellmapi` |
| Sauvegarder | `.env`, `freellmapi.env`, `state/`, et le volume FreeLLMAPI : `docker run --rm -v dailyshorts_freellmapi_data:/d -v $PWD:/b alpine tar czf /b/freellmapi-data.tgz -C /d .` |

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `network web declared as external, but could not be found` | réseau Traefik absent ou nommé autrement | `docker network ls` ; adapter le nom dans `docker-compose.yml` |
| `curl` sur `/shorts/` renvoie 404 Traefik | route non prise | `docker compose logs media` ; vérifier `SHORTS_DOMAIN` et l'entrée `websecure` de ton Traefik |
| `Permission denied` sur `state/` ou `output/` | dossiers pas à l'UID 1000 | `sudo chown -R 1000:1000 state output public` |
| FreeLLMAPI KO dans `check_services` | conteneur pas « healthy » | `docker compose logs freellmapi` |
| FreeLLMAPI 401 | mauvaise clé | recopier la clé unifiée dans `LLM_API_KEY`, `docker compose up -d shorts` |
| « texte généré via cloudflare » | modèles FreeLLMAPI saturés ou indisponibles | ajouter des fournisseurs dans la page Keys |
| Build ou voix tués (`Killed`) | manque de RAM | swap (étape 1) |
| YouTube `invalid_grant` | jeton révoqué ou appli restée en « Test » | étape 8.4 puis refaire l'autorisation |
| Instagram `ERROR` | URL vidéo injoignable | refaire le test `curl -I` de l'étape 6 |
| TikTok `unaudited_client_can_only_post_to_private_accounts` | audit non validé | compte TikTok en privé ou `PUBLISH_TIKTOK=false` |
