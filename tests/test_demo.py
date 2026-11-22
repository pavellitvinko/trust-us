"""Demo rehearsal, without the browser steps."""

from pathlib import Path

import pytest

from ftf.demo import run_demo

SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"


@pytest.mark.sample
@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_demo_sequence_on_the_sample(monkeypatch, tmp_path):
    monkeypatch.setenv("TLDEXTRACT_CACHE", str(tmp_path / "tld"))
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "kept"))
    steps = run_demo(SAMPLE, capture=False)
    assert [s.status for s in steps[:6]] == ["passed"] * 6, steps
    assert {s.status for s in steps[6:]} == {"skipped"}
    assert not (tmp_path / "kept").exists()


def test_a_failed_step_does_not_stop_the_rehearsal(tmp_path):
    (tmp_path / "empty").mkdir()
    from ftf.compare.weights import weights

    before = dict(weights.holders)
    steps = run_demo(tmp_path / "empty", capture=False)
    assert len(steps) == 9
    assert steps[0].status == "failed"
    assert weights.holders == before
