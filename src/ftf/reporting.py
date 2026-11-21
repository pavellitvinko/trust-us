"""Deterministic pair explanations, with provenance, for the CLI and interface.

Only stored observations and fingerprints are read. Reporting never extracts material,
embeds text or calls a model. A derived metric cites its inputs rather than pretending
that the metric itself was found in a source file.
"""

from __future__ import annotations

import json
from typing import Any

from . import pipeline
from .compare.minhash import shingles
from .compare.weights import weights
from .contracts import FingerprintKind, Purpose, RelationType, State, Tier
from .fusion import DEFAULT, prepare
from .fusion.fuse import decide, summarise
from .grouping import build_groups
from .layers._text import TEXT_NAMESPACES
from .registry import layers, load_plugins
from .store import Store

CONFIDENCE_MEANING = (
    "Rule-based confidence label, not a calibrated probability. It uses tier scores, "
    "coverage, independent agreeing groups and the decision rule."
)


def _input_observations(layer, observations):
    namespaces = getattr(layer, "namespaces", ())
    prefixes = getattr(layer, "prefixes", ())
    selected = [
        o
        for o in observations
        if o.namespace in namespaces or (prefixes and o.namespace.startswith(prefixes))
    ]
    if layer.name in ("text.shingle", "text.semantic", "narrative.skeleton"):
        # The text layers select one representation; other text is not evidence for them.
        preferred = next(
            (
                ns
                for ns in TEXT_NAMESPACES
                if any(
                    o.namespace == ns
                    and o.state is State.PRESENT
                    and o.purpose is not Purpose.RISK
                    and isinstance(o.value, str)
                    for o in selected
                )
            ),
            None,
        )
        selected = [
            o for o in selected if o.namespace not in TEXT_NAMESPACES or o.namespace == preferred
        ]
    return selected


def _source_record(o, ref):
    value = o.value
    # Long text remains in the original source; the report carries a labelled excerpt.
    excerpted = isinstance(value, str) and len(value) > 400
    return {
        "id": ref,
        "view_id": o.view_id,
        "namespace": o.namespace,
        "value": value[:400] if excerpted else value,
        "excerpted": excerpted,
        "value_characters": len(value) if isinstance(value, str) else None,
        "state": o.state.value,
        "purpose": o.purpose.value,
        "observation_confidence": o.confidence,
        "extractor": o.extractor,
        "version": o.version,
        "signal_id": o.signal_id,
        "source": o.source.model_dump(),
        "provenance": (
            "complete"
            if o.source.file and o.source.locator
            else "partial"
            if o.source.file or o.source.locator
            else "unavailable"
        ),
    }


def _matches(o, namespace, value, layer):
    """Resolve literal/normalised comparator values back to the observations that supply them."""
    if o.state is not State.PRESENT or o.purpose is Purpose.RISK:
        return False
    if namespace == "phash":
        return str(o.value).lower() == str(value).lower()
    if namespace == "pattern" and isinstance(o.value, dict):
        return o.value.get("pattern") == value
    if namespace in ("entity.cast", "entity.role") and isinstance(o.value, dict):
        entity = o.value.get("entity") or o.value.get("name")
        role = o.value.get("role") or ("persona" if o.namespace.endswith("persona") else "")
        candidate = f"{entity}|{str(role).lower()}" if namespace == "entity.cast" else role
        return candidate == value
    if namespace != o.namespace:
        return False
    normalise = getattr(layer, "normalise", None)
    candidate = str(o.value).strip()
    if normalise:
        candidate = normalise(o.namespace, candidate)
    return candidate == str(value)


def _sets(fp):
    if fp is not None and isinstance(fp.payload, list):
        return {(i["ns"], str(i["value"])) for i in fp.payload}
    return set()


