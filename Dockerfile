FROM python:3.12-slim

# ffmpeg is used for video frames. The Evidence track switches this base to the
# Playwright image when live capture lands.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY app ./app
RUN uv pip install --system --no-cache .

ENV FTF_DATA_DIR=/data
VOLUME /data
EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
