"""Corpus report and export: one read-only bundle built from stored results.

Numbers and tables come from code. Nothing here calls a model or the network. Whole-page text is
left out of exports and secrets are hashed, because a bundle is easy to pass around.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import zipfile
from collections import Counter, defaultdict
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any

from .compare.weights import weights
from .config import get_settings
from .contracts import Purpose, Relation, RelationType, State
from .fusion import DEFAULT, FusionConfig, prepare
from .grouping import build_groups, rollup
from .registry import layers, load_plugins
from .reporting import build_pair_report, render_pair_report
from .store import Store

SCHEMA = "1"
LINKED = (
    RelationType.DUPLICATE,
    RelationType.SAME_CAMPAIGN,
    RelationType.SAME_FAMILY,
    RelationType.SAME_PLAYBOOK,
)
TEXT_NAMESPACES = frozenset(
    {
        "page.clean_text",
        "page.body_text",
        "text.body",
        "page.read_text",
        "expect.read_text",
        "page.comments",
        "dom.comments",
    }
)
MODEL_SECTIONS = {
    "page_reading": "page.read_text",
    "technique_cards": "technique.card",
    "expected_elements": "expect.element",
    "patterns": "pattern.score",
}
_SECRET_PARTS = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "apikey",
        "accesskey",
        "session",
        "sessionid",
        "sessid",
        "phpsessid",
        "jsessionid",
        "sid",
        "auth",
        "authorization",
        "cookie",
        "csrf",
        "xsrf",
        "jwt",
    }
)
_SECRET_PAIRS = frozenset({("api", "key"), ("access", "key"), ("private", "key")})
_PARAM = re.compile(r"([A-Za-z_$][\w.$-]{0,63})=([^&\s\"'<>#,;]+)")
_ENCODED_PARAM = re.compile(r"(?i)(%3F|%26)([\w.-]{1,64})%3D((?:(?!%26)[^&\s\"'<>#]){1,4096})")
_TRUNCATED = re.compile(r"(\S{6,})…")
MIN_GLOBAL_SECRET_CHARS = 8  # shorter values could match ids and refs by accident
_RANK = {t: i for i, t in enumerate(RelationType)}


def _hash(value: str) -> str:
    if value.startswith("[REDACTED:sha256:"):
        return value
    return f"[REDACTED:sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}]"


def _secret_name(name: str) -> bool:
    """True when a whole part of the name is secret-like: `auth_token` yes, `author` no."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    parts = [p.rstrip("0123456789") for p in re.split(r"[^a-z0-9]+", spaced) if p]
    return any(p in _SECRET_PARTS or p.endswith(("token", "password")) for p in parts) or any(
        pair in _SECRET_PAIRS for pair in pairwise(parts)
    )


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _walk(value: Any, clean) -> Any:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list | tuple):
        return [_walk(item, clean) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, clean) for key, item in value.items()}
    return value


def _secret_param(name: str) -> bool:
    return name.lower() == "key" or _secret_name(name)


def is_secret_namespace(namespace: str) -> bool:
    return _secret_name(namespace.rsplit(".", 1)[-1])


def sensitive_values(observations) -> tuple[set[str], set[str]]:
    """Secret values to replace anywhere, and page-text items to replace as whole strings."""
    secrets, text_items = set(), set()
    for o in observations:
        if is_secret_namespace(o.namespace):
            # `raw` can be a whole URL, which is cleaned by parameter instead.
            secrets.update(v for v in _strings(o.value) if len(v) >= MIN_GLOBAL_SECRET_CHARS)
        if o.namespace in TEXT_NAMESPACES:
            text_items.update(_strings(o.value))
    return secrets, text_items


