# Follow the Fake

Links suspicious ads and landing pages into campaigns, and shows the evidence behind each link.
HackNation challenge "Kolekcjoner fejków", set by NASK.

- Design: [docs/architecture.md](docs/architecture.md)
- Signals: [docs/signal-catalogue.md](docs/signal-catalogue.md)
- **NEW**: [Pattern Library](PATTERN_LIBRARY.md) — Pattern discovery, library versioning, novelty tracking

**Status: Method track built; identifier, kit, asset and perception extractors landing.**
Twenty-three fingerprint layers, five comparator families with rarity weighting and a stoplist,
typed fusion with the contradiction and demotion rules, three-level grouping, funnel edges,
open-set assignment, candidate generation and the evaluation harness run on
`tests/fixtures/observations.json` with no model call (architecture §8a, §9a, §10a, §13a). The
Evidence track's identifier extractors (tracker config, pixels and tag IDs, ad parameters,
conversion and outbound targets), tracker-kit extractors (URL parameter schema, path
conventions, API routes, config key paths, hidden fields, server headers) and asset hashes
(exact by role, grayscale perceptual) produce observations on the sample and the layers consume
them; the Perception track reads pages and screenshots with the model (`ftf perceive`).
`docs/method-namespaces.md` lists the namespaces the layers consume. Live capture, enrichment
and the interface are still to be built.

Pattern discovery (architecture §6) includes a versioned library with stable IDs and
merge/split history, embedding and clustering, pattern induction and matching, novelty
tracking, and pattern-based playbook grouping. See [Pattern Library](PATTERN_LIBRARY.md).

## Run locally

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run ftf ingest testing_sample      # a folder of incident folders, or one incident folder
uv run ftf enrich                     # DNS and RDAP lookups again (cached; also part of run)
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

With the organiser sample in `testing_sample/`, run the deterministic checkpoint with:

```bash
uv run pytest tests/test_checkpoint.py -q -s
```

The test ingests the real files into a temporary database through the CLI. It checks the
1↔2 campaign, 2↔3 duplicate, 48↔61 family and 61 article→chat funnel. It blocks model
imports and network access, uses local hashed text vectors, and checks an audit of zero
attempts. It skips when the private sample is absent.

## Run in Docker

```bash
cp .env.example .env                  # keys are only needed for the model-based layers
docker compose up --build             # interface on http://localhost:8501
docker compose run --rm app ftf ingest /sample
```

`FTF_SAMPLE_DIR` chooses which host folder is mounted at `/sample` (default `./testing_sample`).
The sample is not in git, so on a fresh clone either copy it to `./testing_sample` or run
`FTF_SAMPLE_DIR=/path/to/sample docker compose run --rm app ftf ingest /sample`.
Without it `/sample` is empty and `ftf ingest` stops with "no views found" (exit code 1).
State lives in `./data`. The image installs the exact versions in `uv.lock`;
`docker compose ps` shows the interface as `healthy` once it answers.

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

## Outside sources

`ftf run` (and `ingest`) looks up each page's domain in DNS and RDAP after extraction; pass
`--no-enrich` to skip. Answers are cached with their timestamp in `$FTF_DATA_DIR/enrich_cache/`
(7 days; failures 1 hour), and a failed lookup is recorded as `not_observable`, never as a
negative; network failures are not cached. `FTF_ENRICH=cache` uses the cache only,
`FTF_ENRICH=off` (also `0`, `false`, `no`, or any unknown value) makes no lookups.

- DNS: the system resolver (dnspython), so domains are not sent to a third-party DNS service.
- RDAP: the registry's server, found through IANA's bootstrap file
  (<https://data.iana.org/rdap/dns.json>). Registration data is public and served under each
  registry's RDAP terms of use; requests are spaced at least a second apart per server.

## Data handling

Organiser material must not be redistributed publicly. `testing_sample/` and `data/` are
git-ignored; keep this repository private.
