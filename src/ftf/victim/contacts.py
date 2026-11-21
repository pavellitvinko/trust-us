"""Contact details the victim gives (VV-30…VV-32), and how it fills a lead form.

- E-mail: one address per run on the team's catch-all domain, `FTF_VICTIM_EMAIL_DOMAIN`.
  When the setting is empty, a form that needs an e-mail is filled but not sent, and the run
  records why.
- Phone: the safe unassignable range is not confirmed yet (VV-31, open question 1), so
  optional phone fields stay empty and a form that requires one is not sent: "phone required".
- Every value comes from the persona or the run, never from the model, and every value is
  read back from the page before sending (VV-21: no data that is not the persona's).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .persona import Persona, fold

# What a field asks for, judged from its name, type, label, placeholder and autocomplete.
_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"e-?mail|poczt", re.IGNORECASE)),
    (
        "phone",
        re.compile(r"phone|tel\b|telefon|komórk|komork|mobile|numer kontaktowy", re.IGNORECASE),
    ),
    ("first_name", re.compile(r"first.?name|fname|given|\bimi[eę]\b|^imi", re.IGNORECASE)),
    ("last_name", re.compile(r"last.?name|lname|surname|family|nazwisko", re.IGNORECASE)),
    ("full_name", re.compile(r"^name$|full.?name|imi[eę] i nazwisko|\bname\b", re.IGNORECASE)),
    ("age", re.compile(r"\bage\b|wiek|\blat\b", re.IGNORECASE)),
    ("town", re.compile(r"city|town|miasto|miejscowo", re.IGNORECASE)),
    ("country", re.compile(r"country|kraj", re.IGNORECASE)),
    ("consent", re.compile(r"consent|zgod|agree|akceptuj|regulamin|rodo|terms", re.IGNORECASE)),
)
# Fields that are never filled, whatever the form: the rules stop the run at these.
FORBIDDEN_FIELD = re.compile(
    r"card|karty|karta|\bcc-|cvv|cvc|expir|ważno|iban|swift|bic\b|account.?number|numer konta|"
    r"nr konta|rachun|blik|wallet|portfel|seed|mnemonic|private.?key|pesel|dowod|dowód|passport|"
    r"paszport|password|hasło|haslo|\bpin\b",
    re.IGNORECASE,
)


def field_text(f: dict[str, Any]) -> str:
    return " ".join(
        str(f.get(k) or "") for k in ("name", "id", "label", "autocomplete", "placeholder")
    )


def classify_field(f: dict[str, Any]) -> str:
    """first_name | last_name | full_name | email | phone | age | town | country | consent |
    forbidden | other."""
    ftype = (f.get("type") or "").lower()
    text = field_text(f)
    if ftype in {"password", "file"} or FORBIDDEN_FIELD.search(text):
        return "forbidden"
    if ftype == "email":
        return "email"
    if ftype == "tel":
        return "phone"
    autocomplete = (f.get("autocomplete") or "").lower()
    if autocomplete.startswith("cc-"):
        return "forbidden"
    for kind, pattern in _KINDS:
        if pattern.search(text):
            if kind == "consent" and ftype not in {"checkbox", "radio"}:
                continue
            return kind
    if ftype == "checkbox":
        return "consent"
    return "other"


def run_email(persona: Persona, run_id: str, domain: str) -> str | None:
    """`<persona>.<run-id>@<team-domain>`, or None when no team domain is set (VV-30)."""
    domain = domain.strip().lstrip("@").lower()
    if not domain:
        return None
    return f"{persona.id}.{run_id}@{domain}".lower()


@dataclass
class FillPlan:
    """What to type into one form, and whether it may be sent."""

    values: dict[str, str] = field(default_factory=dict)  # xpath -> value
    checks: list[str] = field(default_factory=list)  # xpaths of consent boxes to tick
    selects: dict[str, str] = field(default_factory=dict)  # xpath -> option text
    fields: list[dict[str, Any]] = field(default_factory=list)  # what was asked, for the log
    submit: bool = True
    stop_reason: str | None = None  # why the form is not sent (the first reason found)
    stop_detail: str = ""
    refusals: list[dict[str, str]] = field(default_factory=list)  # every reason found

    def refuse(self, reason: str, detail: str) -> None:
        self.refusals.append({"reason": reason, "detail": detail})
        if self.stop_reason is None:
            self.submit, self.stop_reason, self.stop_detail = False, reason, detail


def plan_form(form: dict[str, Any], persona: Persona, email: str | None) -> FillPlan:
    """Decide every value for a lead form. Pure: the agent types exactly this."""
    plan = FillPlan()
    ident = persona.identity
    for f in form.get("fields", []):
        if not f.get("visible"):
            continue
        kind = classify_field(f)
        required = bool(f.get("required"))
        plan.fields.append(
            {
                "kind": kind,
                "name": f.get("name", ""),
                "label": f.get("label", ""),
                "type": f.get("type", ""),
                "required": required,
                "pattern": f.get("pattern", ""),
            }
        )
        xpath = f.get("xpath", "")
        if kind == "forbidden":
            plan.refuse("forbidden_field", f"the form asks for {field_text(f).strip()!r}")
        elif kind == "email":
            if email is None:
                plan.refuse(
                    "email_domain_unset",
                    "the form needs an e-mail and FTF_VICTIM_EMAIL_DOMAIN is not set",
                )
            else:
                plan.values[xpath] = email
        elif kind == "phone":
            # VV-31: no safe number yet. Optional phone fields stay empty.
            if required:
                plan.refuse("phone_required", "the form requires a phone number (VV-31 open)")
        elif kind == "first_name":
            plan.values[xpath] = ident.first_name
        elif kind == "last_name":
            plan.values[xpath] = ident.surname
        elif kind == "full_name":
            plan.values[xpath] = f"{ident.first_name} {ident.surname}"
        elif kind == "age":
            plan.values[xpath] = str(ident.age)
        elif kind == "town":
            plan.values[xpath] = ident.town
        elif kind == "country":
            if f.get("tag") == "select":
                match = next((o for o in f.get("options", []) if "pol" in fold(o)), None)
                if match:
                    plan.selects[xpath] = match
                elif required:
                    plan.refuse("unknown_field", "a required country list has no Poland")
            else:
                plan.values[xpath] = "Polska"
        elif kind == "consent":
            if required:
                plan.checks.append(xpath)
        elif required:
            plan.refuse(
                "unknown_field",
                f"a required field the persona cannot answer: {field_text(f).strip()!r}",
            )
    return plan


def allowed_values(persona: Persona, email: str | None) -> set[str]:
    """Every value the victim may ever type into a form (read back before sending)."""
    i = persona.identity
    values = {
        i.first_name,
        i.surname,
        f"{i.first_name} {i.surname}",
        str(i.age),
        i.town,
        "Polska",
        "",
    }
    if email:
        values.add(email)
    return values


_DIGITS = re.compile(r"\d[\d\s-]{7,}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def safe_free_text(text: str, email: str | None) -> str | None:
    """A chat answer the decider wrote, or None if it carries data that is not the persona's.

    Long digit runs (phone, card, account numbers) and any e-mail other than the run's own
    are refused.
    """
    if _DIGITS.search(text):
        return None
    for found in _EMAIL.findall(text):
        if email is None or found.lower() != email.lower():
            return None
    return text.strip()[:300] or None
