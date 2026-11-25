# Image légère de l'interface d'administration (contexte de build : racine du dépôt)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Paris
RUN pip install "fastapi>=0.115" "uvicorn>=0.30" "jinja2>=3.1" "python-multipart>=0.0.9" "requests>=2.31" tzdata

WORKDIR /app
COPY shorts/__init__.py shorts/store.py ./shorts/
COPY admin ./admin

# Même utilisateur que le pipeline (UID 1000) : ils partagent state/ et output/
RUN useradd -u 1000 -m shorts && mkdir -p /app/state /app/output && chown -R shorts:shorts /app
USER shorts

CMD ["uvicorn", "admin.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
