# The screens (React, in frontend/) are built with Node and copied into the Python image.
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Playwright's image carries Chromium and its system libraries for live capture (`ftf capture`).
# Its tag must match the `playwright` version pinned in uv.lock.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

# ffmpeg (with ffprobe) is used for video frames.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

# Install exactly what uv.lock pins, into a venv outside /app.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY src ./src
COPY app ./app
COPY corpus ./corpus
COPY .streamlit ./.streamlit
COPY --from=web /web/dist ./frontend/dist
RUN uv sync --locked --no-dev --no-editable --no-cache

ENV FTF_DATA_DIR=/data \
    FTF_SEED_CORPUS=1 \
    FTF_WEB_DIST=/app/frontend/dist \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
VOLUME /data
# 8000: the web interface. 8501: the classic Streamlit screens, if started by hand.
EXPOSE 8000 8501
CMD ["ftf", "serve", "--host", "0.0.0.0", "--port", "8000"]
