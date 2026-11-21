"""Settings, read from the environment (and a local .env file if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    openai_api_key: str
    gemini_api_key: str
    openai_model: str
    embedding_model: str
    video_model: str
    enrich_mode: str = "on"  # on | cache | off — outside lookups (DNS, RDAP)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ftf.sqlite"

    @property
    def enrich_cache_dir(self) -> Path:
        return self.data_dir / "enrich_cache"

    @property
    def model_cache_dir(self) -> Path:
        return self.data_dir / "model_cache"


def get_settings() -> Settings:
    _load_dotenv()
    env = os.environ.get
    return Settings(
        data_dir=Path(env("FTF_DATA_DIR", "./data")),
        openai_api_key=env("OPENAI_API_KEY", ""),
        gemini_api_key=env("GEMINI_API_KEY", ""),
        openai_model=env("FTF_OPENAI_MODEL", "gpt-6-luna"),
        embedding_model=env("FTF_EMBEDDING_MODEL", "gemini-embedding-2"),
        video_model=env("FTF_VIDEO_MODEL", "gemini-4-hydrogen"),
        enrich_mode=env("FTF_ENRICH", "on"),
    )
