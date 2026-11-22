"""Run one capture in its own process: `python -m ftf.capture.worker`.

The parent pickles the `capture_url` keyword arguments to stdin and reads a pickled
("ok", CaptureResult) or ("error", (kind, message, detail)) from stdout. A process, unlike a
thread, can be killed with its browser when a capture hangs, and it never meets the event
loop of the caller's thread, which Playwright's sync API refuses.
"""

from __future__ import annotations

import pickle
import sys


def portable_error(exc: BaseException) -> tuple[str, str, tuple[int, str] | None]:
    kind = next((k for k in (ImportError, ValueError, OSError) if isinstance(exc, k)), Exception)
    detail = (exc.errno, exc.strerror) if isinstance(exc, OSError) and exc.errno else None
    return kind.__name__, str(exc), detail


def rebuild_error(kind: str, message: str, detail: tuple[int, str] | None) -> Exception:
    if kind == "OSError" and detail:
        return OSError(*detail)
    errors = {"ImportError": ImportError, "ValueError": ValueError, "OSError": OSError}
    return errors.get(kind, RuntimeError)(message)


def main() -> None:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing printed may land in the pickled reply
    kwargs = pickle.load(sys.stdin.buffer)
    try:
        from . import capture_url

        reply = ("ok", capture_url(**kwargs))
    except BaseException as exc:  # noqa: BLE001 — every failure goes back to the parent
        reply = ("error", portable_error(exc))
    pickle.dump(reply, out)
    out.flush()


if __name__ == "__main__":
    main()
