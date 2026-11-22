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
`docs/method-namespaces.md` lists the namespaces the layers consume. `ftf capture` turns a URL,
or an archived HAR in replay mode, into a capture bundle (see below). Warning-list enrichment
and the basic interface are available below; broader enrichment remains to be built.

Pattern discovery (architecture §6) includes a versioned library with stable IDs and
merge/split history, embedding and clustering, pattern induction and matching, novelty
tracking, and pattern-based playbook evidence. See [Pattern Library](PATTERN_LIBRARY.md).

## Run locally

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium   # once, for ftf capture
uv run ftf ingest testing_sample      # a folder of incident folders, or one incident folder
uv run ftf enrich                     # DNS and RDAP lookups again (cached; also part of run)
uv run ftf views
uv run ftf pairs
uv run ftf compare <view_id> <view_id>
uv run ftf explain <view_id> <view_id>   # evidence per layer and the typed relation with reasons
uv run ftf explain <view_a> <view_b> --format json --output comparison.json
uv run ftf explain <view_a> <view_b> --output comparison.md
uv run ftf relations --type same_campaign --reasons
uv run ftf groups                         # campaigns, families, playbooks
uv run ftf incidents                      # per-folder roll-up, funnel edges, mismatches
uv run ftf assign <view_id>               # open-set outcome for one view
uv run ftf eval                           # agreement with docs/baseline-report.md
uv run ftf load-fixtures && uv run ftf eval --fixtures   # the same on the hand-made fixtures
uv run ftf loio                           # leave-one-incident-out on the fixtures
uv run ftf ablate [--degrade]             # layer ablation / degradation tables
uv run ftf ablate [--degrade] --sample testing_sample   # the same on real files, model off
uv run ftf loio --sample testing_sample   # leave-one-out on real files, weights frozen
uv run ftf validate testing_sample --out docs/method-results-real-sample.md   # the whole real-file validation
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

`ftf explain` produces a complete pair report: object identities and files, confidence
with its rule, coverage as a group count, scores and weights, shared and differing values
linked to source observations, contradictions, demoted/ignored matches, and every missing
or incompatible layer. Observation references include the file, locator, extractor and
version. Derived text/DOM comparisons include exact input overlaps; Markdown shows labelled
examples and JSON retains the complete sets. Long source text is a labelled excerpt.
Reports use stored fingerprints and never call a model or modify the database.
The same report is available under **Full comparison report** in Streamlit with JSON and
Markdown downloads. Its separate acceptance check is:

```bash
uv run pytest tests/test_reporting.py -q
```

That check covers report completeness on the real sample with model/network access blocked,
source resolution, missing/incompatible inputs, ignored matches, reversed object order,
and the Streamlit report view. The real-sample check skips if the sample is absent.

## Live capture

```bash
uv run ftf capture https://example.com --ingest               # live: capture, ingest, run
uv run ftf capture --replay testing_sample/2/healthlinkq.pro.har --ingest   # dead domain, from its HAR
uv run ftf capture https://example.com --out some/folder      # bundle only, ingest later
```

A capture writes `<incident>/bundle/` with `dom.html` (rendered after scrolling),
`page.har` (bodies embedded), `screenshot.png` (full page), `resources/` (bodies by SHA-256)
and `manifest.json`: requested and final URL, capture time, mode, egress, user agent, the
ordered redirect trace (`http`, `meta_refresh`, `script`, `history`, each citing its HAR
entry), the final response's IP, TLS certificate and headers, failed and blocked requests.
Default location: `$FTF_DATA_DIR/captures/<host>-<UTC time>/`.

Each capture uses a fresh browser context with service workers blocked and downloads
refused; forms are never submitted (blocked in the page, and any non-GET navigation is
aborted) and nothing is typed or clicked. A dead or unreachable domain still produces a
bundle with `status` (`dns_failed`, `connection_failed`, `tls_failed`, `timeout`,
`http_error`) and becomes a view whose page layers are not observable, while DNS and RDAP
still run. Replay serves the browser only from the archive (`not_in_archive` when it lacks
the page); the archive is kept as `page.har` and its own time stays the capture time.

## Known corpus

`corpus/` holds processed results for the organiser sample: observations, fingerprints, evidence
and relations. It has no organiser files or whole-page text, secrets are hashed, and
`corpus/manifest.json` records the commit, input hash and execution mode. The Docker image loads
it into an empty store, so new material is compared with the known campaigns straight away.

```bash
uv run ftf corpus load                       # local: load it into an empty store
uv run ftf corpus build testing_sample       # rebuild it from your own copy of the sample
```

The shipped corpus is deterministic and has no model output or model cache, so model-based
sections show as not run until `ftf perceive` and `ftf patterns discover` run on your material.
**This repository must stay private: organiser material must not be redistributed.**

Model keys are optional for the deterministic path:

| Key | Used for | Get it from |
| --- | --- | --- |
| `OPENAI_API_KEY` | page and image reading, patterns, expectation profiles | <https://platform.openai.com/api-keys> |
| `GEMINI_API_KEY` | embeddings, video | <https://aistudio.google.com/> |

Libraries, models, services and data sources are listed in [docs/licences.md](docs/licences.md).

## Run in Docker

```bash
cp .env.example .env                  # keys are only needed for the model-based layers
docker compose up --build             # interface on http://localhost:8501
docker compose run --rm app ftf ingest /sample
docker compose run --rm app ftf capture https://example.com --ingest   # Chromium is in the image
```

