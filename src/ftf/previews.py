"""A picture for every item: an image itself, a capture's own screenshot, or a saved page rendered.

Saved pages are rendered offline in Chromium with JavaScript off. A page may load only files
inside its own case folder, so a hostile page can neither reach the network nor show other
local files in its picture. Renders are kept under ``<data dir>/previews``; a page that cannot
be rendered leaves a marker so it is not retried on every visit (a missing browser does not:
those pages are rendered once Chromium is installed).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .contracts import View, ViewKind

log = logging.getLogger(__name__)

PREVIEW_DIR = "previews"
VIEWPORT = {"width": 1280, "height": 900}
_PAGE_SUFFIXES = (".html", ".htm")
_LOCK = threading.Lock()
_WARNED = False


def own_picture(view: View, root: Path) -> Path | None:
    """The picture the material already has: the image itself or a capture's screenshot."""
    name = None
    if view.kind is ViewKind.IMAGE:
        name = view.primary
    elif view.kind is ViewKind.PAGE:
        name = next((a for a in view.attachments if a.endswith("screenshot.png")), None)
    path = root / name if name else None
    return path if path and path.is_file() else None


def saved_page(view: View, root: Path) -> Path | None:
    if view.kind is not ViewKind.PAGE or not view.primary:
        return None
    path = root / view.primary
    return path if path.suffix.lower() in _PAGE_SUFFIXES and path.is_file() else None


def rendered_path(data_dir: Path, view: View, page: Path) -> Path:
    """Where a saved page's render lives; a changed file gets a new name."""
    stat = page.stat()
    key = f"{view.view_id}|{page}|{stat.st_size}|{stat.st_mtime_ns}"
    return data_dir / PREVIEW_DIR / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".png")


def picture(view: View, root: Path, data_dir: Path) -> Path | None:
    """The best picture of an item that exists now, without rendering anything."""
    if found := own_picture(view, root):
        return found
    page = saved_page(view, root)
    if page:
        path = rendered_path(data_dir, view, page)
        if path.is_file():
            return path
    return None


def _render(jobs: list[tuple[Path, Path, Path]]) -> int:
    """Render (page, case folder, target) triples in one browser; returns how many succeeded."""
    from playwright.sync_api import sync_playwright

    done = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for page_file, root, target in jobs:
                allowed = root.resolve().as_uri().rstrip("/") + "/"
                context = browser.new_context(viewport=VIEWPORT, java_script_enabled=False)

                def only_own_folder(route, request, prefix=allowed):
                    if request.url.startswith(prefix):
                        route.continue_()
                    else:
                        route.abort()

                try:
                    context.route("**/*", only_own_folder)
                    page = context.new_page()
                    page.goto(page_file.resolve().as_uri(), wait_until="load", timeout=15000)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(".tmp.png")
                    page.screenshot(path=str(tmp))
                    tmp.replace(target)
                    done += 1
                except Exception as exc:  # noqa: BLE001 - one bad page must not stop the rest
                    log.warning("no preview for %s: %s", page_file.name, exc)
                    target.with_suffix(".failed").touch()
                finally:
                    context.close()
        finally:
            browser.close()
    return done


def render_missing(store, data_dir: Path) -> int:
    """Render every saved page that has no picture yet. Safe to call often: it skips the rest."""
    roots = {i.incident_id: Path(i.root) for i in store.incidents()}
    jobs = []
    for view in store.views():
        root = roots.get(view.incident_id)
        if root is None or own_picture(view, root):
            continue
        page = saved_page(view, root)
        if not page:
            continue
        target = rendered_path(data_dir, view, page)
        if not target.is_file() and not target.with_suffix(".failed").exists():
            jobs.append((page, root, target))
    if not jobs:
        return 0
    with _LOCK:
        try:  # Playwright's sync API wants a thread without an event loop
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_render, jobs).result()
        except Exception as exc:  # noqa: BLE001 - no browser installed: items keep their icon
            # Not remembered per page: once Chromium is installed, the next visit renders them.
            global _WARNED
            if not _WARNED:
                log.warning("previews unavailable (%s); run `playwright install chromium`", exc)
                _WARNED = True
            return 0
