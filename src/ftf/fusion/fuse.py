"""Typed fusion: per-layer Evidence in, one Relation out (docs/architecture.md section 9).

    group score = max of the layer scores in one correlation group
    tier score  = 1 - prod over groups g of (1 - weight_g * group score_g)
    coverage    = share of correlation groups comparable on both sides

Decisions in order, strongest first: duplicate, same campaign, same family, same playbook,
unrelated, insufficient evidence. A contradiction caps the pair at family. Demoted values
are moved from identity to kit before any score is computed. Playbook evidence never
reaches the first three rules. Nothing here is asserted by a model.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import prod

from ..compare.weights import weights
from ..contracts import Evidence, Relation, RelationType, Tier
from ..registry import layers, load_plugins
from .config import DEFAULT, FusionConfig
from .demotion import Key

DEMOTED_GROUP_PREFIX = "demoted:"

# Correlation groups that exist somewhere in the corpus. Coverage is measured against these:
# a layer no extractor produces for any view cannot lower the coverage of every pair.
# None means "all registered groups" (the fixtures build every layer).
_active_groups: set[str] | None = None


def set_active_groups(groups: set[str] | None) -> None:
    global _active_groups
    _active_groups = set(groups) if groups is not None else None


def active_groups() -> set[str] | None:
    return _active_groups


@dataclass
class TierSummary:
    score: float = 0.0
    coverage: float = 0.0
    groups: dict[str, float] = field(default_factory=dict)  # comparable groups -> score
    total_groups: int = 0

    @property
    def agreeing(self) -> list[str]:
        return sorted(g for g, s in self.groups.items() if s >= 0.5)


@dataclass
class Fused:
    tiers: dict[Tier, TierSummary]
    contradictions: list[tuple[str, list[str], list[str]]]
    demoted_shared: list[Key]
    anchors: list[Key]
    duplicate_text: float | None  # None when the text layer is not comparable
    coverage: float


def _rescore(items: list[dict]) -> float:
    return 1.0 - prod(1.0 - float(i.get("weight", 0.0)) for i in items) if items else 0.0


def _short(value: str, n: int = 12) -> str:
    return value if len(value) <= n else value[: n - 1] + "…"


def summarise(
    evidence: list[Evidence], config: FusionConfig = DEFAULT, demoted: set[Key] | None = None
) -> Fused:
    load_plugins()
    demoted = weights.demoted if demoted is None else demoted
    meta = {layer.name: layer for layer in layers.all()}

    # all groups per tier, from the registry: a group nobody built still lowers coverage
    groups_in_tier: dict[Tier, set[str]] = defaultdict(set)
    for layer in meta.values():
        if _active_groups is None or layer.group in _active_groups:
            groups_in_tier[layer.tier].add(layer.group)

    comparable: dict[Tier, dict[str, float]] = defaultdict(dict)
    contradictions, demoted_shared, anchors = [], [], []
    duplicate_text = None

    for e in evidence:
        layer = meta.get(e.layer_id)
        if layer is None or not e.comparable:
            continue
        tier, group, score = layer.tier, layer.group, e.score
        shared = list(e.shared)

        if e.layer_id == config.duplicate_layer:
            duplicate_text = score

        if tier is Tier.IDENTITY:
            for d in e.differing:
                if d["ns"] in config.exclusive_namespaces and d.get("a") and d.get("b"):
                    contradictions.append((d["ns"], list(d["a"]), list(d["b"])))
            anchors += [
                (s["ns"], s["value"]) for s in shared if s["ns"] in config.duplicate_anchors
            ]
            kept = [s for s in shared if (s["ns"], str(s["value"])) not in demoted]
            moved = [s for s in shared if (s["ns"], str(s["value"])) in demoted]
            if moved:
                demoted_shared += [(s["ns"], str(s["value"])) for s in moved]
                score = _rescore(kept) if "weight" in shared[0] else (score if kept else 0.0)
                kit_group = DEMOTED_GROUP_PREFIX + group
                comparable[Tier.KIT][kit_group] = max(
                    comparable[Tier.KIT].get(kit_group, 0.0), _rescore(moved)
                )
                groups_in_tier[Tier.KIT].add(kit_group)
        elif tier is Tier.ARTIFACT:
            anchors += [
                (s["ns"], s["value"]) for s in shared if s["ns"] in config.duplicate_anchors
            ]
            if e.layer_id in config.duplicate_anchor_layers:
                anchors += [
                    (e.layer_id, s["value"])
                    for s in shared
                    if s.get("distance", 99) <= config.duplicate_anchor_max_distance
                ]

        comparable[tier][group] = max(comparable[tier].get(group, 0.0), score)

    tiers: dict[Tier, TierSummary] = {}
    for tier in (Tier.IDENTITY, Tier.ARTIFACT, Tier.KIT, Tier.PLAYBOOK):
        all_groups = groups_in_tier.get(tier, set())
        seen = comparable.get(tier, {})
        miss = 1.0
        for group, s in seen.items():
            w = config.group_weight(group.removeprefix(DEMOTED_GROUP_PREFIX), tier)
            miss *= 1.0 - w * s
        tiers[tier] = TierSummary(
            score=round(1.0 - miss, 6),
            coverage=len(seen) / len(all_groups) if all_groups else 0.0,
            groups=dict(seen),
            total_groups=len(all_groups),
        )
    total = sum(len(g) for g in groups_in_tier.values())
    covered = sum(len(c) for c in comparable.values())
    return Fused(
        tiers=tiers,
        contradictions=contradictions,
        demoted_shared=sorted(set(demoted_shared)),
        anchors=sorted(set(anchors)),
        duplicate_text=duplicate_text,
        coverage=covered / total if total else 0.0,
    )


def _confidence(score: float, coverage: float, agreeing: int, threshold: float) -> str:
    if score >= 0.9 and agreeing >= 2 and coverage >= 0.4:
        return "high"
    if score >= threshold and (agreeing >= 2 or coverage >= 0.5):
        return "medium"
    return "low"


def decide(f: Fused, config: FusionConfig = DEFAULT) -> tuple[RelationType, str, list[str]]:
    """The ordered decision table. Returns (type, confidence label, reasons)."""
    t1, t2, t3, t4 = (f.tiers[t] for t in (Tier.IDENTITY, Tier.ARTIFACT, Tier.KIT, Tier.PLAYBOOK))
    reasons: list[str] = []
    for ns, a, b in f.contradictions:
        reasons.append(
            f"contradiction: {ns} {', '.join(_short(x) for x in a)} vs {', '.join(_short(x) for x in b)}"
        )
    for ns, value in f.demoted_shared:
        reasons.append(f"demoted to kit: {ns}={_short(value, 24)}")

    def agree(summary: TierSummary, label: str) -> str:
        groups = ", ".join(f"{g}={s:.2f}" for g, s in sorted(summary.groups.items()) if s > 0)
        return f"{label} {summary.score:.2f} [{groups}]"

    # 1. duplicate: same landing or same primary file, and the text agrees or is unseen
    text_ok = f.duplicate_text is None or f.duplicate_text >= config.duplicate_text
    if f.anchors and text_ok and not f.contradictions:
        reasons.append(
            "duplicate anchor: " + ", ".join(f"{ns}={_short(v, 40)}" for ns, v in f.anchors)
        )
        if f.duplicate_text is not None:
            reasons.append(f"text near-duplicate {f.duplicate_text:.2f}")
        conf = "high" if f.duplicate_text is not None else "medium"
        return RelationType.DUPLICATE, conf, reasons

    # 2. same campaign: identity, or two independent artifact groups; never across a contradiction
    strong_artifact = [g for g, s in t2.groups.items() if s >= config.artifact_group_floor]
    if not f.contradictions:
        if t1.score >= config.identity:
            reasons.append(agree(t1, "identity"))
            return (
                RelationType.SAME_CAMPAIGN,
                _confidence(t1.score, f.coverage, len(t1.agreeing), config.identity),
                reasons,
            )
        if t2.score >= config.artifact and len(strong_artifact) >= config.artifact_min_groups:
            reasons.append(agree(t2, "artifact"))
            return (
                RelationType.SAME_CAMPAIGN,
                _confidence(t2.score, f.coverage, len(strong_artifact), config.artifact),
                reasons,
            )

    # 3. same family: kit evidence, or identity contradicted while artifact or kit is strong
    if t3.score >= config.kit:
        reasons.append(agree(t3, "kit"))
        return (
            RelationType.SAME_FAMILY,
            _confidence(t3.score, f.coverage, len(t3.agreeing), config.kit),
            reasons,
        )
    if f.contradictions and (t2.score >= config.artifact or t1.score >= config.identity):
        reasons.append(
            agree(t2, "artifact") if t2.score >= config.artifact else agree(t1, "identity")
        )
        reasons.append("capped at family by the contradiction")
        return RelationType.SAME_FAMILY, "medium", reasons

    # 4. same playbook: pattern and narrative evidence only
    if t4.score >= config.playbook:
        reasons.append(agree(t4, "playbook"))
        return (
            RelationType.SAME_PLAYBOOK,
            _confidence(t4.score, f.coverage, len(t4.agreeing), config.playbook),
            reasons,
        )

    # 5. nothing above: unrelated if we saw enough, else insufficient
    if f.coverage >= config.min_coverage:
        reasons.append(f"no tier reached its threshold; coverage {f.coverage:.2f}")
        return RelationType.UNRELATED, "high" if f.coverage >= 0.6 else "low", reasons
    reasons.append(f"too few comparable layers: coverage {f.coverage:.2f}")
    return RelationType.INSUFFICIENT, "", reasons


def fuse(
    view_a: str,
    view_b: str,
    evidence: list[Evidence],
    config: FusionConfig = DEFAULT,
    demoted: set[Key] | None = None,
) -> Relation:
    f = summarise(evidence, config, demoted)
    rtype, confidence, reasons = decide(f, config)
    return Relation(
        view_a=view_a,
        view_b=view_b,
        type=rtype,
        confidence=confidence,
        coverage=round(f.coverage, 4),
        tier_scores={t.value: s.score for t, s in f.tiers.items()},
        reasons=reasons,
    )
