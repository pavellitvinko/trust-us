# Pattern library

The pattern stage turns technique cards into a versioned library and writes `pattern.score`
observations for the existing `pattern.mo` layer. It calls models, so it runs only on request.

```bash
uv run ftf ingest testing_sample     # extraction, relations and groups
uv run ftf perceive                  # page, image and technique reading (model)
uv run ftf run                       # refresh groups
uv run ftf patterns discover         # embed, cluster, induce, match (model)
uv run ftf run                       # rebuild fingerprints and groups with pattern.score
uv run ftf patterns list             # library, composites and candidate count
uv run ftf patterns stability        # compare clustering on two shuffled inputs
```

- A cluster enters the library only when its cited cards span two independent campaigns from the
  deterministic grouping. Two folders of one campaign do not count. Others remain candidates.
- Pattern IDs stay stable across versions. Merges, splits and analyst names are recorded.
- A `present` judgement needs a quote found in the text. Failures stay `not_observable`.
- Patterns are playbook tier and cannot merge campaigns.
