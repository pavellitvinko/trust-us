"""Run the library's model checks in dependency order, preserving stage summaries."""

from collections import Counter
from collections.abc import Callable
from typing import Any

from .config import get_settings
from .contracts import ViewKind
from .intake import ANALYSIS_LOCK, IntakeError
from .store import Store

CHECK_STEPS = (
    ("image", "Read images", "Text, people, brands and visual claims", True, True),
    ("page", "Read pages", "Page identity, claims and offers", True, True),
    ("video", "Read videos", "Speech, scenes and on-screen text", True, True),
    ("expectations", "Check expected elements", "Compare pages with their expected elements", True, True),
    ("techniques", "Find persuasion techniques", "Evidence-backed technique cards", True, True),
    ("warnings", "Check warning lists", "Check local snapshots using the extracted names", True, False),
    ("rebuild", "Refresh campaigns", "Prepare current groups for pattern discovery", False, False),
    ("patterns", "Discover and match patterns", "Embed cards, discover patterns and match material", True, False),
    ("finalize", "Update library", "Rebuild links from the available results", False, False),
)


def new_steps() -> list[dict]:
    return [{"id": key, "label": label, "description": description,
             "skippable": skippable, "itemwise": itemwise, "status": "pending",
             "done": 0, "total": None, "current": None, "skipRequested": False}
            for key, label, description, skippable, itemwise in CHECK_STEPS]


def require_model_keys() -> None:
    settings = get_settings()
    missing = [name for name, value in (
        ("OPENAI_API_KEY", settings.openai_api_key),
        ("GEMINI_API_KEY", settings.gemini_api_key),
    ) if not value]
    if missing:
        raise IntakeError(f"Set {' and '.join(missing)} in .env to run all LLM checks.")


