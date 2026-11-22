"""Campaign map data: views grouped by family and campaign, with links per evidence tier."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import RelationType, Tier
from .fusion import DEFAULT, FusionConfig
from .grouping import build_groups
from .registry import layers, load_plugins
from .store import Store

TOGGLES = {
    "identity": Tier.IDENTITY,
    "artifact": Tier.ARTIFACT,
    "kit": Tier.KIT,
    "playbook": Tier.PLAYBOOK,
}
_NOT_LINKS = {RelationType.UNRELATED, RelationType.INSUFFICIENT}


def _thresholds(config: FusionConfig) -> dict[Tier, float]:
    return {
        Tier.IDENTITY: config.identity,
        Tier.ARTIFACT: config.artifact,
        Tier.KIT: config.kit,
        Tier.PLAYBOOK: config.playbook,
    }


def _shared_values(store: Store) -> dict[tuple[str, str, Tier], list[str]]:
    load_plugins()
    tiers = {layer.name: layer.tier for layer in layers.all()}
    out: dict[tuple[str, str, Tier], list[str]] = defaultdict(list)
    for e in store.evidence():
        tier = tiers.get(e.layer_id)
        if tier is None or not e.comparable:
            continue
        for item in e.shared:
            if isinstance(item, dict) and "value" in item:
                out[(e.view_a, e.view_b, tier)].append(f"{item.get('ns')}={item['value']}")
    return out


def map_data(
    store: Store, enabled: list[str] | None = None, config: FusionConfig = DEFAULT
) -> dict[str, Any]:
    """Views, groups and the links whose tier passed its threshold. Read-only."""
    wanted = list(TOGGLES) if enabled is None else enabled
    groups = build_groups(store, config, persist_funnel=False)
    thresholds = _thresholds(config)
    shared = _shared_values(store)
    edges = []
    for r in store.relations():
        if r.type in _NOT_LINKS:
            continue
        tiers = [
            name
            for name in wanted
            if r.tier_scores.get(TOGGLES[name].value, 0.0) >= thresholds[TOGGLES[name]]
        ]
        if tiers:
            values = [
                value
                for name in tiers
                for value in shared.get((r.view_a, r.view_b, TOGGLES[name]), [])
            ]
            edges.append(
                {
                    "a": r.view_a,
                    "b": r.view_b,
                    "relation": r.type.value,
                    "confidence": r.confidence,
                    "tiers": tiers,
                    "shared": list(dict.fromkeys(values)),
                }
            )
    views = [
        {
            "view_id": v.view_id,
            "incident": v.incident_id,
            "label": v.label,
            "campaign": groups.campaign_of.get(v.view_id),
            "family": groups.family_of_view(v.view_id),
        }
        for v in store.views()
    ]
    clusters = [
        {
            "family": family,
            "campaigns": {c: groups.campaigns[c] for c in campaigns},
        }
        for family, campaigns in sorted(groups.families.items())
        if sum(len(groups.campaigns[c]) for c in campaigns) > 1
    ]
    return {"views": views, "edges": edges, "clusters": clusters}


def _quote(text: Any) -> str:
    clean = " ".join(str(text).split())
    return '"' + clean.replace("\\", "\\\\").replace('"', '\\"') + '"'


_COLOURS = {"identity": "#b3261e", "artifact": "#7a4fbf", "kit": "#1f6fb2", "playbook": "#2e7d4f"}


def to_dot(data: dict[str, Any]) -> str:
    """Graphviz source. Every label comes from evidence, so all of them are quoted."""
    by_campaign: dict[Any, list[dict]] = defaultdict(list)
    family_of: dict[Any, Any] = {}
    for view in data["views"]:
        by_campaign[view["campaign"]].append(view)
        family_of[view["campaign"]] = view["family"]
    families: dict[Any, list[Any]] = defaultdict(list)
    for campaign in by_campaign:
        families[family_of[campaign]].append(campaign)
    lines = ["graph map {", "rankdir=LR;", "node [shape=box, fontsize=10];"]
    for f, (family, campaigns) in enumerate(sorted(families.items(), key=lambda x: str(x[0]))):
        lines += [f"subgraph cluster_f{f} {{", f"label={_quote(f'family {family}')};"]
        for c, campaign in enumerate(sorted(campaigns, key=str)):
            lines += [f"subgraph cluster_f{f}_c{c} {{", f"label={_quote(f'campaign {campaign}')};"]
            for view in by_campaign[campaign]:
                label = f"{view['view_id']}\n{view['label'][:32]}".replace("\n", " · ")
                lines.append(f"{_quote(view['view_id'])} [label={_quote(label)}];")
            lines.append("}")
        lines.append("}")
    for edge in data["edges"]:
        colour = _COLOURS[edge["tiers"][0]]
        label = f"{edge['relation']} ({'+'.join(edge['tiers'])})"
        lines.append(
            f"{_quote(edge['a'])} -- {_quote(edge['b'])} "
            f"[color={_quote(colour)}, label={_quote(label)}, fontsize=8];"
        )
    lines.append("}")
    return "\n".join(lines)
