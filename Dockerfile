# Playwright's image carries Chromium's system libraries; discovery (and live capture) need them.
FROM mcr.microsoft.com/playwright/python:v1.65.0-noble

# ffmpeg is used for video frames.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY app ./app
# Install the package, then the Chromium build that matches the installed Playwright version.
RUN uv pip install --system --break-system-packages --no-cache . \
    && python -m playwright install chromium

ENV FTF_DATA_DIR=/data
VOLUME /data
EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
