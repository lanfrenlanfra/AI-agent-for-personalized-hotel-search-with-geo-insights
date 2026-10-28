# syntax=docker/dockerfile:1
# Сборка и запуск ориентированы на linux/amd64 (на Apple Silicon: platform: linux/amd64 в compose включена).

# ── этап 1: зависимости в отдельный каталог ──
FROM python:3.13.2-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ── этап 2: минимальный образ без инструментов сборки ──
FROM python:3.13.2-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_PORT=8080 DATA_DIR=/app/data RUNTIME_DIR=/app/runtime
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=app:app app ./app
COPY --chown=app:app web ./web
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app data ./data
RUN mkdir -p /app/runtime && chown -R app:app /app
USER app
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=150s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"
CMD ["python", "-m", "app.main"]
