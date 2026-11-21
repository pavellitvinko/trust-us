"""Dated warning-list snapshots. Only CERT and KNF have automated fetch paths."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

CERT_URL = "https://hole.cert.pl/domains/v2/domains.txt"
KNF_URL = "https://www.knf.gov.pl/dla_konsumenta/ostrzezenia_publiczne"
MF_URL = "https://hazard.mf.gov.pl/"
MF_REGISTER_URL = "https://hazard.mf.gov.pl/api/Register"
URLS = {"cert": CERT_URL, "knf": KNF_URL, "gambling": MF_URL}
PARSER_VERSION = "3"
MAX_DOWNLOAD = 32 * 1024 * 1024


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def domain(value: str, *, allow_underscores: bool = False) -> str | None:
    """A host, never a substring, IP address, wildcard or URL path."""
    try:
        host = urlsplit(value if "://" in value else "//" + value).hostname
        if not host:
            return None
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        return None
    if len(host) > 253 or "." not in host or host.replace(".", "").isdigit():
        return None
    labels = host.split(".")
    for i, label in enumerate(labels):
        pattern = (
            r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
            if allow_underscores and i < len(labels) - 1
            else r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        )
        if not re.fullmatch(pattern, label):
            return None
    return host


def parent_hosts(host: str):
    parts = host.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


class _Tables(HTMLParser):
    """Read cells in document order; ignore scripts and non-table text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.rows = None
        self.row = None
        self.cell = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.rows = []
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data):
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(
                "\n".join(
                    " ".join(line.split())
                    for line in "".join(self.cell).splitlines()
                    if line.strip()
                )
            )
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.rows is not None:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None
            self.row = None
            self.cell = None


def parse_knf(content: str) -> list[dict]:
    parser = _Tables()
    parser.feed(content)
    records = []
    for table_no, rows in enumerate(parser.tables, 1):
        # Select the named-entity column; do not search prosecutors or case notes.
        column = None
        for row_no, cells in enumerate(rows, 1):
            header = next(
                (i for i, c in enumerate(cells) if "nazwa podmiotu" in c.casefold()), None
            )
            if header is not None:
                column = header
                continue
            if column is None or len(cells) <= column or not re.fullmatch(r"\d+\.?", cells[0]):
                continue
            text = cells[column]
            if not text:
                continue
            # A row can concern several entities. Never give them one shared entity ID.
            entities = re.split(r"(?:^|\s)\d+\.\s+(?=[A-ZŁŚŻŹĆŃÓĄĘ„\"])", text)
            separated = []
            for entity in entities:
                lines = []
                for line in entity.splitlines():
                    if lines and re.match(
                        r"^.{1,150}\s+[-–]\s+(?:prowadząc\w*|za pośrednictwem)\b", line
                    ):
                        separated.append(" ".join(lines))
                        lines = []
                    lines.append(line)
                separated.append(" ".join(lines))
            for entity in filter(None, (" ".join(s.split()) for s in separated)):
                records.append(
                    {
                        "id": "knf:" + digest(entity.casefold())[:24],
                        "name": entity,
                        "raw": cells,
                        "locator": f"table[{table_no}].row[{row_no}].cell[{column + 1}]",
                    }
                )
    if not records:
        raise ValueError("KNF entity tables missing or empty")
    # A changed/truncated page must not silently become an authoritative partial list.
    count = re.search(r"Liczba\s+znalezionych\s+ostrzeżeń\s*:\s*(\d+)", " ".join(parser.text))
    if count:
        row_count = len({r["locator"] for r in records})
        if row_count != int(count[1]):
            raise ValueError(f"KNF row count mismatch: expected {count[1]}, parsed {row_count}")
    return records


