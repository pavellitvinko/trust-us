import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ftf.config import Settings
from ftf.store import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "testing_sample"


@pytest.fixture(autouse=True)
def _no_network_enrichment(monkeypatch):
    """Tests never make DNS or RDAP lookups; enrichment tests inject their own sources."""
    from ftf.enrich import _cache

    monkeypatch.setenv("FTF_ENRICH", "off")
    _cache.reset_shared_cache()
    yield
    _cache.reset_shared_cache()


@pytest.fixture(autouse=True, scope="module")
def _reset_global_tables():
    """A failing test must not leak fitted weights, active groups or a fake embedder into the
    next module. Modules that fit weights once per module still see them within the module."""
    from ftf.compare.weights import weights
    from ftf.layers._embedder import set_embedder

    yield
    weights.reset()
    set_embedder(None)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def settings(tmp_path):
    """Model settings with no API keys: every gateway call must be served by a fake or a cache."""
    return Settings(
        data_dir=tmp_path / "data",
        openai_api_key="",
        gemini_api_key="",
        openai_model="openai-test",
        embedding_model="embed-test",
        video_model="video-test",
    )


@pytest.fixture
def model_off_cli(tmp_path):
    """Run the real CLI in a subprocess that fails on any model import or network call.

    The data directory is `tmp_path / "data"`; the audit of forbidden attempts must be empty."""
    env = {
        **os.environ,
        "FTF_DATA_DIR": str(tmp_path / "data"),
        "TLDEXTRACT_CACHE": str(tmp_path / "tld-cache"),
        "FTF_EMBEDDER": "fake",  # local hashed text only; no model/cache access
        "FTF_ENRICH": "off",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
    }

    def cli(*args, timeout=120):
        audit = tmp_path / f"{args[0]}-audit.json"
        result = subprocess.run(
            [sys.executable, "-m", "tests._model_off_cli", *args],
            cwd=ROOT,
            env={**env, "FTF_MODEL_OFF_AUDIT": str(audit)},
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(audit.read_text()) == {"model": [], "network": []}
        return result.stdout

    return cli


@pytest.fixture(scope="session")
def sample_store():
    """The organiser sample, ingested and run once. Skipped where the sample is absent."""
    if not SAMPLE.is_dir():
        pytest.skip("testing_sample/ not present")
    from ftf import pipeline
    from ftf.ingest import ingest_path

    s = Store(":memory:")
    ingest_path(SAMPLE, s)
    pipeline.run_extract(s)
    pipeline.run_fingerprint(s)
    pipeline.run_compare_all(s)
    yield s
    s.close()
