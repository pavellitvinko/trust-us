"""The Library report: the state of the whole library, as one printable page.

Built from the same `Library` object the screens show plus counts from the store, so it is
current after every addition with no extra step. Numbers come from code; nothing here calls a
model or the network. The page is self-contained HTML with inline styles; Chromium turns it
into the PDF.
"""

from __future__ import annotations

import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import escape
from typing import Any

from ..library import LAYER_NAMES, UNCHECKED_PLAIN, Item, Library, case_title, plural
from ..llm_checks import CHECK_STEPS
from ..registry import comparators, enrichers, extractors, layers, load_plugins
from ..store import Store
from . import views

_LOCK = threading.Lock()


class PdfUnavailable(RuntimeError):
    """Chromium could not make the PDF (usually: it is not installed)."""


# How strong a link each kind of fingerprint can support, in the words the screens use.
_LEVELS = {
    "T1": "Identity: accounts and records only the operator controls",
    "T2": "Content: the same or nearly the same text, image or video",
    "T3": "Kit: the same template, code or hosting set-up",
    "T4": "Playbook: the same way of persuading",
    "T0": "Noise: common values, never counted",
}
_COMPARATORS = {
    "shared_value": "Shared values, each weighted by how rare it is; unshared values do not "
    "lower the score",
    "set_overlap": "Weighted overlap of two sets (Jaccard); common values weigh nothing",
    "minhash": "MinHash estimate of the overlap of large sets, for near-duplicates",
    "phash": "Perceptual hashes within a small bit distance, best match per picture",
    "calibrated_cosine": "Cosine similarity of embeddings, rescaled against unrelated pairs",
    "pattern_overlap": "Weighted overlap of persuasion-pattern scores",
    "absence_agreement": "Agreement on which expected page elements are missing",
}
_READERS = {
    "asset.hash": "File hashes of images and page files",
    "capture.trace": "Redirects and requests recorded during a capture",
    "conversion.targets": "Where clicks and leads go: forms, phones, affiliate links",
    "domain.shape": "Shape of the web address",
    "ids.ad_params": "Ad and campaign IDs in addresses",
    "infra.headers": "Server and hosting headers",
    "kit.config": "Template settings embedded in the page",
    "kit.url": "Non-standard address parameters",
    "page.identity": "Title, address and what the page says it is",
    "page.structure": "Forms, class names and page skeleton",
    "page.text": "Visible text and article body",
    "tracker.config": "Tracker-platform settings",
    "tracking.pixels": "Advertising and analytics pixel IDs",
    "video.media": "Video key frames and container details",
    "video.stream": "Streaming playlists",
    "ct": "Certificate records of the domain",
    "dns": "DNS records of the domain",
    "rdap": "Registration record of the domain",
    "enrich.warning_lists": "Public warning lists of dangerous sites",
}


def _described(plugin, written: dict[str, str]) -> str:
    """A plugin's one-line description: the written one, else its own docstring's first line."""
    if plugin.name in written:
        return written[plugin.name]
    own = type(plugin).__doc__ or sys.modules[type(plugin).__module__].__doc__ or ""
    return own.strip().split("\n", 1)[0].rstrip(".")


def build_method(store: Store) -> dict[str, Any]:
    """What is run on every added item, read from the plugin registries and the store.

    Counted in SQL, so the report stays quick however large the library grows.
    """
    load_plugins()
    db = store.db
    total = db.execute("SELECT COUNT(*) FROM views").fetchone()[0]
    readings = dict(
        db.execute(
            "SELECT extractor, COUNT(*) FROM observations "
            "WHERE view_id IN (SELECT view_id FROM views) GROUP BY extractor"
        )
    )
    have = dict(
        db.execute(
            "SELECT layer_id, COUNT(*) FROM fingerprints "
            "WHERE view_id IN (SELECT view_id FROM views) GROUP BY layer_id"
        )
    )
    outside = set(enrichers.names())

    def readers(plugins) -> list[dict[str, Any]]:
        return [
            {"id": p.name, "what": _described(p, _READERS), "readings": readings.get(p.name, 0)}
            for p in plugins
        ]

    prints = [
        {
            "id": layer.name,
            "name": LAYER_NAMES.get(layer.name, layer.name),
            "level": layer.tier.value,
            "comparator": layer.comparator,
            "items": have.get(layer.name, 0),
        }
        for layer in layers.all()
    ]
    prints.sort(key=lambda r: (r["level"] == "T0", r["level"], r["id"]))
    used = Counter(r["comparator"] for r in prints)
    return {
        "items": total,
        "extractors": readers(extractors.all()),
        "enrichers": readers(enrichers.all()),
        "fingerprints": prints,
        "comparators": [
            {"id": c.name, "what": _described(c, _COMPARATORS), "fingerprints": used.get(c.name, 0)}
            for c in comparators.all()
        ],
        "model_checks": [label for _, label, _, _, _ in CHECK_STEPS],
        "counts": {
            "readings": sum(n for name, n in readings.items() if name not in outside),
            "outside_readings": sum(n for name, n in readings.items() if name in outside),
            "fingerprints": sum(r["items"] for r in prints),
            "evidence": db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
            "relations": db.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
        },
    }


