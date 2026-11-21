FROM python:3.12-slim

# ffmpeg (with ffprobe) is used for video frames. The Evidence track switches this base to the
# Playwright image when live capture lands.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

# Install exactly what uv.lock pins, into a venv outside /app.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY src ./src
COPY app ./app
RUN uv sync --locked --no-dev --no-editable --no-cache

ENV FTF_DATA_DIR=/data \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
VOLUME /data
EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
