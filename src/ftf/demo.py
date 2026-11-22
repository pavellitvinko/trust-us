"""Demo rehearsal: the jury sequence, checked step by step on a private temporary store."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import pipeline
from .contracts import RelationType, ViewKind
from .eval.reference import resolve_view
from .fusion import run_method
from .grouping import Outcome, assign, build_groups
from .ingest import ingest_folder, ingest_path
from .registry import load_plugins
from .reporting import build_pair_report
from .store import Store

REPLAY_HAR = Path("2") / "healthlinkq.pro.har"


@dataclass
class Step:
    name: str
    status: str  # passed, failed or skipped
    detail: str


class StepFailed(Exception):
    pass


def _require(condition: object, message: str) -> None:
    if not condition:
        raise StepFailed(message)


def _view(store: Store, alias: str) -> str:
    found = resolve_view(store, alias)
    _require(found, f"view not found: {alias}")
    return found


def _relation(store: Store, a: str, b: str) -> RelationType:
    pair = tuple(sorted((a, b)))
    for relation in store.relations():
        if (relation.view_a, relation.view_b) == pair:
            return relation.type
    raise StepFailed(f"no stored relation for {a} and {b}")


def _values(store: Store, view_id: str, *namespaces: str) -> set[str]:
    return {
        str(o.value)
        for o in store.observations(view_id)
        if o.namespace in namespaces and o.value is not None
    }


def _analyse(store: Store, view_ids: list[str]) -> None:
    pipeline.run_extract(store, view_ids)
    pipeline.run_fingerprint(store, view_ids)
    pipeline.run_compare_all(store)
    run_method(store)


def _add_capture(
    store: Store, work: Path, name: str, url: str | None, har: Path | None
) -> tuple[str, str]:
    from .capture import capture_url

    folder = work / name
    result = capture_url(url, folder / "bundle", replay_har=har)
    _require(result.ok, f"capture {result.manifest.status.value}: {result.manifest.error or ''}")
    _, views = ingest_folder(folder, store, name)
    pages = [v for v in views if v.kind is ViewKind.PAGE]
    _require(pages, "the capture produced no page view")
    _analyse(store, [v.view_id for v in views])
    return pages[0].view_id, result.manifest.status.value


def run_demo(
    sample: Path,
    *,
    live_url: str | None = None,
    capture: bool = True,
    on_step: Callable[[Step], None] | None = None,
) -> list[Step]:
    """Run every step, even after a failure, so one rehearsal shows all that needs fixing."""
    load_plugins()
    sample = Path(sample).resolve()
    steps: list[Step] = []
    saved = {k: os.environ.get(k) for k in ("FTF_DATA_DIR", "FTF_EMBEDDER", "FTF_SEED_CORPUS")}

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        os.environ.update(FTF_DATA_DIR=str(work / "data"), FTF_EMBEDDER="fake", FTF_SEED_CORPUS="")
        from .layers._embedder import set_embedder

        set_embedder(None)
        store = Store(work / "data" / "demo.sqlite")
        state: dict[str, str] = {}

        def step(name: str, action: Callable[[], str]) -> None:
            try:
                item = Step(name, "passed", action())
            except StepFailed as exc:
                item = Step(name, "failed", str(exc))
            except Exception as exc:  # noqa: BLE001 - a crash is this step's result
                item = Step(name, "failed", f"{type(exc).__name__}: {exc}")
            steps.append(item)
            if on_step:
                on_step(item)

        def ingest() -> str:
            found = ingest_path(sample, store)
            views = [v.view_id for _, group in found for v in group]
            _require(views, f"no views in {sample}")
            _analyse(store, views)
            return f"{len(found)} incidents, {len(views)} views"

        def campaign_1_2() -> str:
            a, b = _view(store, "1:page"), _view(store, "2:page")
            _require(_relation(store, a, b) is RelationType.SAME_CAMPAIGN, "not one campaign")
            hosts = _values(store, a, "url.host"), _values(store, b, "url.host")
            _require(hosts[0] and hosts[1] and not hosts[0] & hosts[1], "domains are not different")
            names = ("ad.ad_id", "ad.adset_id", "ad.campaign_id")
            ads = [
                {x for v in store.views(case) for x in _values(store, v.view_id, *names)}
                for case in ("1", "2")
            ]
            note = (
                "different ad ID sets"
                if all(ads) and ads[0] != ads[1]
                else "ad IDs not observed in both folders"
            )
            shared = _values(store, a, "fb_pixel") & _values(store, b, "fb_pixel")
            link = f"shared pixel {min(shared)}" if shared else "no shared pixel"
            return f"same campaign on {min(hosts[0])} and {min(hosts[1])}; {link}; {note}"

        def bridge() -> str:
            a, b = _view(store, "2:healthlinkq"), _view(store, "3:page")
            _require(_relation(store, a, b) is RelationType.DUPLICATE, "not a duplicate")
            return "folder 2's second page duplicates case 3"

        def family() -> str:
            a, b = _view(store, "48:page"), _view(store, "61:article")
            _require(_relation(store, a, b) is RelationType.SAME_FAMILY, "not one family")
            groups = build_groups(store, persist_funnel=False)
            c48, c61 = groups.campaign_of[a], groups.campaign_of[b]
            _require(c48 != c61, "campaigns were merged")
            _require(groups.family_of[c48] == groups.family_of[c61], "families differ")
            return "same template, separate campaigns"

        def funnel() -> str:
            article, chat = _view(store, "61:article"), _view(store, "61:chat")
            groups = build_groups(store, persist_funnel=False)
            edges = {(e.source, e.target) for e in groups.funnel_edges["61"][0]}
            _require((article, chat) in edges, "no article to chat edge")
            pages = [v.view_id for v in store.views("61") if v.kind is ViewKind.PAGE]
            decoys = [v for v in pages if v not in (article, chat)]
            _require(len(decoys) == 1, "decoy page not found")
            _require(
                _values(store, decoys[0], "url.host") & _values(store, article, "url.host"),
                "decoy is not on the same domain",
            )
            _require(all(decoys[0] not in edge for edge in edges), "decoy is in the funnel")
            ads = sum(v.kind is ViewKind.IMAGE for v in store.views("61"))
            return f"article to chat, decoy kept out; {ads} ad creative(s) in the folder"

        def video_only() -> str:
            views = store.views("5")
            _require(views and all(v.kind is ViewKind.VIDEO for v in views), "case 5 is not video")
            result = assign(store, views[0].view_id)
            _require(result.outcome is Outcome.INSUFFICIENT, f"got {result.outcome.value}")
            return "honest insufficient evidence"

        def replay() -> str:
            har = sample / REPLAY_HAR
            _require(har.is_file(), f"no HAR at {REPLAY_HAR}")
            view, status = _add_capture(store, work, "demo-replay", None, har)
            state["replay"] = view
            return f"replay {status}: {view}"

        def live() -> str:
            view, status = _add_capture(store, work, "demo-live", live_url, None)
            return f"live {status}: {view}"

        def new_material() -> str:
            view = state.get("replay")
            _require(view, "the replay step produced no view")
            result = assign(store, view)
            _require(result.outcome is not Outcome.INSUFFICIENT, "insufficient evidence")
            related = [x for items in result.related.values() for x in items]
            _require(related, "no related known material")
            report = build_pair_report(store, view, related[0].view_id)
            compared = [row for row in report["layers"] if row["status"] == "compared"]
            _require(compared and report["sources"], "evidence table is empty")
            return f"{result.headline()}; evidence from {len(compared)} layers"

        try:
            step("1. Ingest the sample", ingest)
            step("2. Cases 1 and 2: one campaign", campaign_1_2)
            step("3. Folder 2's second page and case 3", bridge)
            step("4. Cases 48 and 61: family", family)
            step("5. Case 61: funnel and decoy", funnel)
            step("6. Case 5: video only", video_only)
            if capture:
                step("7a. Capture: replay a sample HAR", replay)
                if live_url:
                    step("7b. Capture: live URL", live)
                else:
                    steps.append(Step("7b. Capture: live URL", "skipped", "no --live-url given"))
                    if on_step:
                        on_step(steps[-1])
                step("8. New material: assignment and evidence", new_material)
            else:
                for name in ("7a. Capture: replay", "7b. Capture: live URL", "8. New material"):
                    steps.append(Step(name, "skipped", "capture turned off"))
                    if on_step:
                        on_step(steps[-1])
        finally:
            store.close()
            set_embedder(None)
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return steps
