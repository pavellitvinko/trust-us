# Tests

Run from the repository root:

```sh
uv run pytest -q                                  # full suite
uv run pytest -q -m "not sample and not browser"   # no organiser files or Chromium
uv run pytest -q -m browser                       # real Chromium integration
uv run pytest -q -m sample                        # organiser-file integration
uv run pytest -q --durations=40 --junitxml=/tmp/ftf-tests.xml
```

The default run includes every test. Sample tests skip when `testing_sample/` is absent.
Browser tests check Chromium once when selected, and skip if it cannot launch. Install
Chromium with `uv run playwright install chromium`. FFmpeg tests retain their existing
availability checks. The frontend has no test files or test runner in `package.json`.

Pytest prints the 15 slowest phases of at least 0.5 seconds by default. JUnit case times
include setup and teardown; console durations separate those phases. Mark tests that
read organiser files directly with `@pytest.mark.sample`. Tests using the shared sample
fixtures receive the marker through their fixture dependencies. Mark real browser tests
with `@pytest.mark.browser` and use the `chromium` fixture.

## Audit on 22 November 2026

The audit covered the inventory and timing of all 52 committed test modules. Both full
runs used the same checkout, interpreter, local organiser sample, and installed Chromium
and FFmpeg. The timing baseline was commit `70a4dea`; the comparison includes these test edits.
Uncommitted changes from the source checkout were not included.
These are single local measurements, so machine load and file caches can affect them.

| Full run | Before | After |
| --- | ---: | ---: |
| Passing cases | 1239 | 1223 |
| Elapsed time | 273.55 s | 199.42 s |

The measured saving was 74.13 seconds (27.1%). No case was skipped
in either full run. The reduction in case count comes from removing 14 low-value tests
and consolidating two repeated runs while keeping their behavioral assertions.

| Module, including setup and teardown | Before | After |
| --- | ---: | ---: |
| `test_capture.py` | 49.67 s | 35.57 s |
| `test_gateway_embeddings.py` | 38.35 s | 15.77 s |
| `test_reporting.py` | 21.76 s | 1.92 s |
| `test_corpus_report.py` | 20.80 s | 1.22 s |
| `test_sample_validation.py` | 36.12 s | 42.76 s |

The fast selection passed 1,163 cases in 26.97 seconds, with 60 cases deselected.
Ruff passed for all test files, and `git diff --check` passed. The full and fast runs
reported 13 existing Pydantic datetime deprecation warnings.

For integration into `main`, these test changes were replayed onto commit `67f73c2`,
excluding the two unrelated UI commits in the original timing baseline. The full suite
on that base passed all 1,204 cases in 195.94 seconds, with no skipped cases.

## Changes and coverage

- Extract sample observations once per session. Clone them before module-specific
  fingerprinting, fusion, grouping, and UI checks. Keep mutable stores separate.
- Run guarded CLI ingestion once per session. Give checkpoint, pair-report, and corpus-report
  tests independent copies of the closed SQLite database. Continue to run report, explain,
  incidents, and eval through real guarded subprocesses, with model and network audits.
- In the gateway report test, supply a clone of extracted observations at the extraction
  boundary. Continue to run the full report and ablation with the injected gateway embedder.
  Real extraction remains covered by the sample, corpus-build, CLI, and degradation tests.
- Encode each synthetic video input once per module, then copy its bytes into each test's
  private directory. Clear the production media cache for each test as before. Retain
  frame decoding, re-encoding, retry, broken-file, and cache behavior checks.
- Move metadata-purpose assertions into the existing key-frame and metadata test. Combine
  the two empty-demo runs, retaining both failure continuation and state restoration checks.
- Remove eight constructor/dump-only checks from `test_expectations.py`. Strengthen the
  versioned-storage test to compare the complete loaded model with the saved model.
- Remove five Group K tests that only asserted the constants made by their own builders,
  plus a redundant storage smoke test. Retain real wording search and present/absent
  verification. Keep discovery and grounded-output tests in `test_perception_expectations.py`.
- Reduce the local download-refusal timeout from 15 to 3 seconds. Retain its assertions on
  the saved landing page and excluded download. Reduce local server shutdown polling from
  0.5 seconds to 0.01 seconds, close sockets, and join the server threads.

Similar test bodies with different inputs are retained: redaction location matrices,
asset classification cases, and page/image perception registration checks cover distinct
inputs or plugins. Small tests in extraction, grounding, fusion, grouping, reporting,
security, and API modules remain because they check separate behavior or failure modes.

## Remaining expensive checks

- `test_sample_validation.py::test_degradation_on_real_files`: 30.31 s, including fixture setup and teardown.
- `test_corpus.py::test_clean_start_knows_campaigns_and_accepts_new_material`: 21.52 s, including fixture setup and teardown.
- `test_checkpoint.py::test_real_sample_cli_checkpoint_without_models`: 21.02 s, including fixture setup and teardown.
- `test_extract_video.py::test_hac19_done_when`: 17.64 s, including fixture setup and teardown.
- `test_gateway_embeddings.py::test_gateway_report_runs_every_part_on_the_injected_embedder`: 15.33 s, including fixture setup and teardown.

These checks retain real corpus export/import, file damage, model-report orchestration,
replay, or process-deadline behavior. Use the marker selections for faster local feedback;
run the full suite before merging changes to these paths.
