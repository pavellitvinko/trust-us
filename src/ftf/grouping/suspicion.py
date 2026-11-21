"""Suspicion summary: reasons why one view is suspicious, grouped by risk kind.

Each reason links to its observation and source. The level is the count of independent
kinds that produced reasons.

Design: docs/architecture.md §11, signal-catalogue.md groups C, G, J, H.
"""

from __future__ import annotations

from ftf.contracts import Observation, SuspicionReason, SuspicionSummary, Source, Purpose


# Mapping of observation namespaces to suspicion kinds and readable summaries.
# Key: (namespace_prefix, purpose_requirement)
# Value: (kind, signal_id, reason_template)
_REASON_KINDS = {
    # Domain risk (C1, C4, C6, C9, C13)
    "domain.age": ("domain", "C1", "domain registered {value} days ago"),
    "domain.tld_risk": ("domain", "C4", "risky TLD: {value}"),
    "domain.lookalike": ("domain", "C6", "domain looks like {value}"),
    "domain.prior_life": ("domain", "C9", "domain re-registered; prior life {value}"),
    "domain.dead": ("domain", "C13", "domain currently dead or sinkholed"),
    # Impersonation risk (G2, G3)
    "entity.impersonation": ("impersonation", "G2", "impersonates {value}"),
    "entity.institution": ("impersonation", "G3", "invokes authority: {value}"),
    "entity.mismatch": ("impersonation", "G2", "claimed publisher {claimed} ≠ host {host}"),
    # Absent mandatory elements (K, from expectations library)
    "element.absent": ("absent_elements", "K", "expected element missing: {value}"),
    # Evasion risk (E13, J1, J5, J6, J7)
    "evasion.visual_filter": ("evasion", "E13", "visual-hash evasion filter detected"),
    "evasion.cloaking": ("evasion", "J1", "content differs across captures ({value})"),
    "evasion.gating": ("evasion", "J5", "page requires {value} to show offer"),
    "evasion.text_obfuscation": ("evasion", "J6", "text obfuscated: {value}"),
    "evasion.stale_content": ("evasion", "J7", "decoy content dated {value}"),
    # Persuasion patterns (H1–H19)
    "pattern.celebrity": ("persuasion", "H1", "celebrity presented as endorser"),
    "pattern.fake_news": ("persuasion", "H2", "fake news-article framing"),
    "pattern.suppression": ("persuasion", "H3", "suppression story: {value}"),
    "pattern.violence": ("persuasion", "H4", "violence/arrest as hook"),
    "pattern.distrust": ("persuasion", "H5", "distrust narrative: {value}"),
    "pattern.proof_image": ("persuasion", "H6", "profit proof image shown"),
    "pattern.regulator": ("persuasion", "H7", "regulator/state backing claimed"),
    "pattern.authority": ("persuasion", "H8", "authority figure in uniform"),
    "pattern.scarcity": ("persuasion", "H9", "scarcity pressure: {value}"),
    "pattern.countdown": ("persuasion", "H10", "countdown timer detected"),
    "pattern.discount": ("persuasion", "H11", "deep discount with crossed-out price"),
    "pattern.fake_comments": ("persuasion", "H12", "embedded fake social comments"),
    "pattern.gamification": ("persuasion", "H13", "gamified discount (wheel/boxes)"),
    "pattern.chatbot": ("persuasion", "H14", "chatbot/quiz qualification gate"),
    "pattern.callback": ("persuasion", "H15", "callback promise: {value}"),
    "pattern.health_claim": ("persuasion", "H16", "miracle health claim"),
    "pattern.marketplace": ("persuasion", "H17", "app-store/marketplace imitation"),
    "pattern.video_sales": ("persuasion", "H18", "video sales letter with sound prompt"),
    "pattern.low_entry": ("persuasion", "H19", "low entry threshold: {value}"),
}


def reason_summary_for_view(
    view_id: str, observations: list[Observation]
) -> SuspicionSummary:
    """Generate a suspicion summary for one view.

    Groups RISK observations by kind and builds readable reasons with links to sources.

    Args:
        view_id: the view identifier
        observations: all observations for this view

    Returns:
        SuspicionSummary with reasons grouped by kind and level = count of kinds
    """
    reasons_by_kind: dict[str, list[SuspicionReason]] = {}

    for obs in observations:
        # Only consider RISK-tagged observations
        if obs.purpose not in (Purpose.RISK, Purpose.BOTH):
            continue
        if obs.state.value == "not_observable":
            continue

        # Find the mapping for this namespace
        kind, signal_id, template = None, None, None
        for ns_prefix, (k, sig, tmpl) in _REASON_KINDS.items():
            if obs.namespace.startswith(ns_prefix):
                kind, signal_id, template = k, sig, tmpl
                break

        if kind is None:
            continue  # Unmapped namespace; skip

        # Build the reason summary from the template
        try:
            summary = template.format(value=obs.value or "")
        except (KeyError, ValueError):
            summary = template  # Fallback if formatting fails

        reason = SuspicionReason(
            kind=kind,
            signal_id=signal_id,
            namespace=obs.namespace,
            summary=summary,
            value=obs.value,
            quote=None,  # Will be populated if extracted by verification layer
            observation_id=obs.signal_id,
            source=obs.source,
        )

        if kind not in reasons_by_kind:
            reasons_by_kind[kind] = []
        reasons_by_kind[kind].append(reason)

    # Level = number of independent kinds with reasons
    level = len(reasons_by_kind)

    return SuspicionSummary(
        view_id=view_id, level=level, reasons_by_kind=reasons_by_kind
    )
