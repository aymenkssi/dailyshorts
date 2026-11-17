FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris \
    HF_HOME=/app/cache/huggingface

# ffmpeg : montage ; espeak-ng : prononciation des mots inconnus (Kokoro) ; tzdata : heure de Paris ;
# curl : installation de Claude Code
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg espeak-ng tzdata curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# torch en version CPU (~200 Mo au lieu de ~2 Go), puis le reste
RUN pip install --upgrade pip \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt \
 && python -m spacy download en_core_web_sm   # sinon Kokoro tente de l'installer au 1er lancement, sans droits root

COPY . .

# Utilisateur non root (UID 1000 : les dossiers montés doivent lui appartenir)
RUN useradd -u 1000 -m shorts \
 && mkdir -p /app/output /app/state /app/cache /public \
 && chown -R shorts:shorts /app /public
USER shorts

# Claude Code (écriture des scripts avec ton abonnement Claude, voir DEPLOIEMENT.md).
# En cas d'échec de l'installation, l'image reste utilisable : FreeLLMAPI écrit alors les scripts.
ENV PATH="/home/shorts/.local/bin:${PATH}" \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CONFIG_DIR=/app/cache/claude
RUN (curl -fsSL https://claude.ai/install.sh | bash && claude --version) \
 || echo "Claude Code non installé : les scripts passeront par FreeLLMAPI"

CMD ["python", "scheduler.py"]
