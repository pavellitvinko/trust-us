"""Victim runs: funnel edges, later-stage identifiers and the perception fingerprint.

A victim run (`ftf victim run`, src/ftf/victim/) is an incident of capture bundles, one per
stage, with the run's records in `run_files/`. For each stage view this extractor emits:

- `funnel.stage` (I1): the stage's index and kind in the run;
- `funnel.edge` (I1): the stage it came from, as that stage's view ID, with the navigation
  record as proof (`raw`, and `source` pointing at the line of `navigation.jsonl`). `main`
  has no edge table, so a proven funnel edge is an observation on the stage it leads to;
- `redirect.hop_host` (I5): each host of the HTTP redirects on the way to the stage;
- `form_endpoint_host` / `form_endpoint_path` (A7) and `outbound.domain` (A15): where the
  run's form submissions actually went, from the form log, under the same namespaces the
  conversion extractor uses, so they link with any other capture.

On the entry stage (stage 1) it also emits the run-level perception, from the verified
`run_files/perception.json`: `victim.ask`, `victim.money`, `victim.pitch`,
`victim.pitch.embedding`, `victim.lever` and `funnel.sequence`. It reads files only and never
calls a model.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..contracts import Observation, Purpose, Source, State, View
from ..registry import Context, extractors
from ._material import registrable_domain

NAME = "victim.run"
VERSION = "1"
RUN_FILES = "run_files"
_DAYS = {
    "dzie": 1,
    "day": 1,
    "doba": 1,
    "dób": 1,
    "tydz": 7,
    "tygod": 7,
    "week": 7,
    "miesi": 30,
    "month": 30,
    "rok": 365,
    "lat": 365,
    "year": 365,
    "godzin": 1 / 24,
    "hour": 1 / 24,
}
_PLN = {"zł": 1.0, "zl": 1.0, "pln": 1.0, "eur": 4.3, "€": 4.3, "usd": 4.0, "$": 4.0}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    except (OSError, ValueError):
        return []


def victim_manifest(view: View, ctx: Context) -> tuple[Path, dict[str, Any]] | None:
    """(incident root, the stage's `victim` record) for a victim stage view, else None."""
    if not view.primary or not view.primary.endswith("dom.html"):
        return None
    manifest_rel = str(Path(view.primary).with_name("manifest.json"))
    if manifest_rel not in view.attachments:
        return None
    data = _load_json(ctx.path(view, manifest_rel))
    if not isinstance(data, dict) or not isinstance(data.get("victim"), dict):
        return None
    root = ctx.path(view, view.primary).parent.parent
    return root, data["victim"]


def days_of(text: str | None) -> float | None:
    """`w ciągu kilku dni` -> 3, `24 godziny` -> 1, `2 tygodnie` -> 14."""
    if not text:
        return None
    low = text.lower()
    unit = next((d for k, d in _DAYS.items() if k in low), None)
    if unit is None:
        return None
    found = re.search(r"\d+(?:[.,]\d+)?", low)
    count = (
        float(found.group(0).replace(",", "."))
        if found
        else (3.0 if "kilk" in low or "few" in low else 1.0)
    )
    return max(count * unit, 1 / 24)


def money_vector(items: list[dict[str, Any]]) -> list[float]:
    """[log10 entry, log10 promised return, log10 ratio, log10 days]; 0 where unknown."""
    entry = ret = days = None
    for item in items:
        try:
            amount = float(item.get("value", ""))
        except (TypeError, ValueError):
            continue
        rate = _PLN.get((item.get("currency") or "zł").strip().lower(), 1.0)
        pln = amount * rate
        if item.get("kind") in {"entry_amount", "deposit", "price", "fee"} and entry is None:
            entry = pln
        elif item.get("kind") == "promised_return" and ret is None:
            ret = pln
        days = days if days is not None else days_of(item.get("time_frame"))
    vec = [
        math.log10(1 + entry) if entry else 0.0,
        math.log10(1 + ret) if ret else 0.0,
        math.log10(ret / entry) + 1 if entry and ret else 0.0,  # +1 so ratio 1 is not "unknown"
        math.log10(1 + days) + 0.1 if days else 0.0,
    ]
    return [round(v, 4) for v in vec]


@extractors.register
class VictimRun:
    name = NAME
    version = VERSION

    def run(self, view: View, ctx: Context) -> list[Observation]:
        found = victim_manifest(view, ctx)
        if found is None:
            return []
        root, victim = found
        out: list[Observation] = []

        def emit(
            signal: str,
            namespace: str,
            value: Any,
            file: str,
            locator: str,
            raw: str | None = None,
            state: State = State.PRESENT,
        ) -> None:
            out.append(
                Observation(
                    view_id=view.view_id,
                    signal_id=signal,
                    namespace=namespace,
                    value=value,
                    raw=raw,
                    state=state,
                    purpose=Purpose.LINKAGE,
                    source=Source(file=file, locator=locator),
                    extractor=NAME,
                    version=VERSION,
                )
            )

        stage_dir = Path(view.primary).parent.as_posix()
        index = victim.get("stage")
        emit(
            "I1",
            "funnel.stage",
            {
                "run_id": victim.get("run_id"),
                "index": index,
                "kind": victim.get("stage_kind"),
                "kinds": victim.get("stage_kinds_seen", []),
            },
            f"{stage_dir}/manifest.json",
            "manifest:victim.stage",
        )

        arrived = victim.get("arrived_from") or {}
        nav_line = arrived.get("navigation_line")
        nav_loc = (
            f"{RUN_FILES}/navigation.jsonl:line[{nav_line}]"
            if nav_line
            else "manifest:victim.arrived_from"
        )
        if arrived.get("from_stage"):
            source_view = self._stage_view(ctx, view, int(arrived["from_stage"]))
            proof = {
                k: arrived.get(k)
                for k in (
                    "how",
                    "step",
                    "action",
                    "from_url",
                    "to_url",
                    "http_redirects",
                    "request_method",
                    "referer_sent",
                    "requested_at",
                )
            }
            emit(
                "I1",
                "funnel.edge",
                {
                    "from_view": source_view,
                    "to_view": view.view_id,
                    "how": arrived.get("how"),
                    "from_stage": arrived["from_stage"],
                    "to_stage": index,
                },
                f"{RUN_FILES}/navigation.jsonl",
                nav_loc,
                raw=json.dumps(proof, ensure_ascii=False),
            )
        for hop in arrived.get("http_redirects") or []:
            host = (urlsplit(hop).hostname or "").lower()
            if host:
                emit(
                    "I5",
                    "redirect.hop_host",
                    host,
                    f"{RUN_FILES}/navigation.jsonl",
                    nav_loc,
                    raw=hop,
                )

        for line, form in enumerate(_jsonl(root / RUN_FILES / "forms.jsonl"), 1):
            if form.get("stage") != index:
                continue
            loc = f"{RUN_FILES}/forms.jsonl:line[{line}]"
            targets = [r["url"] for r in form.get("sent_to", []) if r.get("url")]
            targets += [r["url"] for r in form.get("sent_while_typing", []) if r.get("url")]
            for url in targets:
                parts = urlsplit(url)
                host = (parts.hostname or "").lower()
                if not host:
                    continue
                emit("A7", "form_endpoint_host", host, f"{RUN_FILES}/forms.jsonl", loc, raw=url)
                if parts.path.rstrip("/"):
                    emit(
                        "A7",
                        "form_endpoint_path",
                        parts.path.rstrip("/"),
                        f"{RUN_FILES}/forms.jsonl",
                        loc,
                        raw=url,
                    )
                if host != (urlsplit(view.meta.get("manifest_url", "")).hostname or ""):
                    emit(
                        "A15",
                        "outbound.domain",
                        registrable_domain(host),
                        f"{RUN_FILES}/forms.jsonl",
                        loc,
                        raw=url,
                    )

        if index == self._mail_stage(root):
            self._mail(root, emit)
        if index == 1:
            self._perception(root, emit)
        return out

    @staticmethod
    def _mail_stage(root: Path) -> int | None:
        """The stage whose form was given the run's address: mail that arrives follows it."""
        mailbox = _load_json(root / RUN_FILES / "mailbox.json") or {}
        address = (mailbox.get("address") or "").lower()
        if not address:
            return None
        for form in _jsonl(root / RUN_FILES / "forms.jsonl"):
            if address in {str(v).lower() for v in (form.get("values_given") or {}).values()}:
                return form.get("stage")
        return None

    @staticmethod
    def _mail(root: Path, emit: Any) -> None:
        """VV-33, VV-42: who wrote to the run's address, and where their mail points."""
        for line, mail in enumerate(_jsonl(root / RUN_FILES / "mail.jsonl"), 1):
            loc = f"{RUN_FILES}/mail.jsonl:line[{line}]"
            file = mail.get("file") or f"{RUN_FILES}/mail.jsonl"
            if mail.get("sender_domain"):
                emit(
                    "A15",
                    "mail.sender_domain",
                    registrable_domain(mail["sender_domain"]),
                    file,
                    loc,
                    raw=mail.get("from"),
                )
            for host in mail.get("link_hosts", []):
                emit("A15", "mail.link_domain", registrable_domain(host), file, loc, raw=host)
            for phone in mail.get("phones", []):
                emit("A12", "mail.phone", phone, file, loc, raw=phone)

    @staticmethod
    def _stage_view(ctx: Context, view: View, stage: int) -> str | None:
        wanted = f"stage-{stage:02d}/dom.html"
        for other in ctx.store.views(view.incident_id):
            if other.primary and other.primary.endswith(wanted):
                return other.view_id
        return None

    def _perception(self, root: Path, emit: Any) -> None:
        run = _load_json(root / RUN_FILES / "run.json") or {}
        shape = run.get("funnel_shape") or []
        if shape:
            emit("I1", "funnel.sequence", shape, f"{RUN_FILES}/run.json", "run:funnel_shape")
        p = _load_json(root / RUN_FILES / "perception.json")
        persona = (run.get("persona") or {}).get("id", "")
        # What the forms asked for is known from the form log, with or without a model.
        for line, form in enumerate(_jsonl(root / RUN_FILES / "forms.jsonl"), 1):
            for field in form.get("fields", []):
                if field.get("kind") and field["kind"] not in {"other", "forbidden", "consent"}:
                    emit(
                        "I2",
                        "victim.ask",
                        f"data:{field['kind']}",
                        f"{RUN_FILES}/forms.jsonl",
                        f"{RUN_FILES}/forms.jsonl:line[{line}]",
                        raw=field.get("label") or field.get("name"),
                    )
        if not p or p.get("state") != State.PRESENT.value:
            reason = (p or {}).get("reason", "no perception.json")
            emit(
                "H",
                "victim.perception",
                None,
                f"{RUN_FILES}/perception.json",
                "perception",
                raw=reason,
                state=State.NOT_OBSERVABLE,
            )
            return

        def items(field: str) -> list[dict[str, Any]]:
            return (p.get(field) or {}).get("items", [])

        def ev(item: dict[str, Any]) -> tuple[str, str, str]:
            e = item.get("evidence", {})
            return (
                e.get("file") or f"{RUN_FILES}/material.txt",
                e.get("locator", ""),
                e.get("quote_found", ""),
            )

        for item in items("asked_action"):
            file, loc, quote = ev(item)
            emit("I1", "victim.ask", f"action:{item['action']}", file, loc, raw=quote)
        for item in items("asked_data"):
            file, loc, quote = ev(item)
            emit("I2", "victim.ask", f"data:{item['data']}", file, loc, raw=quote)
        for item in items("money_path"):
            file, loc, quote = ev(item)
            emit(
                "F7",
                "victim.money",
                {
                    "kind": item["kind"],
                    "value": item.get("value"),
                    "currency": item.get("currency", ""),
                    "time_frame": item.get("time_frame"),
                },
                file,
                loc,
                raw=quote,
            )
        for item in items("levers"):
            file, loc, quote = ev(item)
            emit("H", "victim.lever", item["lever"], file, loc, raw=quote)
        for field in ("belief", "promise", "doubt_handling", "tailoring", "next_step_promised"):
            for item in items(field):
                file, loc, quote = ev(item)
                emit("F4", f"victim.{field}", item["text"], file, loc, raw=quote)
        for item in items("borrowed_authority"):
            file, loc, quote = ev(item)
            emit(
                "G1",
                "victim.borrowed_authority",
                {"name": item["name"], "kind": item["kind"]},
                file,
                loc,
                raw=quote,
            )
        for item in items("verdict"):
            file, loc, quote = ev(item)
            emit(
                "H",
                "victim.verdict",
                {"answer": item["answer"], "persona": persona, "reason": item["reason"]},
                file,
                loc,
                raw=quote,
            )
        retelling = (p.get("pitch_retelling") or {}).get("text")
        if retelling:
            emit(
                "F4",
                "victim.pitch",
                retelling,
                f"{RUN_FILES}/perception.json",
                "perception:pitch_retelling",
                raw=persona,
            )
        vector = p.get("pitch_embedding") or {}
        if vector.get("values"):
            emit(
                "F3",
                "victim.pitch.embedding",
                {"model": vector["model"], "values": vector["values"]},
                f"{RUN_FILES}/perception.json",
                "perception:pitch_embedding",
                raw=persona,
            )
