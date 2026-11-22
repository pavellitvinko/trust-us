"""Contact details the victim gives (VV-30…VV-32), and how it fills a lead form.

Every e-mail address and phone number comes from one versioned file, `contacts.json` next to
this module, or the file `FTF_VICTIM_CONTACTS` names (docs/victim-contacts.md). It has two
scopes, chosen by where the run goes:

- `local`: runs on this machine (the case-61 replay, fixture pages). Nothing leaves the
  machine, so a reserved `victim.test` address and a fixed fictional number per persona are used.
- `live`: real sites. The team mailbox domain, and phone numbers that are used only once the
  file names who confirmed the range cannot be assigned (VV-31). With no domain, a form that
  needs an e-mail is filled but not sent; with no usable number, a form that requires a phone
  stops the run ("phone required"). Optional phone fields stay empty.

Every other value comes from the persona, never from the model, and every value is read back
from the page before sending (VV-21: no data that is not the persona's).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


CONTACTS_FILE = Path(__file__).with_name("contacts.json")
LOCAL, LIVE = "local", "live"
_PHONE = re.compile(r"^\+?[\d\s-]{9,16}$")
_ADDRESS = re.compile(r"^[a-z0-9._+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailRule(_M):
    domain: str = ""
    pattern: str = "{persona}.{run_id}@{domain}"

    @field_validator("pattern")
    @classmethod
    def _unique_per_run(cls, value: str) -> str:
        if "{run_id}" not in value or "{domain}" not in value:
            raise ValueError("the pattern needs {run_id} and {domain}: one address per run (VV-30)")
        return value


class Scope(_M):
    about: str = ""
    email: EmailRule = Field(default_factory=EmailRule)
    phones: dict[str, str] = Field(default_factory=dict)  # persona id -> number
    phones_confirmed_by: str = ""  # who confirmed the numbers cannot be assigned (VV-31)

    @field_validator("phones")
    @classmethod
    def _numbers(cls, value: dict[str, str]) -> dict[str, str]:
        for persona, number in value.items():
            if not _PHONE.match(number) or len(digits_of(number).removeprefix("48")) != 9:
                raise ValueError(f"{persona}: {number!r} is not a 9-digit Polish number")
        return value


class ContactBook(_M):
    version: str
    about: str = ""
    local: Scope
    live: Scope
    path: str = ""  # where it was read from (filled by the loader)


def load_contacts(path: Path | str | None = None) -> ContactBook:
    """The contacts file: `path`, else the packaged `contacts.json`."""
    path = Path(path) if path else CONTACTS_FILE
    book = ContactBook.model_validate_json(path.read_text(encoding="utf-8"))
    return book.model_copy(update={"path": str(path)})


@dataclass(frozen=True)
class RunContacts:
    """What one run gives, and why (recorded in run.json)."""

    scope: str
    email: str | None
    phone: str | None
    email_policy: str
    phone_policy: str
    book_version: str
    book_path: str

    def to_json(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "email": self.email,
            "phone": self.phone,
            "email_policy": self.email_policy,
            "phone_policy": self.phone_policy,
            "file": self.book_path,
            "version": self.book_version,
        }


def contacts_for(book: ContactBook, persona: Persona, run_id: str, local: bool) -> RunContacts:
    """The run's address and number from the file, for the scope its URL is in."""
    scope_name = LOCAL if local else LIVE
    scope: Scope = getattr(book, scope_name)
    domain = scope.email.domain.strip().lstrip("@").lower()
    email = None
    if domain:
        email = scope.email.pattern.format(persona=persona.id, run_id=run_id, domain=domain).lower()
        if not _ADDRESS.match(email):
            raise ValueError(f"contacts {scope_name}.email gives an invalid address: {email!r}")
        email_policy = f"{scope_name}: per-run address on {domain}"
    else:
        email_policy = f"{scope_name}: no e-mail domain set; forms needing an e-mail are not sent"
    number = scope.phones.get(persona.id)
    if number and (local or scope.phones_confirmed_by.strip()):
        phone = number
        phone_policy = f"{scope_name}: number for {persona.id}" + (
            "" if local else f" (range confirmed by {scope.phones_confirmed_by.strip()})"
        )
    else:
        phone = None
        why = (
            "live numbers are listed but nobody confirmed the range (VV-31)"
            if number
            else f"no {scope_name} number for {persona.id}"
        )
        phone_policy = (
            f"{scope_name}: {why}; optional phone left empty, required phone stops the form"
        )
    return RunContacts(
        scope_name, email, phone, email_policy, phone_policy, book.version, book.path
    )


def national(phone: str) -> str:
    """`+48 500 000 101` -> `500 000 101`."""
    d = digits_of(phone)[-9:]
    return f"{d[:3]} {d[3:6]} {d[6:]}"


def digits_of(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def plan_form(
    form: dict[str, Any], persona: Persona, email: str | None, phone: str | None = None
) -> FillPlan:
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
            if phone:  # from the contacts file; the national part, the form adds +48
                plan.values[xpath] = national(phone)
            # VV-31: no safe number yet. Optional phone fields stay empty.
            elif required:
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


def allowed_values(persona: Persona, email: str | None, phone: str | None = None) -> set[str]:
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
    if phone:
        # Phone widgets reformat what was typed; the digits are what must match.
        values |= {phone, national(phone), digits_of(phone), digits_of(phone)[-9:]}
    return values


def value_allowed(value: str, allowed: set[str], phone: str | None) -> bool:
    if value in allowed:
        return True
    return (
        bool(phone)
        and digits_of(value)[-9:] == digits_of(phone)[-9:]
        and len(digits_of(value)) in (9, 11)
    )


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