def _confidence_basis(fused, relation, config):
    if relation is RelationType.DUPLICATE:
        return {
            "rule": "shared duplicate anchor, no contradiction, text absent or above threshold",
            "anchors": fused.anchors,
            "text_score": fused.duplicate_text,
            "text_threshold": config.duplicate_text,
            "label_rule": "high with observed text; medium when text is unavailable",
        }
    if relation is RelationType.INSUFFICIENT:
        return {"rule": "insufficient coverage", "coverage_threshold": config.min_coverage}
    if relation is RelationType.UNRELATED:
        return {
            "rule": "no tier reached its threshold",
            "high_coverage_threshold": 0.6,
            "coverage": fused.coverage,
        }
    tier = {
        RelationType.SAME_CAMPAIGN: Tier.IDENTITY,
        RelationType.SAME_FAMILY: Tier.KIT,
        RelationType.SAME_PLAYBOOK: Tier.PLAYBOOK,
    }[relation]
    threshold = {
        Tier.IDENTITY: config.identity,
        Tier.KIT: config.kit,
        Tier.PLAYBOOK: config.playbook,
    }[tier]
    if relation is RelationType.SAME_CAMPAIGN and fused.tiers[tier].score < threshold:
        tier, threshold = Tier.ARTIFACT, config.artifact
    summary = fused.tiers[tier]
    return {
        "rule": "tier score, independent agreeing groups and pair coverage",
        "tier": tier.value,
        "score": summary.score,
        "threshold": threshold,
        "agreeing_groups": summary.agreeing,
        "coverage": fused.coverage,
        "high_requires": {
            "score_at_least": 0.9,
            "agreeing_groups_at_least": 2,
            "coverage_at_least": 0.4,
        },
        "exception": "contradiction can cap identity/artifact support at family with medium confidence"
        if fused.contradictions and summary.score < threshold
        else None,
    }