def _checked(item: Item) -> bool:
    return len(item.risk.unchecked) < len(UNCHECKED_PLAIN)


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def build_stats(
    library: Library, method: dict[str, Any] | None = None, generated_at: str | None = None
) -> dict[str, Any]:
    """Every number in the report, as plain data."""
    items = library.items()
    total = len(items)
    payload = views.campaigns_payload(library)
    campaigns = payload["campaigns"]
    linked = sum(1 for i in items if any(m.kind != "possible" for m in i.matches))
    checked = [i for i in items if _checked(i)]
    with_signs = sum(1 for i in items if i.risk.signs)
    cases = sorted(library.cases, key=lambda c: c.added_at)
    latest = None
    if cases and cases[-1].added_at:
        # Cases added in one go share one time, so the latest addition can be several cases.
        batch = [c for c in cases if c.added_at == cases[-1].added_at]
        added = [i for c in batch for i in c.items]
        latest = {
            "case": (batch[0].title or case_title(batch[0].case_id))
            if len(batch) == 1
            else plural(len(batch), "case"),
            "day": batch[0].added_at[:10],
            "items": len(added),
            "linked_to_earlier": sum(
                1 for i in added
                if any(not m.same_case and m.kind != "possible" for m in i.matches)
            ),
        }  # fmt: skip

    host_cases: dict[str, set[str]] = {}
    for i in items:
        if i.host:
            host_cases.setdefault(i.host, set()).add(i.case_id)

    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "totals": {
            "items": total,
            "cases": len(library.cases),
            "campaigns": len(campaigns),
            "kits": len(library.kits),
            "linked": linked,
            "linked_pct": _pct(linked, total),
            "with_signs": with_signs,
            "checked": len(checked),
            "websites": len(host_cases),
        },
        "latest": latest,
        "method": method,
        "campaigns": [
            {
                "label": c["title"] or c["name"],
                "items": c["items"],
                "cases": c["cases"],
                "linked_by": c["linkedBy"],
                "strength": c["strength"],
                "signs": c["signs"]["text"],
                "kit": c["kit"],
            }
            for c in campaigns
        ],
        "kits": [
            {
                "label": k["title"] or k["name"],
                "made_of": k["sub"],
                "cases": k["cases"],
                "linked_by": k["linkedBy"],
                "signs": k["signs"]["text"],
            }
            for k in payload["kits"]
        ],
        "campaigns_across_cases": sum(1 for c in campaigns if "," in c["cases"]),
    }


# -- the page -------------------------------------------------------------------------------

_CSS = """
@page { size: A4; margin: 14mm 14mm 15mm; }
:root { --ink: #1f1f1d; --ink-2: #5f5e5a; --ink-3: #8a877f; --line: #d9d7d0; --soft: #f3f2ed;
  --bar: #2a78d6; }
* { box-sizing: border-box; }
body { font: 9.5pt/1.4 "Lato", "DejaVu Sans", -apple-system, "Segoe UI", sans-serif;
  color: var(--ink); margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
h2 { font-size: 11pt; margin: 0 0 1pt; }
p { margin: 0 0 5pt; }
.meta, .note { color: var(--ink-2); font-size: 8.5pt; }
.lead { background: var(--soft); border-left: 3pt solid var(--bar); padding: 6pt 9pt;
  margin: 8pt 0; }
.tiles { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6pt; margin: 8pt 0 10pt; }
.tile { border: .8pt solid var(--line); border-radius: 4pt; padding: 5pt 7pt; }
.tile .n { font-size: 17pt; font-weight: 700; line-height: 1.15; }
.tile .l { font-size: 8pt; color: var(--ink-2); }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10pt 14pt; }
.panel { break-inside: avoid; border-top: .8pt solid var(--line); padding-top: 6pt; }
.panel.wide { grid-column: 1 / -1; }
table { border-collapse: collapse; width: 100%; margin-top: 4pt; font-size: 8.5pt; }
th { text-align: left; color: var(--ink-2); font-weight: 600; border-bottom: .8pt solid var(--line);
  padding: 2pt 6pt 2pt 0; }
td { border-bottom: .5pt solid var(--line); padding: 2.5pt 6pt 2.5pt 0; vertical-align: top; }
td.num, th.num { text-align: right; }
tr { break-inside: avoid; }
.grid.method { margin-top: 10pt; }
.flow { display: grid; grid-template-columns: repeat(6, 1fr); gap: 5pt; margin: 5pt 0 4pt; }
.step { border: .8pt solid var(--line); border-top: 2.5pt solid var(--bar); border-radius: 3pt;
  padding: 4pt 5pt; font-size: 8pt; display: flex; flex-direction: column; gap: 3pt; }
.step .k { font-weight: 700; font-size: 9pt; }
.step .out { margin-top: auto; font-weight: 600; }
tr.group td { background: var(--soft); font-weight: 600; border-bottom: 0; padding-left: 4pt; }
td.dim { color: var(--ink-3); }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 7.5pt; }
"""