def parse_domains(content: str) -> list[dict]:
    records = []
    for i, line in enumerate(content.splitlines(), 1):
        raw = line.strip().lstrip("\ufeff")
        if not raw or raw.startswith("#"):
            continue
        host = domain(raw)
        # Refuse HTML, headers and URL-shaped lines in a domain-only export.
        if host is None or any(c in raw for c in "/ :<>\t"):
            raise ValueError(f"invalid domain at line {i}")
        records.append({"id": host, "domain": host, "raw": raw, "locator": f"line[{i}]"})
    if not records:
        raise ValueError("domain list empty")
    return records


def parse_gambling_xml(content: str) -> list[dict]:
    """Parse a browser-downloaded active MF register, retaining each public record ID."""
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", content, re.IGNORECASE):
        raise ValueError("XML declarations with external/custom entities are not supported")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid gambling-register XML") from exc
    ns = "{http://www.hazard.mf.gov.pl/2017/03/21/}"
    if root.tag != ns + "Rejestr":
        raise ValueError("expected the official MF register XML namespace and root")
    records, ids = [], set()
    for entry in root:
        number = entry.get("Lp", "")
        raw = (entry.findtext(ns + "AdresDomeny") or "").strip()
        listed_at = (entry.findtext(ns + "DataWpisu") or "").strip()
        if entry.tag != ns + "PozycjaRejestru" or not number.isdecimal() or number in ids:
            raise ValueError("invalid or duplicate MF register record ID")
        if entry.find(ns + "DataWykreslenia") is not None:
            raise ValueError("active MF register required; removed entries are not supported")
        # MF publishes some addresses with underscores in a non-TLD label. Preserve and
        # match those exact host labels; URL paths, wildcards and other syntax stay invalid.
        host = (
            domain(raw, allow_underscores=True)
            if not any(c in raw for c in "/ :<>\t\r\n")
            else None
        )
        if not raw or not listed_at:
            raise ValueError(f"invalid MF register entry {number}")
        datetime.fromisoformat(listed_at)  # Preserve the source value, including its precision.
        ids.add(number)
        records.append(
            {
                "id": f"mf:{number}",
                "domain": host,
                "validation_error": None if host else "unsupported_domain_syntax",
                "listed_at": listed_at,
                "raw": {"Lp": number, "AdresDomeny": raw, "DataWpisu": listed_at},
                "locator": f"xml:Rejestr/PozycjaRejestru[@Lp='{number}']/AdresDomeny",
            }
        )
    if not records:
        raise ValueError("MF register empty")
    return records


@dataclass
class Snapshot:
    source: str
    source_url: str
    fetched_at: str
    sha256: str
    content: str
    records: list[dict]
    path: str = ""
    access: str = "automatic"

    def provenance(self) -> dict:
        return {
            "source": self.source,
            "source_url": self.source_url,
            "snapshot_date": self.fetched_at[:10],
            "fetched_at": self.fetched_at,
            "sha256": self.sha256,
            "snapshot_file": self.path,
            "parser_version": PARSER_VERSION,
            "access": self.access,
            "record_count": len(self.records),
            "unmatchable_record_count": sum(
                "domain" in r and r["domain"] is None for r in self.records
            ),
        }


def make_snapshot(source: str, content: str, fetched_at: str, *, access="automatic") -> Snapshot:
    stamp = datetime.fromisoformat(fetched_at)
    if stamp.tzinfo is None:
        raise ValueError("snapshot timestamp must include timezone")
    if source not in URLS or (source == "gambling" and access != "manual"):
        raise ValueError("gambling snapshots require manual access")
    is_gambling_xml = source == "gambling" and content.lstrip("\ufeff \r\n\t").startswith("<")
    records = (
        parse_gambling_xml(content)
        if is_gambling_xml
        else parse_knf(content)
        if source == "knf"
        else parse_domains(content)
    )
    source_url = MF_REGISTER_URL if is_gambling_xml else URLS[source]
    return Snapshot(
        source, source_url, fetched_at, digest(content), content, records, access=access
    )


