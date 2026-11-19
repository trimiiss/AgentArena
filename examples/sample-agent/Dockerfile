# syntax=docker/dockerfile:1.7
FROM python:3.13.18-slim-bookworm AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY constraints.txt ./
COPY examples/sample-agent/pyproject.toml ./service/
COPY examples/sample-agent/sample_agent ./service/sample_agent
RUN --mount=type=cache,target=/root/.cache/pip pip install -c constraints.txt ./service

FROM python:3.13.18-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "sample_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