def _short(text: str, most: int) -> str:
    return text if len(text) <= most else text[: most - 1].rstrip() + "…"


def _panel(title: str, note: str, body: str, wide: bool = False) -> str:
    if not body:
        return ""
    return (
        f'<section class="panel{" wide" if wide else ""}"><h2>{escape(title)}</h2>'
        f'<div class="note">{escape(note)}</div>{body}</section>'
    )


def _campaign_table(stats: dict[str, Any]) -> str:
    rows = stats["campaigns"]
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{escape(_short(r['label'], 70))}</td><td class='num'>{r['items']}</td>"
        f"<td>{escape(r['cases'])}</td><td>{escape(r['linked_by'])}</td>"
        f"<td>{escape(r['strength'])}</td><td>{escape(r['signs'])}</td>"
        f"<td>{escape(r['kit'])}</td></tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>Campaign</th><th class='num'>Items</th><th>Cases</th>"
        "<th>Linked by</th><th>Link</th><th>Warning signs</th><th>Kit</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _kit_table(stats: dict[str, Any]) -> str:
    rows = stats["kits"]
    if not rows:
        return ""
    body = "".join(
        f"<tr><td>{escape(_short(r['label'], 70))}</td><td>{escape(r['made_of'])}</td>"
        f"<td>{escape(r['cases'])}</td><td>{escape(r['linked_by'])}</td>"
        f"<td>{escape(r['signs'])}</td></tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>Kit</th><th>Made of</th><th>Cases</th><th>Linked by</th>"
        f"<th>Warning signs</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _flow(method: dict[str, Any]) -> str:
    """The six steps every added item goes through, with what each has produced so far."""
    c = method["counts"]
    built = sum(1 for f in method["fingerprints"] if f["items"])
    steps = (
        ("Ingest", "Files, folders and captured links become items in a case.",
         plural(method["items"], "item")),
        ("Extract", f"{len(method['extractors'])} extractors read each item's own files.",
         plural(c["readings"], "reading")),
        ("Enrich", "Outside records of each domain, and warning lists.",
         plural(c["outside_readings"], "reading")),
        ("Fingerprint", (f"Readings become {len(method['fingerprints'])} kinds of fingerprint; "
         "pages shown differently to reviewers are flagged."),
         f"{plural(c['fingerprints'], 'fingerprint')}, {built} kinds in use"),
        ("Compare", "Every item against every other, fingerprint by fingerprint.",
         f"{c['evidence']} {'match' if c['evidence'] == 1 else 'matches'} kept"),
        ("Group", "Matches are fused into one verdict per pair, then into campaigns and kits.",
         plural(c["relations"], "pair verdict")),
    )  # fmt: skip
    boxes = "".join(
        f'<div class="step"><div class="k">{n}. {escape(name)}</div><div>{escape(what)}</div>'
        f'<div class="out">{escape(out)}</div></div>'
        for n, (name, what, out) in enumerate(steps, 1)
    )
    checks = ", ".join(label.lower() for label in method["model_checks"])
    return (
        f'<div class="flow">{boxes}</div>'
        '<div class="note">Steps 1 to 6 run automatically on every addition and use no model. '
        f"On request, model checks add to the readings and the library is regrouped: "
        f"{escape(checks)}.</div>"
    )


def _fingerprint_table(method: dict[str, Any]) -> str:
    total = method["items"]
    out, level = [], None
    for f in method["fingerprints"]:
        if f["level"] != level:
            level = f["level"]
            out.append(f'<tr class="group"><td colspan="4">{escape(_LEVELS.get(level, level))}'
                       "</td></tr>")  # fmt: skip
        have = f"{f['items']} of {total}" if f["items"] else "none yet"
        out.append(
            f"<tr><td>{escape(f['name'])}</td><td><code>{escape(f['id'])}</code></td>"
            f"<td><code>{escape(f['comparator'])}</code></td>"
            f"<td class='num{'' if f['items'] else ' dim'}'>{have}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Fingerprint</th><th>ID</th><th>Compared with</th>"
        f"<th class='num'>Items that have it</th></tr></thead><tbody>{''.join(out)}</tbody></table>"
    )


def _method_table(method: dict[str, Any]) -> str:
    def rows(title: str, entries: list[dict[str, Any]], count: str, word: str) -> str:
        head = f'<tr class="group"><td colspan="3">{escape(title)}</td></tr>'
        return head + "".join(
            f"<tr><td><code>{escape(e['id'])}</code></td><td>{escape(e['what'])}</td>"
            f"<td class='num'>{plural(e[count], word)}</td></tr>"
            for e in entries
        )

    return (
        "<table><thead><tr><th>Method</th><th>What it does</th><th class='num'>Used for</th>"
        "</tr></thead><tbody>"
        + rows("Reading the material (extractors)", method["extractors"], "readings", "reading")
        + rows("Outside sources (enrichers)", method["enrichers"], "readings", "reading")
        + rows(
            "Comparing fingerprints (comparators)",
            method["comparators"],
            "fingerprints",
            "fingerprint",
        )
        + "</tbody></table>"
    )


def _lead(stats: dict[str, Any]) -> str:
    t = stats["totals"]
    if not t["items"]:
        return "The library is empty: nothing has been added yet."
    text = (
        f"{plural(t['items'], 'item')} in {plural(t['cases'], 'case')}. "
        f"{t['linked']} ({t['linked_pct']}%) {'is' if t['linked'] == 1 else 'are'} linked to "
        "at least one other item, "
        f"forming {plural(t['campaigns'], 'campaign')}"
        + (f" and {plural(t['kits'], 'shared kit')}" if t["kits"] else "")
        + "."
    )
    across = stats["campaigns_across_cases"]
    if across:
        text += (
            f" {plural(across, 'campaign')} span{'s' if across == 1 else ''} more than one case."
        )
    latest = stats["latest"]
    if latest:
        text += (
            f" Latest addition: {latest['case']} on {latest['day']}, "
            f"{plural(latest['items'], 'item')}, {latest['linked_to_earlier']} linked to "
            "material from other cases."
        )
    return text


def render_html(stats: dict[str, Any]) -> str:
    """The report as one self-contained page: no scripts, no outside files."""
    t = stats["totals"]
    tiles = "".join(
        f'<div class="tile"><div class="n">{escape(str(n))}</div><div class="l">{escape(label)}'
        "</div></div>"
        for n, label in (
            (t["items"], "item" if t["items"] == 1 else "items"),
            (t["cases"], "case" if t["cases"] == 1 else "cases"),
            (t["campaigns"], "campaign" if t["campaigns"] == 1 else "campaigns"),
            (t["kits"], "shared kit" if t["kits"] == 1 else "shared kits"),
            (f"{t['linked_pct']}%", "of items linked"),
            (t["with_signs"], "items with warning signs"),
        )
    )
    panels = _panel(
        "Campaigns",
        "Every campaign: items very likely run by the same people. A page found in several "
        "cases counts once.",
        _campaign_table(stats),
        wide=True,
    ) + _panel(
        "Shared kits",
        "Every kit: items built from the same template or code, possibly by different people.",
        _kit_table(stats),
        wide=True,
    )
    method = stats.get("method")
    method_part = (
        '<div class="grid method">'
        + _panel(
            "What happens to every added item",
            "The processing flow, with what each step has produced for the whole library.",
            _flow(method),
            wide=True,
        )
        + _panel(
            "Fingerprints",
            "Every kind of fingerprint the system builds, by the strength of link it can support.",
            _fingerprint_table(method),
            wide=True,
        )
        + _panel(
            "Methods applied",
            "Every extractor, outside source and comparison method in use.",
            _method_table(method),
            wide=True,
        )
        + "</div>"
        if method
        else ""
    )
    when = stats["generated_at"].replace("T", " ")[:16]
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Follow the Fake: Library report</title><style>{_CSS}</style></head><body>"
        "<h1>Library report</h1>"
        f'<div class="meta">Follow the Fake · state of the whole library · generated {when} UTC'
        "</div>"
        f'<p class="lead">{escape(_lead(stats))}</p>'
        f'<div class="tiles">{tiles}</div>'
        f'<div class="grid">{panels}</div>'
        f"{method_part}"
        "</body></html>"
    )


def _print(html: str) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(java_script_enabled=False)
            context.route("**/*", lambda route: route.abort())  # the page needs nothing outside
            page = context.new_page()
            page.set_content(html, wait_until="load")
            return page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            browser.close()


def to_pdf(html: str) -> bytes:
    """The page as a PDF, printed by headless Chromium with scripts and network off."""
    with _LOCK:
        try:  # Playwright's sync API wants a thread without an event loop
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_print, html).result()
        except Exception as exc:  # noqa: BLE001 - reported to the person as one plain reason
            raise PdfUnavailable(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
