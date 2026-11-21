"""N3–N5 observations and contextual links, kept out of campaign fusion."""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from ..contracts import Observation, Purpose, Source, State
from ..registry import enrichers
from .warning_sources import Sources, digest, domain, load_sources, parent_hosts

NAME = "enrich.warning_lists"
VERSION = "3"
SIGNALS = {"cert": "N3", "knf": "N4", "gambling": "N5"}
DOMAIN_NAMESPACES = {"url.host", "domain.registrable", "image.domain"}
ENTITY_NAMESPACES = {"page.entity.organisation", "brand.offer", "offer.name", "image.brand_offer"}
DOMAIN_RE = re.compile(r"(?:[\w-]+\.)+[a-zA-Z]{2,63}", re.UNICODE)
GENERIC = {
    "bank",
    "finance",
    "financial",
    "capital",
    "investment",
    "investments",
    "group",
    "global",
    "trade",
    "trading",
    "platform",
    "platforma",
}


def normalise_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold().replace("ł", "l"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = " ".join(re.findall(r"[a-z0-9]+", text))
    return re.sub(
        r"\s+(?:sp z o o|spolka z ograniczona odpowiedzialnoscia|s a|sa|ltd|limited|sp k)$",
        "",
        text,
    ).strip()


def aliases(name: str) -> set[str]:
    # Only names in the entity cell. Procedural notes never become candidate aliases.
    core = re.split(
        r"\s+(?:z siedzibą|prowadząc\w*|działając\w*|administrator\w*|"
        r"posługując\w*|oferując\w*|właściciel\w*)\b",
        name,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    candidates = [re.split(r"\s*\(|https?://|www\.", core, maxsplit=1)[0]]
    candidates += re.findall(r"[„\"]([^\"”]+)[”\"]", name)
    for group in re.findall(r"\(([^()]+)\)", name):
        if not re.search(
            r"podmiot|KRS|NIP|REGON|siedzib|likwidac|obecnie|dawniej", group, re.IGNORECASE
        ):
            candidates += re.split(r",|;|\s+oraz\s+", group)
    # Platform names often follow the legal operator name.
    for group in re.findall(r"platform\w*\s+(?:handlow\w*\s*)?:?\s*(.+)", name):
        for piece in re.split(r",\s*(?![^()]*\))|\s+oraz\s+", group):
            candidates.append(piece.split("(", 1)[0])
    out = {normalise_name(c.strip(' .„”"')) for c in candidates if not domain(c.strip(' .„”"'))}
    return {c for c in out if len(c) >= 4 and c not in GENERIC}


def supported_inputs(observations: list[Observation]) -> list[dict]:
    inputs = []
    for o in observations:
        if o.state is not State.PRESENT or o.extractor == NAME:
            continue
        kind = "domain" if o.namespace in DOMAIN_NAMESPACES else "entity"
        if o.namespace not in DOMAIN_NAMESPACES | ENTITY_NAMESPACES:
            continue
        value = o.value.get("name") if isinstance(o.value, dict) else o.value
        if not isinstance(value, str) or not value.strip():
            continue
        normalised = (
            domain(value, allow_underscores=True) if kind == "domain" else normalise_name(value)
        )
        if not normalised:
            continue
        inputs.append(
            {
                "kind": kind,
                "value": value,
                "normalised": normalised,
                "namespace": o.namespace,
                "source": o.source.model_dump(),
                "extractor": o.extractor,
                "version": o.version,
                "confidence": o.confidence,
            }
        )
    # Prefer the actual host to its registrable-domain derivative. Keep image domains as well.
    hosts = {i["normalised"] for i in inputs if i["namespace"] == "url.host"}
    return [
        i
        for i in inputs
        if not (
            i["namespace"] == "domain.registrable"
            and any(i["normalised"] in parent_hosts(h) for h in hosts)
        )
    ]


class WarningLists:
    name = NAME
    version = VERSION

    def __init__(self, sources: Sources | None = None):
        self.sources = sources or Sources()
        self.domain_indexes = {}
        self.name_index = {}
        for source, snapshot in self.sources.snapshots.items():
            index = self.domain_indexes[source] = {}
            for record in snapshot.records:
                if source == "knf":
                    for alias in aliases(record["name"]):
                        self.name_index.setdefault(alias, []).append(record)
                    domains = {domain(d) for d in DOMAIN_RE.findall(record["name"])}
                else:
                    domains = {record["domain"]}
                for host in domains - {None}:
                    index.setdefault(host, []).append(record)

    def matches(self, source: str, item: dict) -> list[dict]:
        found = []
        value = item["normalised"]
        if item["kind"] == "domain":
            for host in parent_hosts(value):
                for record in self.domain_indexes.get(source, {}).get(host, []):
                    found.append(
                        {"record": record, "method": "domain", "matched_value": host, "score": 1.0}
                    )
        elif source == "knf":
            for alias, records in self.name_index.items():
                exact = value == alias
                if not exact and (len(value) < 6 or value in GENERIC):
                    continue
                score = (
                    1.0 if exact else SequenceMatcher(None, value, alias, autojunk=False).ratio()
                )
                if score >= 0.9:
                    found += [
                        {
                            "record": r,
                            "method": "exact_name" if exact else "fuzzy_name",
                            "matched_value": alias,
                            "score": round(score, 4),
                        }
                        for r in records
                    ]
            exacts = [m for m in found if m["method"] == "exact_name"]
            found = exacts or found
        # Retain the strongest alias per source record.
        unique = {}
        for match in sorted(found, key=lambda m: m["score"], reverse=True):
            unique.setdefault(match["record"]["id"], match)
        return list(unique.values())

    def run(self, view, observations, ctx) -> list[Observation]:
        inputs = supported_inputs(observations)
        out = []
        for source, signal in SIGNALS.items():
            snapshot = self.sources.snapshots.get(source)
            applicable = [i for i in inputs if source == "knf" or i["kind"] == "domain"]
            for item in applicable or [None]:
                value = {
                    "source": source,
                    "input": item,
                    "snapshot": snapshot.provenance() if snapshot else None,
                    "refresh_error": self.sources.errors.get(source),
                    "legitimacy_inference": False,
                }
                if not snapshot or item is None:
                    state = State.NOT_OBSERVABLE
                    value.update(
                        result="unavailable",
                        message=(
                            "source snapshot unavailable" if not snapshot else "no supported input"
                        ),
                    )
                    confidence = 0.0
                else:
                    matches = self.matches(source, item)
                    state = State.PRESENT if matches else State.ABSENT
                    uncertain = source == "knf" and (
                        len(matches) > 1 or any(m["method"] == "fuzzy_name" for m in matches)
                    )
                    confidence = min(
                        item["confidence"], max((m["score"] for m in matches), default=1)
                    )
                    value.update(
                        result="possible_match"
                        if uncertain
                        else "listed"
                        if matches
                        else "not_listed",
                        uncertain=uncertain,
                        matches=matches,
                        message="possible warning-list match; verify entity identity"
                        if uncertain
                        else "listed in warning source"
                        if matches
                        else f"not listed as of {snapshot.fetched_at[:10]}",
                    )
                # Source.file stays incident-relative. External snapshot provenance is in value.
                input_source = item["source"] if item else {}
                out.append(
                    Observation(
                        view_id=view.view_id,
                        signal_id=signal,
                        namespace=f"warning.{source}",
                        value=value,
                        raw=item["value"] if item else None,
                        state=state,
                        purpose=Purpose.RISK,
                        source=Source(**input_source),
                        extractor=NAME,
                        version=VERSION,
                        confidence=confidence,
                    )
                )
        return out


# The default registered instance is offline; session orchestration supplies the snapshots.
enrichers.register(WarningLists)


def run_warning_lists(
    store,
    cache: Path,
    *,
    view_ids=None,
    refresh=False,
    gambling: Path | None = None,
    sources: Sources | None = None,
) -> dict:
    selected = store.views() if view_ids is None else [store.get_view(v) for v in view_ids]
    sources = (
        sources if sources is not None else load_sources(cache, refresh=refresh, gambling=gambling)
    )
    enricher = WarningLists(sources)
    from ..registry import Context

    ctx = Context(store)
    count = 0
    for view in selected:
        observations = enricher.run(view, store.observations(view.view_id), ctx)
        store.put_observations(view.view_id, NAME, observations)
        count += len(observations)
    return {
        "views": len(selected),
        "observations": count,
        "errors": sources.errors,
        "snapshots": {s: snap.provenance() for s, snap in sources.snapshots.items()},
    }


def warning_observations(store, view_id: str) -> list[dict]:
    """Stable input for Material, report exports and HAC-44; no network or writes."""
    return [o.model_dump(mode="json") for o in store.observations(view_id) if o.extractor == NAME]


def listed_entities(observations: list[dict]) -> dict[str, list[dict]]:
    entities = {}
    for o in observations:
        value = o["value"]
        if (
            o["state"] != "present"
            or o["version"] == "1"  # Old parsing merged unnumbered entities; rerun before linking.
            or value.get("input", {}).get("kind") != "entity"
            or value["source"] != "knf"
            or value["result"] != "listed"
            or value.get("uncertain")
        ):
            continue
        for match in value["matches"]:
            entities.setdefault(match["record"]["id"], []).append(o)
    return entities


def warning_risk_reasons(observations: list[dict]) -> list[dict]:
    """HAC-44 warning contribution: only hits, with uncertainty and resolvable evidence."""
    reasons = []
    for observation in observations:
        if observation.get("extractor") != NAME:
            continue
        value = observation["value"]
        if observation["state"] != "present" or value.get("result") not in (
            "listed",
            "possible_match",
        ):
            continue
        reasons.append(
            {
                "kind": "domain",
                "check": "warning_list",
                "source": value["source"],
                "message": f"{value['source']}: {value['input']['value']} — {value['message']}",
                "uncertain": value.get("uncertain", False),
                "observation_ref": "warning:" + digest(json.dumps(observation, sort_keys=True)),
                "observation": observation,
            }
        )
    return reasons


def related_listed_views(store, view_id: str) -> list[dict]:
    own = listed_entities(warning_observations(store, view_id))
    related = []
    if not own:
        return related
    for view in store.views():
        if view.view_id == view_id:
            continue
        other = listed_entities(warning_observations(store, view.view_id))
        for entity in sorted(own.keys() & other.keys()):
            related.append(
                {
                    "view_id": view.view_id,
                    "label": view.label,
                    "entity_id": entity,
                    "entity_name": own[entity][0]["value"]["matches"][0]["record"]["name"],
                    "observations": other[entity],
                    "context_only": True,
                }
            )
    return related
