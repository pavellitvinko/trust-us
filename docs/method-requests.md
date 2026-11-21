# Method track: requests to the owners of the frozen files

Collected during the build (2026-11-21). None of these blocks the Method track: every
feature below works today with the frozen files as they are. They are improvements for
after the hackathon or for whoever owns `contracts.py`, `registry.py`, `store.py`,
`pipeline.py`.

| # | File | Request | Why | Workaround in place |
| --- | --- | --- | --- | --- |
| 1 | `store.py` | A `groups` table: `(level, group_id, view_id)` with `put_groups(level, mapping)` and `groups(level)` | Campaigns, families and playbooks are recomputed from relations on every call to `build_groups`; the interface cannot show a stable group id across runs | `grouping.build_groups(store)` recomputes on demand; ids are `c:<smallest view>`, `f:<…>`, `p:<…>`, which are stable for a given corpus |
| 2 | `store.py` | `delete_incident(incident_id)` | Leave-one-incident-out on the real sample needs to remove a folder and re-add it; today that is only possible on the fixtures, which are rebuilt in memory | `eval/loio.py` runs on the fixture file; on the sample, re-ingest without the folder |
| 3 | `pipeline.py` | `run_compare_all(store, pairs=None)` accepting a candidate-pair iterable, or dropping the stage from `ftf run` | The fusion stage recompares every pair with fitted weights and stores the evidence itself, so the brute-force stage runs twice per `ftf run` | `fusion.run_fuse(store, pairs=…)` is the one that matters; the extra pass is cheap at sample size |
| 4 | `contracts.py` | A `kind` on `Relation` or a `FunnelEdge` contract | Funnel and co-captured edges live in a Method-track dataclass and are not persisted | `grouping.funnel.FunnelEdge`; `funnel.mismatch` observations are persisted through the normal observation table |
| 5 | `registry.py` | `Layer.kinds: tuple[ViewKind, ...]` so a layer can declare which view kinds it applies to | `kit.absence` must only compare views of the same kind of page; today the comparator checks a `kind` field inside the payload | payload carries `"kind"`, comparator returns `comparable=False` on a mismatch |

Nothing in `contracts.py` needs to change for the Method track to ship.
