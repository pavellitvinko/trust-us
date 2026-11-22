"""Per-item check coverage, from stored observations and the latest processing attempt."""

import hashlib
import json

from .contracts import State, View, ViewKind

CHECK_EXTRACTORS = {
    "image": "perception.image", "page": "perception.page", "video": "perception.video",
    "expectations": "perception.expectations", "techniques": "perception.technique",
    "warnings": "enrich.warning_lists", "patterns": "perception.pattern",
}
STATE_LABELS = {
    "not_run": "Not checked", "queued": "Waiting", "running": "Processing",
    "complete": "Complete", "partial": "Partly checked", "unavailable": "Unavailable",
    "retained": "Earlier result kept", "skipped": "Skipped", "failed": "Failed",
}
REASONS = {
    "no_key": "The provider key is missing.", "error": "The check could not finish.",
    "timeout": "The provider request timed out.", "schema": "The model response could not be validated.",
    "refusal": "The provider declined this check.", "no_file": "The source file is missing.",
    "no_text": "No readable text is available.", "no_content": "No readable content is available.",
    "no_kind": "The page type has not been identified.", "unsupported": "This source format is unsupported.",
    "no_patterns": "No active patterns are available to match.", "not_judged": "The model did not judge this pattern.",
    "quote_not_in_text": "The quoted evidence could not be verified in the source.",
}


def applicable_checks(view: View) -> list[dict]:
    from .llm_checks import new_steps

    kinds = {"image": ViewKind.IMAGE, "page": ViewKind.PAGE, "video": ViewKind.VIDEO,
             "expectations": ViewKind.PAGE}
    return [s for s in new_steps() if s["id"] in CHECK_EXTRACTORS
            and (s["id"] not in kinds or view.kind == kinds[s["id"]])]


def check_result(check: str, observations: list, summary: dict | None = None) -> dict:
    rows = [o for o in observations if o.extractor == CHECK_EXTRACTORS[check]]
    observable = sum(o.state != State.NOT_OBSERVABLE for o in rows)
    unavailable = len(rows) - observable
    # A successful read with zero findings is still a completed check.
    if summary and summary.get("kept"):
        state, message = "retained", "The latest attempt did not finish; earlier results were kept."
    elif unavailable:
        state = "partial" if observable else "unavailable"
        reasons = list(dict.fromkeys(REASONS.get(o.raw or "", "Some source evidence could not be checked.")
                                    for o in rows if o.state == State.NOT_OBSERVABLE))
        message = " ".join(reasons[:3])
    elif observable or (summary and not summary.get("not_observable") and not summary.get("reason")):
        state, message = "complete", "Check completed."
    elif summary and (summary.get("not_observable") or summary.get("reason")):
        state, message = "unavailable", "This check could not be completed for this item."
    else:
        state, message = "not_run", "This check has not been recorded."
    return {"state": state, "message": message, "hasResult": bool(observable or state == "complete"),
            "observations": len(rows), "unavailable": unavailable,
            "outputSignature": hashlib.sha256(json.dumps(
                sorted(o.model_dump_json() for o in rows), ensure_ascii=False,
            ).encode()).hexdigest()}


def item_processing(view: View, observations: list, tracked: dict | None = None) -> dict:
    checks = []
    for spec in applicable_checks(view):
        key = spec["id"]
        stored = check_result(key, observations)
        attempt = (tracked or {}).get(key, {})
        if (attempt.get("state") in ("complete", "partial", "unavailable", "retained")
                and attempt.get("outputSignature")
                and attempt["outputSignature"] != stored["outputSignature"]):
            attempt = {}  # output changed through another workflow, such as the CLI
        state = attempt.get("state", stored["state"])
        has_result = stored["hasResult"] or attempt.get("hasResult", False)
        message = attempt.get("message", stored["message"])
        if view.meta.get("shipped") and state == "not_run":
            state, message = "unavailable", "This item contains processed results without source material to check."
        if state == "skipped" and has_result:
            message = "Skipped in the latest run. Earlier stored results remain available."
        elif state in ("queued", "running") and has_result:
            message += " Earlier results remain available while this check runs."
        checks.append({"id": key, "label": spec["label"], "state": state,
                       "status": STATE_LABELS[state], "message": message, "hasResult": has_result,
                       "updatedAt": attempt.get("updatedAt"), "runId": attempt.get("runId"),
                       "observations": stored["observations"]})
    states = [c["state"] for c in checks]
    checked = sum(s == "complete" for s in states)
    if "running" in states:
        state = "running"
    elif "queued" in states:
        state = "queued"
    elif "failed" in states:
        state = "failed"
    elif all(s == "complete" for s in states):
        state = "complete"
    elif all(s == "not_run" for s in states):
        state = "not_run"
    elif all(s == "skipped" for s in states):
        state = "skipped"
    elif all(s in ("unavailable", "not_run") for s in states) and "unavailable" in states:
        state = "unavailable"
    else:
        state = "partial"
    current = next((c["label"] for c in checks if c["state"] == "running"), None)
    return {"state": state, "status": STATE_LABELS[state], "complete": checked, "total": len(checks),
            "current": current, "checks": checks}
