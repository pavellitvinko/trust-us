"""The bait identity and the ledger of which site received which one.

With a bait mail domain each site gets its own address, which attributes a later message
exactly. With one shared mailbox, the unique invented name does that instead. The password
exists only in memory, for one run.
"""

from __future__ import annotations

import json
import os
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

FIRST_NAMES = (
    "Adela", "Bogumił", "Celina", "Dobromir", "Eryka", "Florian", "Gustawa", "Hilary",
    "Izolda", "Jarogniew", "Klementyna", "Lubomir", "Marcelina", "Nikodem", "Olimpia",
    "Przemysław", "Rozalia", "Sylwester", "Teodora", "Walenty",
)  # fmt: skip
# Invented, so that a message naming one cannot be about a real person of that name.
LAST_NAMES = (
    "Brzozolski", "Cieniawicz", "Dąbrowiec", "Grabolicki", "Jaskrowiak", "Kalinowiec",
    "Lipobrodzki", "Modrzewiec", "Olszanicki", "Piołunowicz", "Rokitnicki", "Sosnołęcki",
    "Tatarakowski", "Wierzbolik", "Zawilcowski", "Żurawinski", "Bluszczewicz", "Chabrowiak",
    "Kosaćcowski", "Macierzankiewicz",
)  # fmt: skip


def bait_address(first: str, last: str, domain: str) -> str:
    """A unique, ordinary-looking address: `adela.brzozolski.7f3a@bait.example`."""
    plain = f"{first}.{last}".lower().replace("ł", "l")
    plain = unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode()
    return f"{plain}.{secrets.token_hex(2)}@{domain.lower()}"


@dataclass
class Persona:
    first_name: str
    last_name: str
    email: str
    password: str = field(repr=False, default_factory=lambda: f"{secrets.token_urlsafe(14)}aA1!")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def username(self) -> str:
        return f"{self.first_name}.{self.last_name}".lower().encode("ascii", "ignore").decode()


def ledger_path(data_dir: Path) -> Path:
    """The sandbox's ledger when the app reads it, else this data folder's own."""
    sandboxed = data_dir / "probe-sandbox" / "probe" / "ledger.json"
    return sandboxed if sandboxed.is_file() else data_dir / "probe" / "ledger.json"


class Ledger:
    """`$FTF_DATA_DIR/probe/ledger.json`: one entry per site given an identity. No passwords."""

    def __init__(self, path: Path):
        self.path = path

    def entries(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def resolved(self) -> list[dict]:
        """Entries with absolute bundle paths, wherever this data folder is mounted."""
        base = self.path.parent
        return [
            {**e, "bundle": str((base / e["bundle"]).resolve())} if e.get("bundle") else e
            for e in self.entries()
        ]

    def new_persona(
        self, email: str, site: str, bundle: Path, level: int, mail_domain: str = ""
    ) -> Persona:
        entries = self.entries()
        used = {e.get("name") for e in entries}
        free = [f"{a} {b}" for a in FIRST_NAMES for b in LAST_NAMES if f"{a} {b}" not in used]
        if not free:
            raise RuntimeError("every bait name has been used; extend the lists in persona.py")
        first, last = secrets.choice(free).split(" ", 1)
        if mail_domain:
            taken = {e.get("email") for e in entries}
            email = bait_address(first, last, mail_domain)
            while email in taken:
                email = bait_address(first, last, mail_domain)
        entries.append(
            {
                "name": f"{first} {last}",
                "email": email,
                "site": site,
                "bundle": os.path.relpath(bundle.resolve(), self.path.parent.resolve()),
                "level": level,
                "given_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        self._write(entries)
        return Persona(first, last, email)

    def _write(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)
