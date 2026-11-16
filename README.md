# Daily Shorts — chaque jour une vidéo d'histoire de 4 min + un Short d'accroche, 100 % gratuit

**Installation pas à pas : voir [DEPLOIEMENT.md](DEPLOIEMENT.md).**

## Fonctionnement

| Étape | Service (gratuit) | Secours automatique |
|---|---|---|
| Script de la vidéo longue, puis du Short d'accroche | FreeLLMAPI `auto:smart` (meilleur modèle gratuit dispo : Gemini, Groq, Cerebras, Mistral…) | Cloudflare gpt-oss-120b |
| Vérification des faits | FreeLLMAPI `fusion` (plusieurs modèles + un juge) | `auto:smart`, puis Cloudflare |
| Images (~55 en 16:9 + ~10 en 9:16, style anime) | Cloudflare FLUX.2 klein | FreeLLMAPI (NVIDIA, SiliconFlow…) |
| Voix off + minutage des mots | Kokoro, dans le conteneur | — |
| Montage | ffmpeg + Pillow : zoom/pan fluide, sous-titres mot à mot (1920×1080 et 1080×1920) | — |
| Publication | YouTube : vidéo longue + Short (avec le lien vers la longue) ; Instagram et TikTok : le Short | chaque cible indépendante |

Coût : 0 €. Le quota gratuit Cloudflare (10 000 « neurons »/jour) couvre les deux vidéos (~7 000) ; s'il est épuisé, les images passent par FreeLLMAPI. Pas d'animation IA des personnages : aucun service gratuit ne le permet au quotidien.

## Déploiement

Docker Compose derrière Traefik, comme Waselni : `freellmapi` (local uniquement), `shorts` (planificateur interne : création à `GENERATE_AT`, publication à `PUBLISH_AT`, heure de Paris) et `media` (sert `/shorts/*.mp4` pour Instagram).

## Commandes

```bash
docker compose up -d --build                                  # démarrer / mettre à jour
docker compose logs -f shorts                                 # suivre le pipeline
docker compose exec shorts python tools/check_services.py     # tester les services
docker compose exec shorts python main.py --no-publish        # générer sans publier
docker compose exec shorts python main.py --date 2026-11-12   # reprendre un jour (étapes faites sautées)
```

## Points d'attention

- **TikTok** : sans audit de ton app TikTok, les publications via API restent privées.
- **Instagram** : via l'API, seules les vidéos de 5 à 90 s vont dans l'onglet Reels : garde `SHORT_SECONDS` sous 90.
- **YouTube** : Shorts jusqu'à 3 min ; vidéo marquée « contenu synthétique ». Démarre en `YOUTUBE_PRIVACY=private` pour relire.
- **Exactitude** : même avec `fusion`, des erreurs restent possibles ; les textes sont dans `output/<date>/scripts.json`.
- **FreeLLMAPI** : projet tiers prévu pour un usage personnel ; les conditions de chaque fournisseur s'appliquent. Il doit rester accessible uniquement en local (127.0.0.1:3001).
- **Personnaliser** : style visuel → `STYLE` dans `shorts/script_writer.py` ; sous-titres → haut de `shorts/editing.py` ; voix → `KOKORO_VOICE` ; modèles → `.env`.