def build_pair_report(store: Store, view_a: str, view_b: str, config=DEFAULT) -> dict[str, Any]:
    """Recompute one pair under current campaign rarity; keep the store unchanged."""
    load_plugins()
    views = {"a": store.get_view(view_a), "b": store.get_view(view_b)}
    # Match the final Method pass: count rarity over campaigns, not provisional domains.
    groups = build_groups(store, config, funnel=False)
    prepare(store, config, campaigns=groups.campaign_of)
    evidence = pipeline.compare_views(store, view_a, view_b)
    fused = summarise(evidence, config)
    rtype, confidence, reasons = decide(fused, config)
    observations = {side: store.observations(v.view_id) for side, v in views.items()}
    fingerprints = {
        side: {f.layer_id: f for f in store.fingerprints(view_id=v.view_id)}
        for side, v in views.items()
    }
    refs = {
        id(o): f"{side}:{i + 1}" for side, obs in observations.items() for i, o in enumerate(obs)
    }
    sources = {}

    def register(obs):
        out = []
        for o in obs:
            ref = refs[id(o)]
            sources[ref] = _source_record(o, ref)
            out.append(ref)
        return out

    def provenance(ref_lists):
        if not all(ref_lists):
            return "unavailable"
        return (
            "resolved"
            if all(sources[ref]["provenance"] == "complete" for ids in ref_lists for ref in ids)
            else "partial"
        )

    report = {
        "schema_version": "1",
        "objects": {
            side: {
                **v.model_dump(mode="json"),
                "incident_root": store.get_incident(v.incident_id).root,
            }
            for side, v in views.items()
        },
        "relation": rtype.value,
        "confidence": {
            "label": confidence or "unavailable",
            "meaning": CONFIDENCE_MEANING,
            "basis": _confidence_basis(fused, rtype, config),
        },
        "coverage": {
            "value": round(fused.coverage, 4),
            "comparable_groups": sum(len(t.groups) for t in fused.tiers.values()),
            "total_groups": sum(t.total_groups for t in fused.tiers.values()),
            "meaning": "Comparable correlation groups / active corpus groups, including demoted groups. "
            "This is not the fraction of all possible evidence collected.",
        },
        "reasons": reasons,
        "tiers": {
            tier.value: {
                "score": t.score,
                "coverage": t.coverage,
                "total_groups": t.total_groups,
                "groups": {
                    g: {
                        "score": score,
                        "weight": config.group_weight(g.removeprefix("demoted:"), tier),
                    }
                    for g, score in sorted(t.groups.items())
                },
            }
            for tier, t in fused.tiers.items()
        },
        "rules": config.to_dict(),
        "calculation": "Recomputed from stored fingerprints with current campaign-level rarity; "
        "no observations, fingerprints or relations are written.",
        "layers": [],
        "sources": sources,
        "ignored_observations": [],
        "limitations": [
            (
                "Only stored observations and fingerprints can be explained; material discarded by "
                "an extractor is not reconstructed."
            ),
            "Missing fingerprints mean not comparable, not proof that a feature is absent.",
        ],
    }
    from .enrich.warning_lists import listed_entities, warning_observations, warning_risk_reasons

    warnings = {side: warning_observations(store, v.view_id) for side, v in views.items()}
    entities = {side: listed_entities(items) for side, items in warnings.items()}
    report["warning_lists"] = {
        "observations": warnings,
        "risk_reasons": {side: warning_risk_reasons(items) for side, items in warnings.items()},
        "shared_listed_entities": [
            {
                "entity_id": entity,
                "name": entities["a"][entity][0]["value"]["matches"][0]["record"]["name"],
            }
            for entity in sorted(entities["a"].keys() & entities["b"].keys())
        ],
        "meaning": "Shared listed entities are context only, not proof of a shared campaign. "
        "A warning-list miss says nothing about legitimacy. Fuzzy matches require verification.",
    }
    for e in evidence:
        layer = layers.get(e.layer_id)
        fps = {side: fingerprints[side].get(layer.name) for side in views}
        inputs = {side: _input_observations(layer, observations[side]) for side in views}
        if layer.name in ("asset.perceptual", "ad.creative"):
            from .layers.perceptual import _whole_image

            inputs = {
                side: [
                    o
                    for o in obs
                    if (
                        not _whole_image(views[side], o)
                        if layer.name == "asset.perceptual"
                        else o.namespace == "creative.phash" or _whole_image(views[side], o)
                    )
                ]
                for side, obs in inputs.items()
            }
        input_refs = {side: register(obs) for side, obs in inputs.items()}

        def source_refs(side, ns, value, inputs=inputs, layer=layer):
            return register([o for o in inputs[side] if _matches(o, ns, value, layer)])

        def sourced(ns, value, side, source_refs=source_refs):
            resolved = source_refs(side, ns, value)
            return {
                "value": value,
                "sources": resolved,
                "provenance": provenance([resolved]),
            }

        row = {
            "layer_id": layer.name,
            "tier": layer.tier.value,
            "group": layer.group,
            "layer_version": layer.version,
            "comparator": e.comparator,
            "comparator_version": e.version,
            "fingerprint_versions": {side: fp.version if fp else None for side, fp in fps.items()},
            "status": "compared" if e.comparable else "not_comparable",
            "score": e.score if e.comparable else None,
            "inputs": input_refs,
            "shared": [],
            "differing": [],
            "ignored": [],
            "missing": [],
            "details": {},
        }
        for side, fp in fps.items():
            if fp is None:
                row["missing"].append(
                    {
                        "side": side,
                        "reason": "no stored fingerprint",
                        "expected_namespaces": list(getattr(layer, "namespaces", ())),
                        "observed_states": sorted({o.state.value for o in inputs[side]}),
                        "source_refs": input_refs[side],
                    }
                )
        if not e.comparable and all(fps.values()):
            row["missing"].append(
                {
                    "side": "pair",
                    "reason": "comparator rejected incompatible inputs",
                    "source_refs": input_refs["a"] + input_refs["b"],
                }
            )
        for item in e.shared:
            if item.get("weight", 1) <= 0:
                continue
            ns, value = item["ns"], item["value"]
            derived = ns in ("jaccard", "cosine", "element")
            matched = {
                side: register(
                    [
                        o
                        for o in inputs[side]
                        if (
                            o.state is not State.NOT_OBSERVABLE
                            if ns == "element"
                            else o.state is State.PRESENT and o.purpose is not Purpose.RISK
                        )
                    ]
                )
                if derived
                else source_refs(side, ns, item.get("other", value) if side == "b" else value)
                for side in views
            }
            row["shared"].append(
                {
                    **item,
                    "evidence_kind": "derived_metric" if derived else "matched_value",
                    "sources": matched,
                    "provenance": provenance(list(matched.values())),
                    "demoted_to_kit": (ns, str(value)) in weights.demoted,
                }
            )
        # Show every set difference, including namespaces observed on only one side.
        sets = {side: _sets(fp) for side, fp in fps.items()}
        for ns in sorted({ns for side in sets.values() for ns, _ in side}):
            values = {
                side: sorted(
                    v for n, v in sets[side] - sets[other] if n == ns and weights.weight(n, v) > 0
                )
                for side, other in (("a", "b"), ("b", "a"))
            }
            if any(values.values()):
                row["differing"].append(
                    {
                        "ns": ns,
                        **{side: [sourced(ns, v, side) for v in values[side]] for side in views},
                        "exclusive_conflict": ns in config.exclusive_namespaces
                        and all(values.values()),
                    }
                )
        for ns, value in sorted(sets["a"] & sets["b"]):
            if weights.weight(ns, value) == 0:
                row["ignored"].append(
                    {
                        "ns": ns,
                        "value": value,
                        "reason": "learned rarity stoplist"
                        if (ns, value) in weights.learned_stoplist
                        else "seed stoplist or zero base weight",
                        "sources": {side: source_refs(side, ns, value) for side in views},
                    }
                )
        if layer.name in ("text.shingle", "kit.dom") and all(fps.values()):
            if layer.name == "text.shingle":
                from .layers._text import body_text

                tokens = {side: shingles(body_text(obs)) for side, obs in inputs.items()}
                unit = "normalised word 5-gram"
            else:
                from .layers.kit import _FRAMEWORK_CLASS_PREFIXES

                tokens = {}
                for side, obs in inputs.items():
                    values = [
                        v
                        for o in obs
                        if o.state is State.PRESENT and o.purpose is not Purpose.RISK
                        for v in (o.value if isinstance(o.value, list) else [o.value])
                        if isinstance(v, str)
                    ]
                    tokens[side] = {
                        v.strip()
                        for v in values
                        if v.strip() and not v.strip().startswith(_FRAMEWORK_CLASS_PREFIXES)
                    }
                unit = "CSS class"
            union = tokens["a"] | tokens["b"]
            row["details"] = {
                "unit": unit,
                "shared_values": sorted(tokens["a"] & tokens["b"]),
                "only_a": sorted(tokens["a"] - tokens["b"]),
                "only_b": sorted(tokens["b"] - tokens["a"]),
                "exact_jaccard": len(tokens["a"] & tokens["b"]) / len(union) if union else 0.0,
                "metric_note": "Layer score is a MinHash estimate; these values are reconstructed "
                "from stored inputs for inspection.",
                "sources": input_refs,
            }
        elif layer.comparator == "phash" and all(fps.values()):
            for side in views:
                matched = {
                    item.get("other", item["value"]) if side == "b" else item["value"]
                    for item in e.shared
                }
                unmatched = sorted(set(fps[side].payload.get("values", [])) - matched)
                row["details"]["unmatched_" + side] = [sourced("phash", v, side) for v in unmatched]
        elif layer.kind is FingerprintKind.VECTOR:
            row["details"]["models"] = {
                side: fp.payload.get("model") if fp else None for side, fp in fps.items()
            }
            if any(str(model).startswith("fake-") for model in row["details"]["models"].values()):
                row["details"]["metric_note"] = (
                    "Local deterministic hash vectors; this does not validate semantic model quality."
                )
        report["layers"].append(row)
    # Explain observed common material that never entered a layer, including libraries
    # classified as noise. Do not invent an audit of inputs an extractor discarded.
    used = {
        ref
        for row in report["layers"]
        for refs_in_side in row["inputs"].values()
        for ref in refs_in_side
    }
    by_value = {}
    for side, obs in observations.items():
        for o in obs:
            if o.state is State.PRESENT:
                key = (o.namespace, json.dumps(o.value, sort_keys=True, ensure_ascii=False))
                by_value.setdefault(key, {"a": [], "b": []})[side].append(o)
    for (ns, _), found in sorted(by_value.items()):
        if not all(found.values()):
            continue
        matching = found["a"] + found["b"]
        if all(o.purpose is Purpose.RISK for o in matching):
            reason = "risk-only observations do not establish linkage"
        elif all(refs[id(o)] not in used for o in matching):
            reason = "not selected as input by any registered linkage layer"
        else:
            continue
        report["ignored_observations"].append(
            {
                "ns": ns,
                "value": found["a"][0].value,
                "reason": reason,
                "sources": {side: register(obs) for side, obs in found.items()},
            }
        )
    return report