def make_cleaner(secrets: set[str], text_items: set[str], roots: dict[str, str] | None = None):
    """A string cleaner. Equal values stay equal, so hashed identifiers can still be compared."""
    roots = roots or {}
    ordered = sorted(secrets, key=len, reverse=True)
    ordered_roots = sorted(roots, key=len, reverse=True)

    def param(match: re.Match[str]) -> str:
        name, value = match.groups()
        return f"{name}={_hash(value)}" if _secret_param(name) else match.group(0)

    def encoded(match: re.Match[str]) -> str:
        mark, name, value = match.groups()
        return f"{mark}{name}%3D{_hash(value)}" if _secret_param(name) else match.group(0)

    def truncated(match: re.Match[str]) -> str:
        token = match.group(1).rsplit("=", 1)[-1]
        full = next((x for x in ordered if x.startswith(token)), None)
        return match.group(0) if full is None else match.group(0).replace(token, _hash(full))

    def clean(text: str) -> str:
        if text in text_items:
            return _hash(text)
        for secret in ordered:
            text = text.replace(secret, _hash(secret))
        for root in ordered_roots:
            text = text.replace(root, roots[root])
        text = _ENCODED_PARAM.sub(encoded, _PARAM.sub(param, text))
        return _TRUNCATED.sub(truncated, text)

    return clean


def _cleaner(store: Store):
    """Hash secrets and page-text items and remove local folders from any exported string."""
    roots = {
        incident.root: f"<incident:{incident.incident_id}>"
        for incident in store.incidents()
        if incident.root not in (".", "")
    }
    rows = [o for view in store.views() for o in store.observations(view.view_id)]
    secrets, text_items = sensitive_values(rows)
    return make_cleaner(secrets, text_items, roots)


def clean_payload(payload: Any, observations) -> Any:
    """Apply the export's hashing to one view's fingerprint, so it can match shipped ones."""
    secrets, text_items = sensitive_values(observations)
    return _walk(payload, make_cleaner(secrets, text_items))


def _strip_page_text(pair: dict[str, Any]) -> dict[str, Any]:
    """Keep the evidence and its counts, without text a reader could rebuild the page from."""
    pair["ignored_observations"] = [
        item for item in pair["ignored_observations"] if item["ns"] not in TEXT_NAMESPACES
    ]
    for record in pair["sources"].values():
        if record["namespace"] in TEXT_NAMESPACES:
            record["value"] = "[omitted: page text]"
            record["excerpted"] = False
        elif _secret_name(record["namespace"].rsplit(".", 1)[-1]) and isinstance(
            record["value"], str
        ):
            record["value"] = _hash(record["value"])
    for row in pair["layers"]:
        details = row["details"]
        if "shared_values" in details:
            for field in ("shared_values", "only_a", "only_b"):
                details[field] = [
                    "#" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:10]
                    for token in details[field]
                ]
            details["metric_note"] = (
                f"{details.get('metric_note', '')} Text tokens are shown as hashes in exports."
            ).strip()
    return pair


