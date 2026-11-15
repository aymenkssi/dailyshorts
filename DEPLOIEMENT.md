# Guide de déploiement détaillé

Tout tourne sur ton VPS : **FreeLLMAPI** (routeur de LLM gratuits, dans Docker, accessible seulement en local) et **le pipeline** (Python, lancé chaque jour par un timer systemd).

| Partie | Temps estimé |
|---|---|
| 1-2. Préparer le VPS + installer FreeLLMAPI | 20 min |
| 3-4. Clés gratuites (LLM + Cloudflare) | 30 min |
| 5-7. Installer le pipeline + première vidéo de test | 30 min |
| 8-10. YouTube, Instagram, TikTok | 1 h à 2 h (+ délai d'audit TikTok) |
| 11-12. Première publication + automatisation | 15 min |

Dans les commandes : `user@IP_VPS` = ton compte SSH sur le VPS. Les blocs marqués **(PC)** se lancent sur ton ordinateur, les autres sur le VPS.

---

## 1. Préparer le VPS

**Vérifier le système** (Ubuntu 22.04 ou 24.04 recommandé) :

```bash
lsb_release -d            # version d'Ubuntu
python3 --version         # doit être 3.10, 3.11 ou 3.12 (Kokoro ne supporte pas 3.13)
free -h                   # RAM
df -h /                   # au moins 10 Go libres
```

**Mettre à jour et installer les paquets** :

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git ffmpeg espeak-ng python3-venv docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo docker compose version     # doit afficher une version v2
```

**Ajouter de la mémoire virtuelle si le VPS a moins de 4 Go de RAM** (l'installation de torch et la voix en ont besoin) :

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

> Si `python3 --version` affiche 3.13 ou plus : installe Python 3.12 (`sudo apt install python3.12 python3.12-venv`, ou via le PPA deadsnakes) et remplace `python3` par `python3.12` à l'étape 5.

---

## 2. Installer FreeLLMAPI

```bash
sudo git clone https://github.com/tashfeenahmed/freellmapi.git /opt/freellmapi
cd /opt/freellmapi
printf "ENCRYPTION_KEY=%s\nPORT=3001\n" "$(openssl rand -hex 32)" | sudo tee .env >/dev/null
sudo chmod 600 .env
sudo cat .env        # ⚠️ copie ENCRYPTION_KEY dans ton gestionnaire de mots de passe
sudo docker compose up -d
```

Vérifier :

```bash
sudo docker compose ps                                    # état "healthy" après ~30 s
ss -ltnp | grep 3001                                      # doit montrer 127.0.0.1:3001, PAS 0.0.0.0
sudo docker compose logs freellmapi | grep -i "setup code" # note ce code pour l'étape 3
```

Ne mets jamais `HOST_BIND=0.0.0.0` : le service contiendra toutes tes clés et ne doit pas être exposé sur Internet.

---

## 3. Clés LLM gratuites + configuration de FreeLLMAPI

### 3a. Créer des clés gratuites

Plus tu en ajoutes, plus les scripts sont bons et fiables. Toutes sont gratuites, sans carte bancaire :

| Fournisseur | Où créer la clé | Intérêt |
|---|---|---|
| Google AI Studio (Gemini) | aistudio.google.com/apikey | très bon en écriture (voir la note ci-dessous) |
| Groq | console.groq.com/keys | très rapide |
| Cerebras | cloud.cerebras.ai → API Keys | très rapide, gros modèles |
| Mistral | console.mistral.ai → API Keys (plan gratuit « Experiment ») | bon en anglais |
| OpenRouter | openrouter.ai/settings/keys | accès à de nombreux modèles `:free` |
| NVIDIA | build.nvidia.com → Get API Key | LLM + **images Flux de secours** |

> Note Gemini : les conditions de l'offre gratuite de Google limitent son usage pour des services destinés à l'UE. Pour un usage personnel c'est à ton appréciation ; tu peux t'en passer, les autres suffisent.

### 3b. Ouvrir le tableau de bord via un tunnel SSH

**(PC)** — laisse cette fenêtre ouverte :

```bash
ssh -L 3001:127.0.0.1:3001 user@IP_VPS
```

Puis ouvre **http://localhost:3001** dans ton navigateur.

### 3c. Configurer

1. **Créer le compte admin** : e-mail + mot de passe ; entre le *setup code* noté à l'étape 2.
2. **Page Keys** : clique sur chaque fournisseur et colle sa clé.
3. **Clé unifiée** : copie la clé `freellmapi-…` affichée en haut de la page Keys → elle ira dans `LLM_API_KEY`.
4. **Page Playground** : choisis le modèle `auto:smart`, envoie « Hello » → une réponse doit arriver.
5. **Page Fusion** (recommandé) : choisis 3 modèles solides de fournisseurs différents pour le panel et un bon modèle comme juge.

---

## 4. Cloudflare Workers AI (images)

1. Crée un compte gratuit sur dash.cloudflare.com (pas de carte bancaire nécessaire).
2. Menu **AI → Workers AI** → **Use REST API**.
3. Copie l'**Account ID** → `CF_ACCOUNT_ID`.
4. Clique **Create a Workers AI API Token** → copie le jeton (affiché une seule fois) → `CF_API_TOKEN`.

Quota gratuit : 10 000 « neurons »/jour, soit environ une vidéo. Au-delà, les images basculent automatiquement sur FreeLLMAPI.

---

## 5. Installer le pipeline

```bash
sudo git clone https://github.com/aymenkssi/dailyshorts.git /opt/daily_shorts
sudo chown -R $USER:$USER /opt/daily_shorts
cd /opt/daily_shorts
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu   # version CPU (~200 Mo au lieu de ~2 Go)
.venv/bin/pip install -r requirements.txt
```

---

## 6. Remplir la configuration

```bash
cp .env.example .env && chmod 600 .env && nano .env
```

Pour l'instant, renseigne seulement :

| Variable | Valeur |
|---|---|
| `LLM_API_KEY` | clé unifiée `freellmapi-…` (étape 3c) |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | étape 4 |
| `KOKORO_VOICE` | `am_michael` par défaut ; essaie aussi `bm_george` (voix britannique) |
| `PUBLISH_YOUTUBE`, `PUBLISH_INSTAGRAM`, `PUBLISH_TIKTOK` | `false` pour le moment |

Laisse les autres valeurs par défaut.

---

## 7. Tester et générer une première vidéo

```bash
cd /opt/daily_shorts
.venv/bin/python tools/check_services.py
```

Chaque ligne doit afficher `[OK]` (le test `fusion` peut prendre 1 à 2 minutes). Ensuite :

```bash
.venv/bin/python main.py --no-publish
```

Durée : 10 à 20 minutes. Le premier lancement télécharge en plus le modèle de voix Kokoro (~330 Mo). À la fin s'affiche le chemin `output/<date>/final.mp4`.

**(PC)** Récupère la vidéo pour la regarder :

```bash
scp user@IP_VPS:/opt/daily_shorts/output/<date>/final.mp4 .
```

À vérifier : la voix est synchronisée avec les sous-titres, les images correspondent au texte, les faits sont corrects (le texte complet est dans `output/<date>/script.json`). Pour retenter le même jour depuis zéro : `rm -rf output/<date>` puis relance.

---

## 8. YouTube

### 8a. Projet Google Cloud (navigateur)

1. console.cloud.google.com → **Nouveau projet** (ex. « dailyshorts »).
2. **API et services → Bibliothèque** → recherche *YouTube Data API v3* → **Activer**.
3. **Google Auth Platform** (écran de consentement OAuth) → **Commencer** : nom de l'appli, ton e-mail d'assistance, audience **Externe**, coordonnées → **Créer**.
4. **Accès aux données** → **Ajouter des champs d'application** → coche `.../auth/youtube.upload` → **Enregistrer**.
5. **Audience** → **Publier l'application** (statut « En production »). Indispensable : en mode « Test », le jeton expire tous les 7 jours. Pas besoin de faire valider l'appli pour ton propre usage.
6. **Clients** → **Créer un client** → type **Application de bureau** → **Créer** → **Télécharger le JSON**.

### 8b. Autoriser la chaîne (PC, une seule fois)

Il faut Python sur ton PC.

```bash
git clone https://github.com/aymenkssi/dailyshorts.git && cd dailyshorts
python -m pip install google-auth-oauthlib
python tools/youtube_auth.py CHEMIN/VERS/client_secret_xxx.json
```

Le navigateur s'ouvre : choisis le compte **de la chaîne**, puis « Google n'a pas validé cette application » → **Paramètres avancés** → **Accéder à … (non sécurisé)** → **Continuer**. Le fichier `state/youtube_token.json` est créé.

Envoie-le sur le VPS :

```bash
scp state/youtube_token.json user@IP_VPS:/opt/daily_shorts/state/
```

Puis sur le VPS : `chmod 600 /opt/daily_shorts/state/youtube_token.json`.

---

## 9. Instagram

### 9a. Rendre les vidéos téléchargeables par Instagram (nginx)

Instagram récupère la vidéo depuis une URL publique de ton site.

```bash
sudo mkdir -p /var/www/aymendridi.com/shorts
sudo chown $USER:$USER /var/www/aymendridi.com/shorts
sudo nano /etc/nginx/sites-available/aymendridi.com     # adapte au nom de ton fichier
```

Dans le bloc `server { ... }` **HTTPS** (celui avec `listen 443`), ajoute :

```nginx
location /shorts/ {
    alias /var/www/aymendridi.com/shorts/;
    types { video/mp4 mp4; }
    autoindex off;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
cp /opt/daily_shorts/output/<date>/final.mp4 /var/www/aymendridi.com/shorts/test.mp4
curl -I https://aymendridi.com/shorts/test.mp4     # doit répondre 200 et Content-Type: video/mp4
rm /var/www/aymendridi.com/shorts/test.mp4
```

### 9b. Compte et appli Meta (navigateur)

1. Dans l'appli Instagram : **Paramètres → Type de compte et outils** → passe en **compte professionnel** (Créateur ou Entreprise). Le compte doit être **public**.
2. developers.facebook.com → **Mes applications → Créer une application** → cas d'utilisation **Autre** → type **Entreprise** → nom + e-mail → **Créer**.
3. Dans l'appli, produit **Instagram** → **Configurer** (« API setup with Instagram login » s'ajoute).
4. Section **Générer des jetons d'accès** → **Ajouter un compte** → connecte-toi avec ton compte Instagram → accepte les permissions (dont la publication de contenu).
5. Clique **Générer un jeton** pour ce compte → copie-le → `IG_ACCESS_TOKEN` dans `.env`.

Récupère l'identifiant du compte (sur le VPS) :

```bash
curl -s "https://graph.instagram.com/v23.0/me?fields=user_id,username&access_token=TON_JETON"
```

Mets la valeur de `user_id` dans `IG_USER_ID`. Le pipeline rafraîchit le jeton chaque semaine, il n'expirera pas tant que le pipeline tourne.

> L'appli peut rester en mode développement pour publier sur ton propre compte. Si Meta refuse la publication en invoquant l'*App Review*, il faudra soumettre la permission `instagram_business_content_publish` à la vérification.

---

## 10. TikTok

### 10a. Créer l'appli (navigateur)

1. developers.tiktok.com → crée un compte développeur → **Manage apps → Connect an app**.
2. Remplis : icône, nom, catégorie, description ; plateforme **Web** avec l'URL `https://aymendridi.com`. Pour la soumission à l'audit, TikTok demande aussi une page de conditions d'utilisation et une page de confidentialité : publie deux pages simples sur aymendridi.com.
3. **Add products** :
   - **Login Kit** → Redirect URI : `https://aymendridi.com/tiktok-callback` (la page n'a pas besoin d'exister).
   - **Content Posting API** → active **Direct Post**.
4. **Scopes** : `user.info.basic` et `video.publish`.
5. Copie **Client key** et **Client secret** → `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` dans `.env`.
6. Si l'appli est en mode **Sandbox**, ajoute ton compte TikTok dans les utilisateurs de test du sandbox.
7. **Submit for review** pour l'audit *Content Posting API* : sans audit, TikTok n'accepte que des publications **privées** et seulement depuis un **compte privé**.

### 10b. Autoriser ton compte (VPS)

```bash
cd /opt/daily_shorts
.venv/bin/python tools/tiktok_auth.py
```

1. Ouvre l'URL affichée sur ton PC, connecte-toi à TikTok, autorise.
2. Tu arrives sur `https://aymendridi.com/tiktok-callback?code=…` (page d'erreur normale).
3. Copie **l'URL complète** de la barre d'adresse et colle-la dans le terminal **rapidement** (le code expire en quelques minutes).

`state/tiktok_token.json` est créé ; le pipeline le renouvelle tout seul.

> Avant la fin de l'audit : passe ton compte TikTok en privé, ou laisse `PUBLISH_TIKTOK=false`.

---

## 11. Première publication réelle

Dans `.env`, active les plateformes prêtes (`PUBLISH_YOUTUBE=true`…). Garde `YOUTUBE_PRIVACY=private` pour commencer.

```bash
cd /opt/daily_shorts
.venv/bin/python main.py
cat output/$(date +%F)/published.json
```

Chaque plateforme doit afficher `OK …`. En cas d'erreur sur l'une, corrige puis relance la même commande : la vidéo n'est pas regénérée et les plateformes déjà publiées sont sautées.

---

## 12. Automatisation quotidienne (timer systemd)

On utilise systemd plutôt que cron : le cron d'Ubuntu ne gère pas de fuseau horaire par tâche, alors que le timer se cale sur **16 h 47 heure de Paris** (heure d'été comprise) et rattrape un passage manqué si le VPS était éteint.

```bash
cd /opt/daily_shorts
sed "s/CHANGE_ME/$USER/" deploy/dailyshorts.service | sudo tee /etc/systemd/system/dailyshorts.service >/dev/null
sudo cp deploy/dailyshorts.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dailyshorts.timer
systemctl list-timers dailyshorts.timer      # affiche la prochaine exécution
```

Lancer un passage tout de suite (test) : `sudo systemctl start dailyshorts.service`, puis suis-le avec `journalctl -u dailyshorts -f`.

Le service fait aussi le ménage : dossiers de travail de plus de 30 jours, vidéos publiques de plus de 7 jours.

Après quelques jours de relecture : `YOUTUBE_PRIVACY=public` dans `.env` (pris en compte au prochain passage).

---

## Entretien

| Action | Commande |
|---|---|
| Voir le dernier passage | `journalctl -u dailyshorts -n 100` |
| Prochain passage prévu | `systemctl list-timers dailyshorts.timer` |
| Relancer un jour raté | `cd /opt/daily_shorts && .venv/bin/python main.py --date AAAA-MM-JJ` |
| Changer l'heure | modifier `OnCalendar` dans `/etc/systemd/system/dailyshorts.timer`, puis `sudo systemctl daemon-reload` |
| Mettre en pause | `sudo systemctl disable --now dailyshorts.timer` |
| Mettre à jour le pipeline | `cd /opt/daily_shorts && git pull && .venv/bin/pip install -r requirements.txt` |
| Mettre à jour FreeLLMAPI | `cd /opt/freellmapi && sudo docker compose pull && sudo docker compose up -d` |
| Sauvegarder | `/opt/daily_shorts/.env`, `/opt/daily_shorts/state/`, `ENCRYPTION_KEY` de FreeLLMAPI |

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| `check_services` : FreeLLMAPI KO, *Connection refused* | conteneur arrêté | `cd /opt/freellmapi && sudo docker compose up -d` |
| FreeLLMAPI : erreur 401 | mauvaise clé | recopier la clé unifiée dans `LLM_API_KEY` |
| « texte généré via cloudflare » au lieu de freellmapi | FreeLLMAPI indisponible ou tous ses modèles saturés | vérifier la page Keys / Analytics du tableau de bord ; ajouter des fournisseurs |
| Cloudflare 429 | quota du jour atteint | rien à faire : bascule automatique sur FreeLLMAPI |
| `pip install` de kokoro échoue | Python 3.13 | recréer le `.venv` avec Python 3.12 |
| Processus tué pendant la voix ou l'installation | manque de RAM | ajouter le swap (étape 1) |
| YouTube `invalid_grant` | jeton révoqué ou écran de consentement resté en « Test » | passer en production (8a.5) et refaire 8b |
| Instagram `ERROR` au traitement | URL non publique ou fichier absent | refaire le test `curl -I` de l'étape 9a |
| TikTok `unaudited_client_can_only_post_to_private_accounts` | appli pas encore auditée | compte TikTok en privé ou `PUBLISH_TIKTOK=false` |
| Disque plein | vidéos et images accumulées | `du -sh /opt/daily_shorts/output` ; le service nettoie au-delà de 30 jours |
