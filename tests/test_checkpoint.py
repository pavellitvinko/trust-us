"""Hour-4 checkpoint through the CLI and real organiser files, with no model calls."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ftf.contracts import RelationType
from ftf.eval.reference import resolve_view
from ftf.grouping import build_groups
from ftf.registry import layers, load_plugins
from ftf.store import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "testing_sample"


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_real_sample_cli_checkpoint_without_models(tmp_path):
    env = {
        **os.environ,
        "FTF_DATA_DIR": str(tmp_path / "data"),
        "TLDEXTRACT_CACHE": str(tmp_path / "tld-cache"),
        "FTF_EMBEDDER": "fake",  # local hashed text only; no model/cache access
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
    }

    def cli(*args):
        audit = tmp_path / f"{args[0]}-audit.json"
        result = subprocess.run(
            [sys.executable, "-m", "tests._model_off_cli", *args],
            cwd=ROOT,
            env={**env, "FTF_MODEL_OFF_AUDIT": str(audit)},
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(audit.read_text()) == {"model": [], "network": []}
        return result.stdout

    assert "relations:" in cli("ingest", str(SAMPLE))
    load_plugins()
    store = Store(tmp_path / "data" / "ftf.sqlite")
    try:

        def view(alias):
            resolved = resolve_view(store, alias)
            assert resolved is not None, alias
            return resolved

        relations = {(r.view_a, r.view_b): r for r in store.relations()}
        for a, b, expected in (
            ("1:page", "2:page", RelationType.SAME_CAMPAIGN),
            ("2:healthlinkq", "3:page", RelationType.DUPLICATE),
            ("48:page", "61:article", RelationType.SAME_FAMILY),
            ("61:article", "61:chat", RelationType.SAME_CAMPAIGN),
        ):
            relation = relations[tuple(sorted((view(a), view(b))))]
            assert relation.type is expected, relation

        groups = build_groups(store)
        article, chat = view("61:article"), view("61:chat")
        assert groups.campaign_of[article] == groups.campaign_of[chat]
        c48, c61 = groups.campaign_of[view("48:page")], groups.campaign_of[article]
        assert c48 != c61 and groups.family_of[c48] == groups.family_of[c61]
        assert len({groups.campaign_of[v.view_id] for v in store.views("2")}) == 2
        edge = next(
            e for e in groups.funnel_edges["61"][0] if (e.source, e.target) == (article, chat)
        )
        assert {(e.source, e.target) for e in groups.funnel_edges["61"][0]} == {(article, chat)}
        assert any("stored navigation" in why for why in edge.evidence)
        assert f"funnel {article} -> {chat}:" in cli("incidents")

        # Check extractor-to-layer wiring, not just the relation labels.
        assert store.fingerprints(layer_id="kit.dom")
        for alias in ("1:page", "2:page", "61:article"):
            vid = view(alias)
            obs = store.observations(vid)
            body = [o for o in obs if o.namespace == "page.body_text"]
            assert body
            actual = store.fingerprints(layer_id="text.shingle", view_id=vid)[0]
            assert actual == layers.get("text.shingle").build(store.get_view(vid), body)
        print(cli("eval"))
    finally:
        store.close()