def save_snapshot(snapshot: Snapshot, path: Path) -> None:
    body = {
        "schema_version": 1,
        "source": snapshot.source,
        "source_url": snapshot.source_url,
        "fetched_at": snapshot.fetched_at,
        "sha256": snapshot.sha256,
        "access": snapshot.access,
        "content": snapshot.content,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        temp = Path(f.name)
        json.dump(body, f, ensure_ascii=False)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    snapshot.path = str(path.resolve())


def read_snapshot(path: Path, source: str) -> Snapshot:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        raise ValueError("snapshot schema mismatch")
    required = ("source", "source_url", "fetched_at", "sha256", "access", "content")
    if any(not isinstance(body.get(key), str) for key in required):
        raise ValueError("snapshot fields missing or invalid")
    if body["source"] != source or body["access"] not in ("manual", "automatic"):
        raise ValueError("snapshot schema/source mismatch")
    snapshot = make_snapshot(source, body["content"], body["fetched_at"], access=body["access"])
    if body["sha256"] != snapshot.sha256 or body["source_url"] != snapshot.source_url:
        raise ValueError("snapshot integrity/source mismatch")
    snapshot.path = str(path.resolve())
    return snapshot


def archive_path(cache: Path, snapshot: Snapshot) -> Path:
    # Identical source bytes fetched on different dates remain separate dated evidence.
    return (
        cache
        / "archive"
        / (f"{snapshot.source}-{snapshot.sha256}-{digest(snapshot.fetched_at)[:12]}.json")
    )


def cache_snapshot(snapshot: Snapshot, cache: Path) -> None:
    archive = archive_path(cache, snapshot)
    save_snapshot(snapshot, archive)
    save_snapshot(snapshot, cache / f"{snapshot.source}.json")
    snapshot.path = str(archive.resolve())


def download(url: str) -> bytes:
    if url not in (CERT_URL, KNF_URL):
        raise ValueError("automated source not allowed")
    with urlopen(Request(url, headers={"User-Agent": "FollowTheFake/0.1"}), timeout=15) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("warning list exceeds download limit")
    return data


@dataclass
class Sources:
    snapshots: dict[str, Snapshot] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def load_sources(
    cache: Path, *, refresh=False, fetch=download, gambling: Path | None = None
) -> Sources:
    """One refresh per source per session. Failed refreshes never replace valid data."""
    result = Sources()
    for source in ("cert", "knf", "gambling"):
        candidates = [gambling] if source == "gambling" and gambling else [cache / f"{source}.json"]
        if not (source == "gambling" and gambling):
            candidates.extend(
                sorted(
                    (cache / "archive").glob(f"{source}-*.json"),
                    key=lambda p: p.stat().st_mtime_ns,
                    reverse=True,
                )
            )
        if source == "knf" or (source == "gambling" and not gambling):
            candidates.append(Path(__file__).with_name("snapshots") / f"{source}.json")
        for path in candidates:
            try:
                snapshot = read_snapshot(path, source)
                archive = archive_path(cache, snapshot)
                if path == cache / f"{source}.json" and archive.is_file():
                    snapshot = read_snapshot(archive, source)
                result.snapshots[source] = snapshot
                break
            except (OSError, ValueError, KeyError, TypeError) as exc:
                result.errors[source] = f"snapshot unavailable: {type(exc).__name__}"
        if refresh and source in ("cert", "knf"):
            try:
                content = fetch(URLS[source]).decode("utf-8-sig")
                snapshot = make_snapshot(source, content, datetime.now(UTC).isoformat())
                # Preserve the exact source version referenced by existing observations.
                cache_snapshot(snapshot, cache)
                result.snapshots[source] = snapshot
                result.errors.pop(source, None)
            except (OSError, ValueError, HTTPException) as exc:
                result.errors[source] = f"refresh failed: {type(exc).__name__}"
        elif source in result.snapshots:
            result.errors.pop(source, None)
    return result