def _display(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("\n", " ").replace("|", "\\|").replace("`", "\\`")


def render_pair_report(report: dict) -> str:
    """Human-readable Markdown. JSON retains full derived sets; Markdown labels its examples."""
    sources = report["sources"]

    def refs(ids):
        if not ids:
            return "provenance unavailable"
        return "; ".join(
            f"[{ref}] {_display(sources[ref]['source']['file'] or '(file not recorded)')} "
            f"— {_display(sources[ref]['source']['locator'] or '(locator not recorded)')}"
            for ref in ids
        )

    cov = report["coverage"]
    lines = ["# Pair comparison report", "", "## Objects", ""]
    for side, obj in report["objects"].items():
        lines += [
            (
                f"- **{side.upper()}**: {_display(obj['label'])} (`{obj['view_id']}`); "
                f"incident `{obj['incident_id']}`, kind `{obj['kind']}`."
            ),
            (
                f"  Primary: {_display(obj['primary'])}; HAR: {_display(obj['har'])}; "
                f"root: {_display(obj['incident_root'])}."
            ),
        ]
    warnings = report.get("warning_lists")
    if warnings:
        lines += ["", "## Warning lists", "", warnings["meaning"], ""]
        for side, items in warnings["observations"].items():
            for obs in items:
                value = obs["value"]
                lines += [
                    (
                        f"- {side.upper()} {_display(value['source'])}: {_display(value['message'])} "
                        f"— {_display((value['input'] or {}).get('value'))} [{obs['state']}]"
                    ),
                    f"  Snapshot: {_display(value['snapshot'])}",
                    f"  Input provenance: {_display(value['input'])}",
                    f"  Matches: {_display(value.get('matches', []))}",
                    f"  Refresh error: {_display(value['refresh_error'])}",
                ]
        names = "; ".join(_display(e["name"]) for e in warnings["shared_listed_entities"])
        lines += [f"Shared listed entities: {names or 'none'}"]
    lines += [
        "",
        "## Decision",
        "",
        (
            f"relation: {report['relation']}  confidence={report['confidence']['label']}  "
            f"coverage={cov['value']:.2f}"
        ),
        "",
        f"Coverage: **{cov['comparable_groups']} / {cov['total_groups']} groups**.",
        report["confidence"]["meaning"],
        "Confidence basis: " + _display(report["confidence"]["basis"]),
        cov["meaning"],
        "",
        *[f"- {reason}" for reason in report["reasons"]],
        "",
        "## Tier scores and independent groups",
        "",
    ]
    for tier, summary in report["tiers"].items():
        groups = (
            "; ".join(
                f"{g}: score={v['score']:.3f}, weight={v['weight']:.2f}"
                for g, v in summary["groups"].items()
            )
            or "none comparable"
        )
        lines += [f"- **{tier}={summary['score']:.3f}** — {groups}"]
    lines += ["", "## Evidence, differences and missing features", ""]
    for row in report["layers"]:
        lines += [
            f"### {row['layer_id']}",
            "",
            (
                f"Tier {row['tier']}; group `{row['group']}`; "
                f"{row['comparator']}@{row['comparator_version']}; layer@{row['layer_version']}."
            ),
            f"Status: **{row['status']}**; score: {row['score']}.",
            "",
        ]
        for item in row["shared"]:
            note = " (demoted to kit)" if item["demoted_to_kit"] else ""
            lines += [
                (
                    f"- {item['evidence_kind']}: `{_display(item['ns'])}` = "
                    f"`{_display(item['value'])}`; weight={item.get('weight', 'n/a')}{note}."
                )
            ]
            if "other" in item:
                lines += [f"  B value: `{item['other']}`; Hamming distance: {item['distance']}."]
            for side in ("a", "b"):
                lines += [f"  {side.upper()} source: {refs(item['sources'][side])}"]
        for difference in row["differing"]:
            conflict = " **exclusive contradiction**" if difference["exclusive_conflict"] else ""
            lines += [f"- Differs `{difference['ns']}`{conflict}:"]
            for side in ("a", "b"):
                items = difference[side]
                if not items:
                    lines += [f"  {side.upper()}: no differing value recorded."]
                for item in items:
                    lines += [
                        f"  {side.upper()}: `{_display(item['value'])}` — {refs(item['sources'])}"
                    ]
        for item in row["ignored"]:
            lines += [
                f"- Ignored `{item['ns']}={_display(item['value'])}`: {item['reason']}.",
                f"  A: {refs(item['sources']['a'])}",
                f"  B: {refs(item['sources']['b'])}",
            ]
        for missing in row["missing"]:
            lines += [
                (
                    f"- Missing / not comparable ({missing['side']}): {missing['reason']}; "
                    f"expected inputs: {', '.join(missing.get('expected_namespaces', [])) or 'see layer inputs'}."
                )
            ]
        details = row["details"]
        if "shared_values" in details:
            lines += [
                (
                    f"- Exact input overlap ({details['unit']}): "
                    f"{len(details['shared_values'])} shared, {len(details['only_a'])} only A, "
                    f"{len(details['only_b'])} only B; Jaccard={details['exact_jaccard']:.4f}."
                )
            ]
            for field in ("shared_values", "only_a", "only_b"):
                lines += [
                    f"  {field} examples (up to 5; full lists in JSON): "
                    + _display(details[field][:5])
                ]
        for side in ("a", "b"):
            for item in details.get("unmatched_" + side, []):
                lines += [
                    f"- Unmatched {side.upper()} hash `{item['value']}`: {refs(item['sources'])}"
                ]
        if "models" in details:
            lines += [f"- Vector models: {_display(details['models'])}."]
        if "metric_note" in details:
            lines += [details["metric_note"]]
        for side in ("a", "b"):
            if row["inputs"][side]:
                lines += [f"- {side.upper()} input sources: {refs(row['inputs'][side])}"]
        lines += [""]
    lines += ["## Observed matches excluded from linkage", ""]
    if not report["ignored_observations"]:
        lines += ["No additional shared stored observations were excluded."]
    for item in report["ignored_observations"]:
        lines += [
            f"- `{item['ns']}={_display(item['value'])}` — {item['reason']}.",
            f"  A: {refs(item['sources']['a'])}",
            f"  B: {refs(item['sources']['b'])}",
        ]
    lines += ["", "## Source observations", ""]
    for ref, obs in sources.items():
        suffix = f" (excerpt of {obs['value_characters']} characters)" if obs["excerpted"] else ""
        lines += [
            (
                f"- **{ref}** `{obs['namespace']}` = {_display(obs['value'])}{suffix}; "
                f"state={obs['state']}; observation confidence={obs['observation_confidence']}; "
                f"{obs['extractor']}@{obs['version']}; signal {obs['signal_id']}."
            ),
            f"  {refs([ref])}",
        ]
    lines += [
        "",
        "## Scope and limitations",
        "",
        report["calculation"],
        "",
        *[f"- {item}" for item in report["limitations"]],
        "",
    ]
    return "\n".join(lines)
