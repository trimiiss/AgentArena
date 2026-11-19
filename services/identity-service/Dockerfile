# syntax=docker/dockerfile:1.7
# Build context is the repository root, because the shared library lives outside this folder.
FROM python:3.13.18-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
# Third-party dependencies change rarely, so this layer is shared by every service image.
COPY constraints.txt ./
COPY shared/ ./shared/
RUN --mount=type=cache,target=/root/.cache/pip pip install -c constraints.txt ./shared
COPY services/identity-service/pyproject.toml ./service/
COPY services/identity-service/identity_service ./service/identity_service
RUN --mount=type=cache,target=/root/.cache/pip pip install -c constraints.txt ./service

FROM python:3.13.18-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY services/identity-service/alembic.ini ./
COPY services/identity-service/migrations ./migrations
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn identity_service.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 20 --proxy-headers --forwarded-allow-ips '*'"]