def _commit() -> str:
    """The commit of this code, not of whatever repository the command was started in."""
    here = Path(__file__).resolve()
    try:

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args],
                cwd=here.parent,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()

        if (Path(git("rev-parse", "--show-toplevel")) / "src/ftf/corpus_report.py") != here:
            return "unknown"
        return git("rev-parse", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _provenance(store: Store) -> dict[str, Any]:
    settings = get_settings()
    artifacts = sorted(
        (a.incident_id, a.path, a.sha256)
        for incident in store.incidents()
        for a in store.artifacts(incident.incident_id)
    )
    cache = settings.model_cache_dir
    entries = (
        {d.name: sum(1 for _ in d.glob("*.json")) for d in sorted(cache.iterdir()) if d.is_dir()}
        if cache.is_dir()
        else {}
    )
    return {
        "commit": _commit(),
        "corpus_sha256": hashlib.sha256(json.dumps(artifacts).encode()).hexdigest(),
        "artifacts": len(artifacts),
        "execution_mode": "read-only over stored results; this report made no model or network call",
        "models_configured": {
            "text": settings.openai_model,
            "embedding": settings.embedding_model,
            "video": settings.video_model,
        },
        "model_cache_entries_by_task": entries,
    }


def _section_status(store: Store) -> dict[str, dict[str, Any]]:
    views = store.views()
    out = {}
    for section, namespace in MODEL_SECTIONS.items():
        ran, usable = 0, 0
        for view in views:
            rows = [o for o in store.observations(view.view_id) if o.namespace == namespace]
            ran += bool(rows)
            usable += any(o.state is not State.NOT_OBSERVABLE for o in rows)
        status = "not_run" if not ran else "unavailable" if not usable else "available"
        out[section] = {"status": status, "views_with_results": usable, "views": len(views)}
    return out


def _pattern_table(settings_dir: Path) -> dict[str, Any]:
    from .patterns.library import PatternLibraryStore

    path = settings_dir / "patterns" / "library"
    version = PatternLibraryStore(path).latest_version() if path.is_dir() else None
    if version is None:
        return {"status": "not_run", "patterns": [], "composites": []}
    return {
        "status": "available",
        "version": version.version_id,
        "patterns": [
            {
                "id": p.pattern_id,
                "name": p.name,
                "description": p.description,
                "examples": len(p.positive_examples),
                "retired": bool(p.metadata and p.metadata.analyst_retired_at),
            }
            for p in version.patterns
        ],
        "composites": [
            {
                "id": c.composite_id,
                "name": c.name,
                "patterns": c.technique_ids,
                "views": c.frequency,
            }
            for c in version.composites
        ],
    }


def _strongest(r: Relation) -> float:
    return max(r.tier_scores.values(), default=0.0)


def _defining(relations: list[Relation], members: set[str], limit: int = 5) -> list[dict]:
    inside = [r for r in relations if r.view_a in members and r.view_b in members]
    inside = [r for r in inside if r.type in LINKED]
    inside.sort(key=lambda r: (_RANK[r.type], -_strongest(r), r.view_a, r.view_b))
    return [
        {
            "a": r.view_a,
            "b": r.view_b,
            "relation": r.type.value,
            "confidence": r.confidence,
            "reasons": r.reasons,
        }
        for r in inside[:limit]
    ]


def _group_links(views: list[str], direct: dict, funnel: set) -> list[dict]:
    out = []
    for a, b in combinations(sorted(views), 2):
        relation = direct.get((a, b))
        is_direct = relation in (RelationType.DUPLICATE, RelationType.SAME_CAMPAIGN)
        is_funnel = (a, b) in funnel
        if is_direct and is_funnel:
            link = "direct_and_funnel"
        elif is_direct:
            link = "direct"
        elif is_funnel:
            link = "funnel"
        else:
            link = "transitive"
        out.append({"a": a, "b": b, "link": link})
    return out


def _edge(edge) -> dict[str, Any]:
    return {"source": edge.source, "target": edge.target, "evidence": list(edge.evidence)}


def build_corpus_report(store: Store, config: FusionConfig = DEFAULT) -> dict[str, Any]:
    """The whole report as data. CLI, interface and files all render this one object."""
    load_plugins()
    views = store.views()
    relations = store.relations()
    groups = build_groups(store, config, persist_funnel=False)
    direct = {(r.view_a, r.view_b): r.type for r in relations}
    funnel_pairs = {
        tuple(sorted((e.source, e.target)))
        for found, _ in groups.funnel_edges.values()
        for e in found
    }
    linked = sorted(
        (r for r in relations if r.type in LINKED),
        key=lambda r: (_RANK[r.type], -_strongest(r), r.view_a, r.view_b),
    )
    pairs = [_strip_page_text(build_pair_report(store, r.view_a, r.view_b, config)) for r in linked]
    prepare(store, config, campaigns=groups.campaign_of)

    campaigns = [
        {
            "id": cid,
            "family": groups.family_of.get(cid),
            "views": members,
            "defining_evidence": _defining(relations, set(members)),
            "links": _group_links(members, direct, funnel_pairs),
        }
        for cid, members in sorted(groups.campaigns.items())
    ]
    families = []
    for fid, cids in sorted(groups.families.items()):
        members = {v for c in cids for v in groups.campaigns[c]}
        families.append(
            {
                "id": fid,
                "campaigns": cids,
                "views": sorted(members),
                "defining_evidence": _defining(relations, members),
            }
        )
    playbooks = [
        {"id": pid, "views": members, "defining_evidence": _defining(relations, set(members))}
        for pid, members in sorted(groups.playbooks.items())
    ]

    incident_of = {v.view_id: v.incident_id for v in views}
    matrix: dict[tuple[str, str], RelationType] = {}
    for r in relations:
        key = tuple(sorted((incident_of[r.view_a], incident_of[r.view_b])))
        if key not in matrix or _RANK[r.type] < _RANK[matrix[key]]:
            matrix[key] = r.type

    below_folder = []
    for summary in rollup(store, groups, groups.funnel_edges):
        below_folder.append(
            {
                "incident": summary.incident_id,
                "campaigns_in_folder": {c: v for c, v in summary.campaigns.items()},
                "funnel_edges": [_edge(e) for e in summary.funnel],
                "co_captured_display_only": [_edge(e) for e in summary.co_captured],
                "ad_landing_mismatches": summary.mismatches,
            }
        )

    ignored: dict[tuple[str, str], dict] = {}
    contradictions, demotions = [], []
    not_comparable: Counter = Counter()
    for pair in pairs:
        a, b = pair["objects"]["a"]["view_id"], pair["objects"]["b"]["view_id"]
        for item in pair["ignored_observations"]:
            key = (item["ns"], json.dumps(item["value"], sort_keys=True, ensure_ascii=False))
            entry = ignored.setdefault(
                key,
                {"ns": item["ns"], "value": item["value"], "reason": item["reason"], "pairs": 0},
            )
            entry["pairs"] += 1
        for row in pair["layers"]:
            for item in row["ignored"]:
                if item.get("ns") in TEXT_NAMESPACES:
                    continue
                key = (
                    str(item.get("ns")),
                    json.dumps(item.get("value"), sort_keys=True, ensure_ascii=False),
                )
                entry = ignored.setdefault(
                    key,
                    {
                        "ns": item.get("ns"),
                        "value": item.get("value"),
                        "reason": item.get("reason") or "zero weight in this layer",
                        "pairs": 0,
                    },
                )
                entry["pairs"] += 1
        for reason in pair["reasons"]:
            if reason.startswith("contradiction"):
                contradictions.append({"a": a, "b": b, "reason": reason})
            elif reason.startswith("demoted"):
                demotions.append({"a": a, "b": b, "reason": reason})
        for row in pair["layers"]:
            if row["status"] == "not_comparable":
                not_comparable[row["layer_id"]] += 1

    # Stoplisted values shared by any views, including pairs that never became links.
    holders: dict[tuple[str, str], set[str]] = defaultdict(set)
    for view in views:
        for o in store.observations(view.view_id):
            usable = o.state is State.PRESENT and o.purpose is not Purpose.RISK
            if usable and isinstance(o.value, str) and o.namespace not in TEXT_NAMESPACES:
                holders[(o.namespace, o.value)].add(view.view_id)
    for (ns, value), holding in holders.items():
        if len(holding) > 1 and weights.is_stoplisted(ns, value):
            key = (ns, json.dumps(value, sort_keys=True, ensure_ascii=False))
            entry = ignored.setdefault(
                key,
                {"ns": ns, "value": value, "reason": "stoplisted common value", "pairs": 0},
            )
            entry["views"] = len(holding)

    sections = _section_status(store)
    layer_coverage = {
        layer.name: len({f.view_id for f in store.fingerprints(layer_id=layer.name)})
        for layer in layers.all()
    }
    types = Counter(r.type.value for r in relations)
    open_questions = [
        f"{name.replace('_', ' ')}: {info['status']}; results for "
        f"{info['views_with_results']} of {info['views']} views"
        for name, info in sections.items()
        if info["status"] != "available" or info["views_with_results"] < info["views"]
    ]
    if types.get(RelationType.INSUFFICIENT.value):
        open_questions.append(
            f"{types[RelationType.INSUFFICIENT.value]} pairs have insufficient evidence."
        )
    no_layer = [name for name, count in layer_coverage.items() if count == 0]
    if no_layer:
        open_questions.append("Layers with no fingerprint: " + ", ".join(no_layer))
    disagreements = [
        {
            "a": p["objects"]["a"]["view_id"],
            "b": p["objects"]["b"]["view_id"],
            "stored": p["stored_relation"]["relation"],
            "recomputed": p["relation"],
            "stored_confidence": p["stored_relation"]["confidence"],
            "recomputed_confidence": p["confidence"]["label"],
        }
        for p in pairs
        if p["stored_relation"]["agrees"] is False
    ]
    if disagreements:
        open_questions.append(
            f"{len(disagreements)} linked pairs recompute to a different relation or label "
            "than the stored one: rerun the Method pass before relying on the stored table."
        )

    return {
        "schema_version": SCHEMA,
        "provenance": _provenance(store),
        "reading_guide": {
            "observed": "Values read from the material, with file and locator.",
            "calculated": "Scores, coverage and confidence labels. Confidence is rule-based, "
            "not a calibrated probability.",
            "direct": "The two views have their own stored campaign or duplicate relation.",
            "funnel": "One view leads to the other in a stored navigation path.",
            "transitive": "The views share a campaign only through other views.",
            "unresolved": "Not run, unavailable or not comparable. This never means absent.",
        },
        "restriction": "Private project material. Do not publish or redistribute.",
        "inventory": {
            "incidents": len(store.incidents()),
            "views_by_kind": dict(sorted(Counter(v.kind.value for v in views).items())),
            "views": [
                {
                    "view_id": v.view_id,
                    "incident": v.incident_id,
                    "kind": v.kind.value,
                    "label": v.label,
                    "file": v.primary or v.har,
                }
                for v in views
            ],
        },
        "campaigns": campaigns,
        "families": families,
        "playbooks": playbooks,
        "relation_counts": dict(sorted(types.items())),
        "pair_matrix": [
            {"incident_a": a, "incident_b": b, "strongest_relation": t.value}
            for (a, b), t in sorted(matrix.items())
        ],
        "linked_pairs": [
            {
                "a": p["objects"]["a"]["view_id"],
                "b": p["objects"]["b"]["view_id"],
                "relation": p["relation"],
                "confidence": p["confidence"]["label"],
                "coverage": p["coverage"]["value"],
                "comparable_groups": p["coverage"]["comparable_groups"],
                "total_groups": p["coverage"]["total_groups"],
                "decision_path": p["confidence"]["basis"].get("path"),
                "agrees_with_stored": p["stored_relation"]["agrees"],
            }
            for p in pairs
        ],
        "recomputation_disagreements": disagreements,
        "below_folder": below_folder,
        "patterns": _pattern_table(get_settings().data_dir),
        "model_sections": sections,
        "ignored": sorted(ignored.values(), key=lambda x: (x["ns"], str(x["value"]))),
        "contradictions": contradictions,
        "demotions": demotions,
        "rejected_merges": [list(item) for item in groups.rejected_merges],
        "not_comparable_layers_in_linked_pairs": dict(sorted(not_comparable.items())),
        "layer_coverage": layer_coverage,
        "open_questions": open_questions,
        "pairs": pairs,
    }


def _cell(value: Any) -> str:
    text = " ".join(str(value).split()).replace("\\", "\\\\")
    text = text.replace("|", "\\|").replace("<", "&lt;")
    return text.replace("[", "\\[").replace("`", "\\`")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["None.", ""]
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return [*lines, ""]


def render_corpus_report(report: dict[str, Any]) -> str:
    p = report["provenance"]
    lines = [
        "# Corpus report",
        "",
        f"> {report['restriction']}",
        "",
        f"- Commit: `{p['commit']}`",
        f"- Corpus SHA-256: `{p['corpus_sha256']}` ({p['artifacts']} files)",
        f"- Execution: {p['execution_mode']}",
        f"- Models configured: {_cell(p['models_configured'])}",
        f"- Model cache entries: {_cell(p['model_cache_entries_by_task'] or 'none')}",
        "",
        "## How to read this",
        "",
        *[f"- **{k}:** {v}" for k, v in report["reading_guide"].items()],
        "",
        "## Inventory",
        "",
        (
            f"{report['inventory']['incidents']} incidents, views: "
            f"{_cell(report['inventory']['views_by_kind'])}."
        ),
        "",
        *_table(
            ["View", "Incident", "Kind", "Label", "File"],
            [
                [v["view_id"], v["incident"], v["kind"], v["label"], v["file"]]
                for v in report["inventory"]["views"]
            ],
        ),
        "## Campaigns",
        "",
    ]
    shown = [c for c in report["campaigns"] if len(c["views"]) > 1]
    for campaign in shown:
        kinds = Counter(link["link"] for link in campaign["links"])
        lines += [
            f"### {_cell(campaign['id'])} (family {_cell(campaign['family'])})",
            "",
            "Views: " + ", ".join(_cell(v) for v in campaign["views"]),
            "",
            (
                f"Links: {kinds['direct']} direct, {kinds['direct_and_funnel']} direct and "
                f"funnel, {kinds['funnel']} funnel only, {kinds['transitive']} transitive."
            ),
            "",
            *[
                f"- {_cell(e['a'])} ↔ {_cell(e['b'])}: {e['relation']}, {e['confidence']}. "
                + _cell("; ".join(e["reasons"][:2]))
                for e in campaign["defining_evidence"]
            ],
            "",
        ]
    lines.append(f"{len(report['campaigns']) - len(shown)} single-view campaigns are not listed.")
    lines += ["", "## Families", ""]
    lines += _table(
        ["Family", "Campaigns", "Views"],
        [
            [f["id"], len(f["campaigns"]), ", ".join(f["views"])]
            for f in report["families"]
            if len(f["campaigns"]) > 1
        ],
    )
    lines += ["## Playbooks", ""]
    lines += _table(
        ["Playbook", "Views"], [[x["id"], ", ".join(x["views"])] for x in report["playbooks"]]
    )
    lines += [
        "## Typed pairs",
        "",
        f"All stored relations: {_cell(report['relation_counts'])}.",
        "",
    ]
    lines += _table(
        ["Incident", "Incident", "Strongest relation"],
        [
            [m["incident_a"], m["incident_b"], m["strongest_relation"]]
            for m in report["pair_matrix"]
            if m["strongest_relation"] in {t.value for t in LINKED}
        ],
    )
    lines += ["### Linked view pairs", "", "Full evidence for each pair is in `pairs.md`.", ""]
    lines += _table(
        ["A", "B", "Relation", "Confidence", "Coverage"],
        [
            [
                x["a"],
                x["b"],
                x["relation"],
                x["confidence"],
                f"{x['comparable_groups']} of {x['total_groups']} groups",
            ]
            for x in report["linked_pairs"]
        ],
    )
    lines += ["## Links below folder level", ""]
    for item in report["below_folder"]:
        edges = item["funnel_edges"]
        split = len(item["campaigns_in_folder"])
        if not edges and not item["ad_landing_mismatches"] and split < 2:
            continue
        lines.append(f"### Incident {_cell(item['incident'])}: {split} campaign(s)")
        lines += [f"- Funnel: {_cell(e['source'])} → {_cell(e['target'])}" for e in edges]
        lines += [f"- Ad and landing differ: {_cell(m)}" for m in item["ad_landing_mismatches"]]
        lines.append("")
    lines += ["## Patterns", ""]
    patterns = report["patterns"]
    if patterns["status"] != "available":
        lines += ["Unavailable: pattern discovery has not been run.", ""]
    else:
        lines += [f"Library {_cell(patterns['version'])}.", ""]
        lines += _table(
            ["Pattern", "Name", "Examples", "Retired"],
            [[x["id"], x["name"], x["examples"], x["retired"]] for x in patterns["patterns"]],
        )
    lines += ["## Model-dependent sections", ""]
    lines += _table(
        ["Section", "Status", "Views with results"],
        [
            [k, v["status"], f"{v['views_with_results']} of {v['views']}"]
            for k, v in report["model_sections"].items()
        ],
    )
    lines += ["## Links deliberately ignored", ""]
    lines += _table(
        ["Namespace", "Value", "Reason", "Linked pairs"],
        [[x["ns"], str(x["value"])[:80], x["reason"], x["pairs"]] for x in report["ignored"][:100]],
    )
    if len(report["ignored"]) > 100:
        lines += [f"{len(report['ignored']) - 100} more are in `report.json`.", ""]
    lines += ["## Contradictions and demotions", ""]
    lines += _table(
        ["A", "B", "Reason"],
        [[x["a"], x["b"], x["reason"]] for x in report["contradictions"] + report["demotions"]],
    )
    lines += ["## Open questions and missing evidence", ""]
    lines += [f"- {_cell(q)}" for q in report["open_questions"]] or ["None."]
    return "\n".join(lines) + "\n"


def _safe_csv(value: Any) -> str:
    """Stop hostile page content from running as a spreadsheet formula."""
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_safe_csv(row.get(c, "")) for c in columns])
    return out.getvalue()