`FTF_SAMPLE_DIR` chooses which host folder is mounted at `/sample` (default `./testing_sample`).
The sample is not in git, so on a fresh clone either copy it to `./testing_sample` or run
`FTF_SAMPLE_DIR=/path/to/sample docker compose run --rm app ftf ingest /sample`.
Without it `/sample` is empty and `ftf ingest` stops with "no views found" (exit code 1).
State lives in `./data`. The image installs the exact versions in `uv.lock`;
`docker compose ps` shows the interface as `healthy` once it answers.

## Warning lists (HAC-17)

`ftf ingest` / `ftf run` check domains and supported entity/offer names against local
snapshots after extraction. `ftf perceive` repeats the checks after page and image reading.
These paths make no warning-list network request. To download CERT Polska and KNF once
at the start of an enrichment session, then check all stored views locally:

```bash
uv run ftf enrich                         # DNS/RDAP, then CERT/KNF refresh and local checks
uv run ftf enrich --view <view_id>         # restrict the views, not the source coverage
uv run ftf enrich --offline               # skip DNS/RDAP; warning snapshots only
```

Sources: [CERT Polska active domains](https://cert.pl/en/warning-list/) and
[KNF public warnings](https://www.knf.gov.pl/dla_konsumenta/ostrzezenia_publiczne).
CERT's published terms permit commercial use without guarantees of completeness,
currency or maliciousness. This tool records list membership, not a legal conclusion.
KNF entries describe warnings and proceedings; they are not a register of licensed entities.
The package and Docker image include a KNF snapshot downloaded on **2026-11-21**
(669 warning rows; some rows name multiple entities). CERT needs a successful initial
refresh. The supplement register is omitted because no export has been verified.

Snapshots under `data/warning_lists/` retain the source content, URL, retrieval timestamp
and SHA-256 of the decoded UTF-8 content. Dated versions are kept in `archive/`, including
when unchanged source content is downloaded again. A failed refresh keeps the last valid
snapshot, its original date, and an explicit refresh error. A malformed or missing source
falls back to the most recently archived valid file, then the bundled KNF or gambling snapshot.
Without a valid fallback it produces `not_observable`. Interrupted HTTP responses are treated
as source failures, never negative findings.

The image includes the **2026-11-22 Ministry of Finance snapshot**, downloaded through
Chrome from the documented [active-register XML API](https://hazard.mf.gov.pl/api/Register).
It contains 60,231 source records. The snapshot retains the original XML, SHA-256,
retrieval time with timezone, public record IDs, entry dates and XML locators. All 60,231
addresses are indexed, including the eight official addresses with underscores in a host
label. Future unsupported addresses remain in the source with validation errors;
snapshot provenance reports both the total and unmatchable record counts.

The API is a data source for the dated snapshot, rather than a live per-domain lookup.
An earlier scripted request was blocked; the browser download succeeded without a bot-check
workaround. The application retains **no automated fetch path** for this source. To refresh
it, download the API response in the browser (use **Save Link As** on the API link in the
[official specification](https://hazard.mf.gov.pl/Content/files/Rejestr_Stron%20Hazardowych_Specyfikacja_WeWy_v1-2.pdf))
and import the XML with its actual retrieval date. UTF-8 domain-per-line files remain supported:

```bash
uv run ftf enrich --offline --import-gambling /path/to/Register.xml --snapshot-date 2026-11-22
```

Imported updates live with the persistent application data and take precedence over the
bundled snapshot. To ship a newer snapshot, replace `src/ftf/enrich/snapshots/gambling.json`
with the generated `data/warning_lists/gambling.json`. N5 is now available offline from the
bundled source; missing inputs or unavailable valid data still yield `not_observable`.

`warning.cert`, `warning.knf` and `warning.gambling` observations include the checked input
and its provenance, snapshot metadata, original matched records and source locators, and
the matching method. Domains match only at host-label boundaries, including listed
subdomains. KNF names use case/accent/punctuation normalization and legal-suffix removal;
fuzzy candidates use `SequenceMatcher` similarity at least 0.90 (not a probability).
Fuzzy and ambiguous matches are labelled `possible_match` and require verification.
Only the entity column is searched; prosecutor and case-note names are excluded.

A miss is **"not listed as of <snapshot date>"** and says nothing about legitimacy.
Warnings are risk-only observations and do not alter campaign grouping. Exact, unambiguous
name references to the same KNF entity provide contextual links between views. Domain
membership alone does not establish entity identity. Numbered and separately labelled
entities in one KNF row are kept separate, and parenthetical/platform-list aliases are indexed.
After upgrading from parser version 1, rerun enrichment to rebuild entity links; older
observations remain visible but cannot create contextual links. Material shows
these links and full provenance; comparison JSON/Markdown carries both views' warning
observations and shared listed entities. The HAC-44 adapter is
`warning_risk_reasons(warning_observations(store, view_id))`: it returns only hits and possible
matches, preserving uncertainty and a stable reference to the full source observation.
Misses and unavailable checks are excluded. Material and comparison JSON use this adapter;
the complete HAC-44 summary is a separate issue. The Material **Check warning lists** button uses
offline snapshots by default; clear its offline checkbox to refresh both online sources.

```bash
uv run pytest tests/test_warning_lists.py -q
```

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
