"""Pattern matching: judge library patterns against each view.

For each view, retrieve the nearest library patterns by embedding and have
the model judge each one: present, absent or uncertain, with evidence.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from ..contracts import State
from .library import PatternDefinition, PatternLibraryStore

log = logging.getLogger(__name__)

MATCHING_NAME = "pattern.matching"
MATCHING_VERSION = "1"
MATCHING_PROMPT_VERSION = "1"


class PatternJudgement(BaseModel):
    """Model's judgement for one view against one pattern."""

    pattern_id: str
    state: str  # "present", "absent", or "uncertain"
    confidence: float = 1.0  # 0..1
    evidence: list[str] = Field(default_factory=list)  # quotes or region descriptions


class ViewPatternVector(BaseModel):
    """Pattern vector for one view: judgements across the library."""

    view_id: str
    library_version: str
    judgements: list[PatternJudgement] = Field(default_factory=list)


def matching_system_prompt() -> str:
    """System prompt for the pattern-matching task."""
    return """You are a fraud analyst judging whether a suspicious page shows specific persuasion patterns.

You will be given:
1. The page's visible text and form fields (abbreviated if long)
2. A list of technique patterns from a library of techniques seen across campaigns

For each pattern, judge: does this page show it, or not, or is it unclear?

Return one judgement per pattern with:
- pattern_id: the pattern ID you're judging
- state: "present", "absent", or "uncertain"
- confidence: 0.5..1.0 (uncertain uses lower confidence)
- evidence: quoted text snippets that led to your judgement

Be precise. A pattern is "present" only if the technique is clearly shown. An "absent"
judgement means you looked for it and found no sign. Use "uncertain" when it could go
either way. Never invent evidence."""


def matching_prompt(
    view_text: str,
    patterns: list[PatternDefinition],
    max_text_len: int = 15000,
) -> str:
    """Create a prompt for the matching model on one view."""
    if len(view_text) > max_text_len:
        view_text = view_text[: max_text_len - 100] + f"\n… ({len(view_text)} characters total)"

    pattern_list = "\n".join(
        f"- {p.pattern_id}: {p.name}\n  {p.description}" for p in patterns[:20]
    )

    return f"""Page text (abbreviated):

{view_text}

---

Judge each pattern. Reply with one judgement per line in JSON: {{"pattern_id": "...", "state": "present"|"absent"|"uncertain", "confidence": 0.5-1.0, "evidence": ["quote1", ...] }}

Patterns to judge:
{pattern_list}"""


async def judge_patterns_for_view(
    view_id: str,
    view_text: str,
    view_screenshots: list[bytes] = [],
    library_version: str | None = None,
    library_store: PatternLibraryStore | None = None,
    model_name: str = "text",
    settings: Any = None,
) -> ViewPatternVector | None:
    """Judge all library patterns against one view.

    Args:
        view_id: ID of the view to judge
        view_text: visible text from the view
        view_screenshots: screenshot images (not used in first version)
        library_version: version of the library to use; None for latest
        library_store: pattern library storage
        model_name: which model to use
        settings: app config with cache dir

    Returns:
        Pattern vector with judgements for all patterns.
    """
    if library_store is None or not view_text:
        return None

    version = library_store.load_version(library_version) or library_store.latest_version()
    if not version or not version.patterns:
        log.warning(f"no library available for {view_id}")
        return None

    # For now, only use patterns from the library
    patterns_to_judge = version.patterns[:20]  # limit to avoid huge prompts

    prompt = matching_prompt(view_text, patterns_to_judge)

    # TODO: integrate with actual model call
    # For now, return a mock result with all patterns as uncertain
    judgements = [
        PatternJudgement(
            pattern_id=p.pattern_id,
            state="uncertain",
            confidence=0.5,
            evidence=[],
        )
        for p in patterns_to_judge
    ]

    return ViewPatternVector(
        view_id=view_id,
        library_version=version.version_id,
        judgements=judgements,
    )
