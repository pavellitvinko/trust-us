# Follow the Fake

Links suspicious ads and landing pages into campaigns, and shows the evidence behind each link.
HackNation challenge "Kolekcjoner fejków", set by NASK.

- Design: [docs/architecture.md](docs/architecture.md)
- Signals: [docs/signal-catalogue.md](docs/signal-catalogue.md)

**Status: Method track built** (architecture §8a, §9a, §10a, §13a). Twenty-three fingerprint
layers, five comparator families with rarity weighting and a stoplist, typed fusion with the
contradiction and demotion rules, three-level grouping, funnel edges, open-set assignment,
candidate generation and the evaluation harness all run on `tests/fixtures/observations.json`
with no model call. Extractors beyond the reference slice, live capture, enrichment and the
interface are other tracks' work; `docs/method-namespaces.md` says what they must emit.

## Run locally

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ftf ingest testing_sample      # a folder of incident folders, or one incident folder
uv run ftf views
uv run ftf pairs
uv run ftf compare <view_id> <view_id>
uv run ftf explain <view_id> <view_id>   # evidence per layer and the typed relation with reasons
uv run ftf relations --type same_campaign --reasons
uv run ftf groups                         # campaigns, families, playbooks
uv run ftf incidents                      # per-folder roll-up, funnel edges, mismatches
uv run ftf assign <view_id>               # open-set outcome for one view
uv run ftf eval                           # agreement with docs/baseline-report.md
uv run ftf load-fixtures && uv run ftf eval --fixtures   # the same on the hand-made fixtures
uv run ftf loio                           # leave-one-incident-out on the fixtures
uv run ftf ablate [--degrade]             # layer ablation / degradation tables
uv run streamlit run app/streamlit_app.py
uv run pytest
```

## Run in Docker

```bash
cp .env.example .env                  # keys are only needed for the model-based layers
docker compose up --build             # interface on http://localhost:8501
docker compose run --rm app ftf ingest /sample
```

`FTF_SAMPLE_DIR` chooses which host folder is mounted at `/sample` (default `./testing_sample`).
State lives in `./data`.

## Layout and ownership

| Path | What | Track |
| --- | --- | --- |
| `src/ftf/contracts.py`, `registry.py`, `store.py`, `pipeline.py` | Shared contracts and plumbing. **Frozen**: change only by agreement | shared |
| `src/ftf/ingest/`, `extract/`, `capture/`, `enrich/` | Reading material, harvesting identifiers, live capture, outside lookups | Evidence |
| `src/ftf/layers/`, `compare/`, `fusion/`, `grouping/`, `eval/` | Fingerprints, comparators, typed relations, grouping, tests of the method | Method |
| `src/ftf/models/`, `perception/`, `patterns/`, `expectations/`, `app/` | Model gateway, reading by model, pattern discovery, expected elements, interface | Perception and interface |

## Adding a plugin

Write a class in the right package and decorate it; the pipeline picks it up.

```python
from ftf.registry import extractors

@extractors.register
class MyExtractor:
    name = "my.extractor"
    version = "1"
    def run(self, view, ctx):
        return []   # list[Observation]
```

Layers (`@layers.register`) turn observations into a fingerprint and name their comparator.
Comparators (`@comparators.register`) turn two fingerprints into an `Evidence`.

## Data handling

Organiser material must not be redistributed publicly. `testing_sample/` and `data/` are
git-ignored; keep this repository private.
