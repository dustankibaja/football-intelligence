FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DEBUG=False

WORKDIR /app
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin football
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY alembic ./alembic
COPY alembic.ini c1_artifact.json m1_artifact.json ./
RUN chown -R football:football /app
USER football

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 CMD python -c "import json,urllib.request; x=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=4)); assert x['status']=='ok' and x['database']=='connected'"

CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
