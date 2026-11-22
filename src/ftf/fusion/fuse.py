"""Typed fusion: per-layer Evidence in, one Relation out (docs/architecture.md section 9).

    group score = max of the layer scores in one correlation group
    tier score  = 1 - prod over groups g of (1 - weight_g * group score_g)
    coverage    = share of correlation groups comparable on both sides

Decisions in order, strongest first: duplicate, same campaign, same family, same playbook,
unrelated, insufficient evidence. A contradiction caps the pair at family. So does identity
that rests on the shared domain alone: it needs a second independent group to be a campaign. Demoted values
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
    comparable_groups: int = 0  # the coverage fraction, as counts
    total_groups: int = 0


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
        comparable_groups=covered,
        total_groups=total,
    )


# Confidence label thresholds, shared by the tiers that use the graded label.
HIGH_SCORE = 0.9
HIGH_AGREEING = 2
# High needs score and agreement as well, so its coverage bar is the lower of the two;
# medium can be reached on coverage alone, so that alternative route asks for more.
HIGH_MIN_COVERAGE = 0.4
MEDIUM_COVERAGE_ALTERNATIVE = 0.5


def _confidence(score: float, coverage: float, agreeing: int, threshold: float) -> str:
    if score >= HIGH_SCORE and agreeing >= HIGH_AGREEING and coverage >= HIGH_MIN_COVERAGE:
        return "high"
    if score >= threshold and (
        agreeing >= HIGH_AGREEING or coverage >= MEDIUM_COVERAGE_ALTERNATIVE
    ):
        return "medium"
    return "low"


def _label_rule(threshold: float) -> dict[str, str]:
    """The graded label, worded once so the report cannot drift from the code above."""
    return {
        "high": (
            f"score >= {HIGH_SCORE} and at least {HIGH_AGREEING} agreeing groups "
            f"and coverage >= {HIGH_MIN_COVERAGE}"
        ),
        "medium": (
            f"score >= {threshold} and (at least {HIGH_AGREEING} agreeing groups "
            f"or coverage >= {MEDIUM_COVERAGE_ALTERNATIVE})"
        ),
        "low": "otherwise",
    }


def _tier_basis(
    path: str, rule: str, tier: Tier, summary: TierSummary, threshold: float, f: Fused, **extra
) -> dict:
    return {
        "path": path,
        "rule": rule,
        "tier": tier.value,
        "score": summary.score,
        "threshold": threshold,
        "agreeing_groups": summary.agreeing,
        "coverage": f.coverage,
        "label_rule": _label_rule(threshold),
        **extra,
    }


def explain_separation(
    coverage: float,
    comparable: int,
    total: int,
    identity_seen: bool,
    artifact_seen: bool,
    config: FusionConfig = DEFAULT,
    weak: list[str] = (),
) -> tuple[RelationType, str, list[str], dict]:
    """The last rule: nothing links the pair, so is it unrelated or is the evidence missing?

    A separation is a claim too. It needs the identity tier seen on both sides: without it
    the evidence that defines a campaign is unavailable, and unavailable is not absent (two
    screenshots whose hashes differ are not thereby unrelated). Shared with the candidate
    fallback (`grouping/candidates.py`) so both paths word and decide it the same way."""
    counts = f"{comparable} of {total} groups comparable"
    basis = {
        "coverage": coverage,
        "comparable_groups": comparable,
        "total_groups": total,
        "min_coverage": config.min_coverage,
        "identity_seen": identity_seen,
        "artifact_seen": artifact_seen,
    }
    if coverage >= config.min_coverage and identity_seen:
        reasons = [f"no tier reached its threshold; coverage {coverage:.2f} ({counts})", *weak]
        if not artifact_seen:
            reasons.append("not comparable: artifact tier; separation is low confidence")
        high = coverage >= config.unrelated_high_coverage and artifact_seen
        basis.update(
            path="unrelated",
            rule="no tier reached its threshold and the identity tier was comparable",
            label_rule={
                "high": (
                    f"coverage >= {config.unrelated_high_coverage} and the artifact tier comparable"
                ),
                "low": "otherwise",
            },
        )
        return RelationType.UNRELATED, "high" if high else "low", reasons, basis
    if coverage >= config.min_coverage:
        basis.update(
            path="insufficient",
            cause="identity_not_comparable",
            rule=(
                "the identity tier was not comparable on both sides, so the pair cannot be "
                "called unrelated"
            ),
            label_rule="no label: insufficient evidence carries none",
        )
        return (
            RelationType.INSUFFICIENT,
            "",
            [
                (
                    "identity tier not comparable on both sides: the pair cannot be called "
                    f"unrelated; coverage {coverage:.2f} ({counts})"
                )
            ],
            basis,
        )
    basis.update(
        path="insufficient",
        cause="low_coverage",
        rule="too few comparable groups",
        label_rule="no label: insufficient evidence carries none",
    )
    return (
        RelationType.INSUFFICIENT,
        "",
        [f"too few comparable layers: coverage {coverage:.2f} ({counts})"],
        basis,
    )


def separation(
    coverage: float,
    comparable: int,
    total: int,
    identity_seen: bool,
    artifact_seen: bool,
    config: FusionConfig = DEFAULT,
    weak: list[str] = (),
) -> tuple[RelationType, str, list[str]]:
    return explain_separation(
        coverage, comparable, total, identity_seen, artifact_seen, config, weak
    )[:3]


def explain(f: Fused, config: FusionConfig = DEFAULT) -> tuple[RelationType, str, list[str], dict]:
    """The ordered decision table, with the basis of the label it produced.

    Returns (type, confidence label, reasons, basis). The basis names the path taken
    through the table and the numbers that path compared, so a report explains the
    decision that was made and not one reconstructed from the scores afterwards."""
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
        basis = {
            "path": "duplicate",
            "rule": "shared duplicate anchor, no contradiction, text absent or above threshold",
            "anchors": [list(a) for a in f.anchors],
            "text_score": f.duplicate_text,
            "text_threshold": config.duplicate_text,
            "coverage": f.coverage,
            "label_rule": {"high": "text compared", "medium": "text not comparable"},
        }
        return RelationType.DUPLICATE, conf, reasons, basis

    # 2. same campaign: identity, or two independent artifact groups; never across a contradiction
    strong_artifact = [g for g, s in t2.groups.items() if s >= config.artifact_group_floor]
    # Identity resting on a solo group (the domain) with nothing else agreeing is not a campaign.
    solo = (
        t1.score >= config.identity
        and bool(t1.agreeing)
        and all(g in config.identity_solo_groups for g in t1.agreeing)
        and not strong_artifact
        and t3.score < config.kit
    )
    if not f.contradictions:
        if t1.score >= config.identity and not solo:
            reasons.append(agree(t1, "identity"))
            return (
                RelationType.SAME_CAMPAIGN,
                _confidence(t1.score, f.coverage, len(t1.agreeing), config.identity),
                reasons,
                _tier_basis(
                    "identity",
                    "identity tier at or above threshold, not resting on a solo group alone",
                    Tier.IDENTITY,
                    t1,
                    config.identity,
                    f,
                    solo_groups=list(config.identity_solo_groups),
                ),
            )
        if t2.score >= config.artifact and len(strong_artifact) >= config.artifact_min_groups:
            reasons.append(agree(t2, "artifact"))
            return (
                RelationType.SAME_CAMPAIGN,
                _confidence(t2.score, f.coverage, len(strong_artifact), config.artifact),
                reasons,
                {
                    **_tier_basis(
                        "artifact",
                        "artifact tier at or above threshold with enough strong independent groups",
                        Tier.ARTIFACT,
                        t2,
                        config.artifact,
                        f,
                    ),
                    "agreeing_groups": sorted(strong_artifact),
                    "group_floor": config.artifact_group_floor,
                    "min_groups": config.artifact_min_groups,
                },
            )

    # 3. same family: kit evidence, or identity contradicted while artifact or kit is strong
    if t3.score >= config.kit:
        reasons.append(agree(t3, "kit"))
        return (
            RelationType.SAME_FAMILY,
            _confidence(t3.score, f.coverage, len(t3.agreeing), config.kit),
            reasons,
            _tier_basis("kit", "kit tier at or above threshold", Tier.KIT, t3, config.kit, f),
        )
    if solo and not f.contradictions:
        reasons.append(agree(t1, "identity"))
        reasons.append(
            f"shared {' and '.join(t1.agreeing)} only: no second independent group agrees, so "
            "this is shared infrastructure, not a campaign link (possible white page or shared host)"
        )
        basis = _tier_basis(
            "identity_solo",
            "identity rests on a solo group alone with no second independent group: shared "
            "infrastructure, family at most",
            Tier.IDENTITY,
            t1,
            config.identity,
            f,
            solo_groups=list(config.identity_solo_groups),
            kit_score=t3.score,
            kit_threshold=config.kit,
            strong_artifact_groups=sorted(strong_artifact),
        )
        basis["label_rule"] = "fixed low"
        return RelationType.SAME_FAMILY, "low", reasons, basis
    if f.contradictions and (t2.score >= config.artifact or t1.score >= config.identity):
        capped = (
            (Tier.ARTIFACT, t2, config.artifact)
            if t2.score >= config.artifact
            else (
                Tier.IDENTITY,
                t1,
                config.identity,
            )
        )
        reasons.append(agree(capped[1], "artifact" if capped[0] is Tier.ARTIFACT else "identity"))
        reasons.append("capped at family by the contradiction")
        basis = _tier_basis(
            "contradiction_cap",
            "identity or artifact support at or above threshold, capped at family by a "
            "contradiction in an exclusive namespace",
            capped[0],
            capped[1],
            capped[2],
            f,
            contradictions=[ns for ns, _, _ in f.contradictions],
        )
        basis["label_rule"] = "fixed medium"
        return RelationType.SAME_FAMILY, "medium", reasons, basis

    # 4. same playbook: pattern and narrative evidence only
    if t4.score >= config.playbook:
        reasons.append(agree(t4, "playbook"))
        return (
            RelationType.SAME_PLAYBOOK,
            _confidence(t4.score, f.coverage, len(t4.agreeing), config.playbook),
            reasons,
            _tier_basis(
                "playbook",
                "playbook tier at or above threshold; never a campaign or family link",
                Tier.PLAYBOOK,
                t4,
                config.playbook,
                f,
            ),
        )

    # 5. nothing above: unrelated if we saw enough, else insufficient
    weak = [
        "below threshold, not used: " + agree(tier, label)
        for tier, label in ((t1, "identity"), (t2, "artifact"), (t3, "kit"), (t4, "playbook"))
        if tier.score >= 0.1
    ]
    rtype, confidence, why, basis = explain_separation(
        f.coverage,
        f.comparable_groups,
        f.total_groups,
        identity_seen=bool(t1.groups),
        artifact_seen=bool(t2.groups),
        config=config,
        weak=weak,
    )
    basis["below_threshold"] = {t.value: s.score for t, s in f.tiers.items() if s.score >= 0.1}
    return rtype, confidence, reasons + why, basis


def decide(f: Fused, config: FusionConfig = DEFAULT) -> tuple[RelationType, str, list[str]]:
    """The ordered decision table. Returns (type, confidence label, reasons)."""
    return explain(f, config)[:3]


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
