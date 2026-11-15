FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris \
    HF_HOME=/app/cache/huggingface

# ffmpeg : montage ; espeak-ng : prononciation des mots inconnus (Kokoro) ; tzdata : heure de Paris
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg espeak-ng tzdata \
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

CMD ["python", "scheduler.py"]