def run_llm_checks(
    store: Store, on_stage: Callable[[str], None], results: dict, *,
    should_skip: Callable[[str], bool] = lambda name: False,
    on_progress: Callable[[str, int, int, str | None], None] = lambda *args: None,
    on_complete: Callable[[str, bool], None] = lambda *args: None,
    on_item: Callable[[str, str, dict], None] = lambda *args: None,
) -> None:
    from . import pipeline
    from .enrich.warning_lists import run_warning_lists
    from .fusion import run_method
    from .layers._embedder import GatewayEmbedder, get_embedder, set_embedder
    from .patterns.workflow import run_discover
    from .perception.expectations import run_expectations
    from .perception.image import run_perceive
    from .perception.page import run_perceive_pages
    from .perception.techniques import run_techniques
    from .perception.video import run_perceive_videos
    from .registry import Context

    require_model_keys()
    settings = get_settings()
    ctx = Context(store)
    with ANALYSIS_LOCK:
        views = store.views()
        results["inputs"] = {
            "models": {"reading": settings.openai_model, "video": settings.video_model,
                       "embedding": settings.embedding_model},
            "source_views": dict(Counter(v.kind.value for v in views if not v.meta.get("shipped"))),
            "shipped_views_skipped": sum(bool(v.meta.get("shipped")) for v in views),
        }
        source = [v for v in views if not v.meta.get("shipped")]
        from .processing import applicable_checks, check_result

        for view in source:
            for spec in applicable_checks(view):
                key = spec["id"]
                skipped = should_skip(key)
                on_item(key, view.view_id, {
                    "state": "skipped" if skipped else "queued",
                    "message": "Excluded from this run." if skipped else "Waiting for this check.",
                })

        matched_items: set[str] = set()

        def pattern_item(view_id, summary):
            if summary is None:
                on_item("patterns", view_id, {"state": "running", "message": "Matching patterns for this item."})
            else:
                matched_items.add(view_id)
                on_item("patterns", view_id, check_result("patterns", store.observations(view_id), summary))

        def perform(name, run, selected=None, batch_items=None):
            on_stage(name)
            summary = {}
            if selected is None:
                if not should_skip(name):
                    for view in batch_items or []:
                        on_item(name, view.view_id, {
                            "state": "queued" if name == "patterns" else "running",
                            "message": "Preparing the pattern library before matching this item."
                            if name == "patterns" else "Processing this library batch.",
                        })
                    summary = run()
                    for view in batch_items or []:
                        if name == "patterns" and view.view_id in matched_items:
                            continue
                        part = {"reason": summary.get("reason", "no_result")} if name == "patterns" else summary
                        result = check_result(name, store.observations(view.view_id), part)
                        if name == "patterns" and result["hasResult"]:
                            result.update(state="retained", message="Pattern matching did not finish; earlier results remain available.")
                        on_item(name, view.view_id, result)
                else:
                    for view in batch_items or []:
                        on_item(name, view.view_id, {"state": "skipped", "message": "Skipped in this run."})
            else:
                on_progress(name, 0, len(selected), None)
                for index, view in enumerate(selected):
                    if should_skip(name):
                        for remaining in selected[index:]:
                            on_item(name, remaining.view_id, {"state": "skipped", "message": "Skipped in this run."})
                        break
                    on_progress(name, index, len(selected), view.label)
                    on_item(name, view.view_id, {"state": "running", "message": "Checking this item."})
                    part = run(view.view_id)
                    on_item(name, view.view_id, check_result(name, store.observations(view.view_id), part))
                    for key, value in part.items():
                        summary[key] = summary.get(key, 0) + value
                    on_progress(name, index + 1, len(selected), None)
            skipped = should_skip(name)
            if skipped:
                summary["skipped"] = True
            results[name] = summary
            on_complete(name, skipped)

        for name, run, kind in (
            ("image", run_perceive, ViewKind.IMAGE),
            ("page", run_perceive_pages, ViewKind.PAGE),
            ("video", run_perceive_videos, ViewKind.VIDEO),
            ("expectations", run_expectations, ViewKind.PAGE),
            ("techniques", run_techniques, None),
        ):
            perform(name, lambda view_id, run=run: run(ctx, [view_id], settings=settings),
                    [v for v in source if kind is None or v.kind is kind])
        perform("warnings", lambda: run_warning_lists(store, settings.data_dir / "warning_lists"), batch_items=source)
        # Pattern discovery needs current independent campaigns. Refresh once before it,
        # then again after matching so new pattern scores reach the Library.
        previous = get_embedder()
        embedder = GatewayEmbedder(settings.embedding_model, settings=settings)
        set_embedder(embedder)
        try:
            def rebuild():
                pipeline.run_fingerprint(store)
                pipeline.run_cloaking(store)
                pipeline.run_compare_all(store)
                groups = run_method(store)
                return {"campaigns": len(groups.campaigns), "families": len(groups.families)}

            perform("rebuild", rebuild)
            perform("patterns", lambda: run_discover(ctx, settings=settings, on_match=pattern_item), batch_items=source)
            perform("finalize", rebuild)
            results["embeddings"] = {
                "model": embedder.model, "texts": embedder.texts,
                "chunks": embedder.chunks, "failures": dict(embedder.failures),
            }
        finally:
            set_embedder(previous)


def result_issues(results: dict[str, Any]) -> list[str]:
    """Completion is separate from coverage: surface unavailable checks and kept old output."""
    issues = []
    for stage, summary in results.items():
        if not isinstance(summary, dict):
            continue
        if summary.get("skipped"):
            issues.append(f"{stage}: skipped; coverage is incomplete")
        if summary.get("reason"):
            issues.append(f"{stage}: {summary['reason']}")
        for field in ("not_observable", "kept"):
            if summary.get(field):
                issues.append(f"{stage}: {summary[field]} {field.replace('_', ' ')}")
        if summary.get("failures"):
            issues.append(f"{stage}: {summary['failures']}")
        if summary.get("errors"):
            issues.append(f"{stage}: {summary['errors']}")
        if isinstance(summary.get("matching"), dict):
            issues.extend(result_issues({"pattern matching": summary["matching"]}))
    return issues
