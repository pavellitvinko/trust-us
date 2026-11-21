from pathlib import Path

import pytest

from ftf.store import Store

SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"


@pytest.fixture(autouse=True)
def _no_network_enrichment(monkeypatch):
    """Tests never make DNS or RDAP lookups; enrichment tests inject their own sources."""
    from ftf.enrich import _cache

    monkeypatch.setenv("FTF_ENRICH", "off")
    _cache.reset_shared_cache()
    yield
    _cache.reset_shared_cache()


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


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
