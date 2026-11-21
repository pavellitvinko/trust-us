"""The persona library (VV-01…VV-05): four synthetic people, stored as versioned data files.

Each file under `personas/` is one persona. Its `version` changes whenever the file
changes, and every run records the version it used, so runs stay comparable. No persona is
based on a real person (VV-03).
"""

from __future__ import annotations

import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PERSONA_DIR = Path(__file__).with_name("personas")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identity(_Model):
    first_name: str
    surname: str
    age: int
    sex: Literal["female", "male"]
    town: str
    voivodeship: str


class Situation(_Model):
    occupation: str
    monthly_income_pln: str
    savings_band: str
    family: str
    health_worries: str


class Viewport(_Model):
    width: int
    height: int


class Digital(_Model):
    device: Literal["desktop", "phone"]
    browser: str
    phone_model: str | None = None
    viewport: Viewport
    platform: Literal["facebook", "instagram", "google", "tiktok"]
    reading: str
    reading_wpm: int
    typing_cps: float


class Disposition(_Model):
    trust: str
    urgency: str


class Locale(_Model):
    locale: str = "pl-PL"
    timezone: str = "Europe/Warsaw"


class AnswerRule(_Model):
    """How the persona answers one kind of question, the same way all through a run (VV-14)."""

    topic: str
    patterns: list[str]  # regular expressions matched against the question text
    prefer: list[str]  # option texts to look for, best first


class Persona(_Model):
    id: str
    version: str
    summary: str
    matches: str
    identity: Identity
    situation: Situation
    interests: list[str]
    fears: list[str]
    goal: str
    digital: Digital
    disposition: Disposition
    locale: Locale = Field(default_factory=Locale)
    answers: list[AnswerRule]
    suggest_keywords: list[str]

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def brief(self) -> str:
        """The persona as the decider and the perception model see it."""
        i, s, d = self.identity, self.situation, self.digital
        return (
            f"You are {i.first_name} {i.surname}, {i.age}, {i.sex}, from {i.town} "
            f"({i.voivodeship}). Occupation: {s.occupation}; income about "
            f"{s.monthly_income_pln} zł a month; savings {s.savings_band}; family: {s.family}; "
            f"health: {s.health_worries}. Interests: {', '.join(self.interests)}. "
            f"Fears: {', '.join(self.fears)}. You came from {d.platform} on a {d.device} and "
            f"you read {d.reading}. Disposition: {self.disposition.trust}; "
            f"{self.disposition.urgency}. Your goal: {self.goal}."
        )


@cache
def load_personas() -> dict[str, Persona]:
    out: dict[str, Persona] = {}
    for path in sorted(PERSONA_DIR.glob("*.json")):
        persona = Persona.model_validate_json(path.read_text(encoding="utf-8"))
        if persona.id != path.stem:
            raise ValueError(f"{path.name}: id {persona.id!r} does not match the file name")
        out[persona.id] = persona
    return out


def get_persona(persona_id: str) -> Persona:
    personas = load_personas()
    try:
        return personas[persona_id]
    except KeyError:
        raise KeyError(f"unknown persona {persona_id!r}; known: {sorted(personas)}") from None


def fold(text: str) -> str:
    """Lower case without Polish diacritics, for matching."""
    text = text.casefold().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def suggest_persona(text: str) -> tuple[str, str]:
    """VV-05: the persona whose keywords the ad text matches best, and why."""
    folded = fold(text)
    best, best_hits = "retiree-income", []
    for persona in load_personas().values():
        hits = [k for k in persona.suggest_keywords if fold(k) in folded]
        if len(hits) > len(best_hits):
            best, best_hits = persona.id, hits
    if not best_hits:
        return best, "no persona keywords in the text; the default investment persona is used"
    return best, "the text mentions " + ", ".join(f"'{h}'" for h in best_hits[:6])


def answer_for(persona: Persona, question: str, options: list[str]) -> tuple[int | None, str]:
    """Pick the option the persona would give, with the topic that decided it.

    Returns (index, topic) or (None, "") when no rule covers the question; the caller then
    improvises and records that it did (VV-14).
    """
    q = fold(question)
    folded = [fold(o) for o in options]
    for rule in persona.answers:
        if not any(re.search(fold(p), q) for p in rule.patterns):
            continue
        for wanted in rule.prefer:
            w = fold(wanted)
            for index, option in enumerate(folded):
                if w in option:
                    return index, rule.topic
    return None, ""
