# Build from repository root: docker build -f backend/Dockerfile .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system footmo && useradd --system --gid footmo --home-dir /opt/footmo footmo
WORKDIR /opt/footmo/backend

COPY backend/ /opt/footmo/backend/
COPY phase6_3_shadow_prediction_manifest.json /opt/footmo/phase6_3_shadow_prediction_manifest.json

RUN pip install --no-cache-dir .

USER footmo
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