def export_files(store: Store, config: FusionConfig = DEFAULT) -> dict[str, str]:
    """Every export file by name, with secrets hashed and whole-page text left out."""
    clean = _cleaner(store)
    report = _walk(build_corpus_report(store, config), clean)
    pair_reports = report.pop("pairs")
    observations, omitted = [], 0
    for view in store.views():
        for o in store.observations(view.view_id):
            if o.namespace in TEXT_NAMESPACES:
                omitted += 1
                continue
            row = o.model_dump(mode="json")
            if is_secret_namespace(o.namespace):
                row["value"] = _walk(row["value"], _hash)
                row["raw"] = _hash(row["raw"]) if isinstance(row["raw"], str) else row["raw"]
            source = row.pop("source") or {}
            row["source_file"], row["source_locator"] = source.get("file"), source.get("locator")
            observations.append(_walk(row, clean))
    report["export"] = {"whole_page_text_observations_omitted": omitted}
    fingerprints = [_walk(f.model_dump(mode="json"), clean) for f in _fingerprints(store)]
    relations = [_walk(r.model_dump(mode="json"), clean) for r in store.relations()]
    evidence = [_walk(e.model_dump(mode="json"), clean) for e in store.evidence()]
    tables = {
        "observations": (
            observations,
            [
                "view_id",
                "signal_id",
                "namespace",
                "value",
                "raw",
                "state",
                "purpose",
                "source_file",
                "source_locator",
                "extractor",
                "version",
                "confidence",
            ],
        ),
        "fingerprints": (fingerprints, ["view_id", "layer_id", "kind", "version", "payload"]),
        "relations": (
            relations,
            ["view_a", "view_b", "type", "confidence", "coverage", "tier_scores", "reasons"],
        ),
        "evidence": (
            evidence,
            ["view_a", "view_b", "layer_id", "comparable", "score", "shared", "differing"],
        ),
    }
    files = {
        "report.md": render_corpus_report(report),
        "report.json": json.dumps(report, ensure_ascii=False, indent=1),
        "pairs.json": json.dumps(pair_reports, ensure_ascii=False, indent=1),
        "pairs.md": "\n\n---\n\n".join(render_pair_report(p) for p in pair_reports) + "\n",
    }
    for name, (rows, columns) in tables.items():
        files[f"{name}.json"] = json.dumps(rows, ensure_ascii=False, indent=1)
        files[f"{name}.csv"] = _csv(rows, columns)
    return files


def _fingerprints(store: Store):
    grouped = defaultdict(list)
    for view in store.views():
        grouped[view.view_id] = store.fingerprints(view_id=view.view_id)
    return [f for rows in grouped.values() for f in rows]


def write_export(store: Store, out_dir: Path, config: FusionConfig = DEFAULT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in export_files(store, config).items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def export_zip(files: dict[str, str]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, content)
    return data.getvalue()
