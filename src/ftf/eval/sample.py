"""Validation on real incident files (HAC-54): baseline, degradation, ablation, leave-one-out.

Everything here starts from files on disk and a fresh in-memory store, never from the
hand-made fixtures (`ablation.py` and `loio.py` do that and stay credited separately).
All runs are model-off: no gateway call is made, and the vector layers use the local
hashed-word embedder. Paths that need a model reading (text in screenshots, video
transcripts, patterns, expected elements) are reported as unavailable, not as failures.

    base = extracted(sample_dir)          # ingest + extract once
    run = run_sample("baseline", base)    # fingerprint, fuse, group, evaluate, checkpoint
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import pipeline
from ..compare.weights import weights
from ..contracts import Observation, Relation, RelationType, Tier, ViewKind
from ..fusion import DEFAULT, FusionConfig, run_method
from ..fusion.fuse import summarise
from ..grouping.assign import Assignment, assign_incident
from ..grouping.levels import Groups
from ..layers._embedder import FakeEmbedder, get_embedder, set_embedder
from ..registry import layers, load_plugins
from ..store import Store
from .ablation import MODEL_LAYERS, disabled_layers
from .harness import Report, evaluate, format_report
from .reference import resolve_view

MODE = (
    "model-off: no gateway call; vector layers use the local hashed-word embedder; "
    "no screenshot, video, pattern or expected-element reading"
)

LINKED = (
    RelationType.DUPLICATE,
    RelationType.SAME_CAMPAIGN,
    RelationType.SAME_FAMILY,
    RelationType.SAME_PLAYBOOK,
)

# Pairs that must stay unlinked or uncertain, and inputs with missing data (HAC-54).
REVIEW_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("61:business", "61:article", "decoy page on the article's domain"),
    ("61:business", "61:chat", "decoy page on the chat's domain"),
    ("3:page", "14:page", "unproven tracker-software similarity"),
    ("3:page", "14:capture", "unproven tracker-software similarity"),
    ("36:page", "47:page", "unproven lander-path similarity"),
    ("2:page", "2:healthlinkq", "one folder, two campaigns"),
    ("3:ad", "3:page", "screenshot beside its page, no model reading"),
    ("5:video", "1:page", "video-only folder"),
    ("47:video", "47:page", "screen recording beside its page"),
)


# -- stores ----------------------------------------------------------------------------------


@contextmanager
def model_off() -> Iterator[None]:
    """Force the local embedder for the duration, whatever keys the environment holds."""
    previous = get_embedder()
    set_embedder(FakeEmbedder())
    try:
        yield
    finally:
        set_embedder(previous)


def extracted(sample_dir: Path) -> Store:
    """Ingest and extract a folder of incident folders into a fresh in-memory store."""
    from ..ingest import ingest_path

    store = Store(":memory:")
    ingest_path(Path(sample_dir), store)
    pipeline.run_extract(store)
    return store


def clone(
    base: Store,
    exclude_incidents: Iterable[str] = (),
    only_incidents: Iterable[str] | None = None,
    into: Store | None = None,
) -> Store:
    """Copy incidents, views and observations (nothing derived) into a fresh store."""
    exclude = set(exclude_incidents)
    only = None if only_incidents is None else set(only_incidents)
    store = into or Store(":memory:")
    for incident in base.incidents():
        iid = incident.incident_id
        if iid in exclude or (only is not None and iid not in only):
            continue
        views = base.views(iid)
        store.put_incident(incident, base.artifacts(iid), views)
        for view in views:
            by_extractor: dict[str, list[Observation]] = defaultdict(list)
            for o in base.observations(view.view_id):
                by_extractor[o.extractor].append(o)
            for extractor, items in by_extractor.items():
                store.put_observations(view.view_id, extractor, items)
    return store


# -- the run record ----------------------------------------------------------------------------


def git_commit() -> str:
    """Short commit of the code that ran, with `+changes` when the tree is dirty."""
    root = Path(__file__).resolve().parents[3]
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha + ("+changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass
class Manifest:
    """What ran on what: the header every table in the results file is read against."""

    commit: str
    run_date: str
    corpus: str
    incidents: int
    views: dict[str, int]
    views_without_evidence: int
    observations: int
    layers: int
    mode: str
    config: dict

    def lines(self) -> list[str]:
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(self.views.items()))
        return [
            f"- Commit: `{self.commit}`",
            f"- Date: {self.run_date}",
            (
                f"- Corpus: `{self.corpus}`: {self.incidents} incident folders, "
                f"{sum(self.views.values())} views ({kinds}), {self.observations} observations; "
                f"{self.views_without_evidence} views carry no fingerprint at all"
            ),
            f"- Execution mode: {self.mode}",
            f"- Layers registered: {self.layers}",
            "- Store: fresh in-memory store per run",
            "- Configuration: `FusionConfig` defaults: thresholds "
            + ", ".join(
                f"{k} {self.config[k]}"
                for k in ("duplicate_text", "identity", "artifact", "kit", "playbook")
            )
            + f"; minimum coverage {self.config['min_coverage']}; "
            f"solo identity groups {list(self.config['identity_solo_groups'])}",
        ]


def manifest(base: Store, sample_dir: Path, fingerprinted: Store, config: FusionConfig) -> Manifest:
    views = base.views()
    with_fp = {f.view_id for f in fingerprinted.fingerprints()}
    return Manifest(
        commit=git_commit(),
        run_date=date.today().isoformat(),
        corpus=Path(sample_dir).name,
        incidents=len(base.incidents()),
        views=dict(Counter(v.kind.value for v in views)),
        views_without_evidence=sum(v.view_id not in with_fp for v in views),
        observations=sum(len(base.observations(v.view_id)) for v in views),
        layers=len(layers.all()),
        mode=MODE,
        config=config.to_dict(),
    )


# -- one run -----------------------------------------------------------------------------------


def view_labels(store: Store) -> dict[str, str]:
    """`61 · glowpixel.org (strona_3)`: the stored label, plus the sub-folder when the
    primary file sits in one, since several pages of one folder can share a label."""
    out = {}
    for v in store.views():
        parts = Path(v.primary).parts if v.primary else ()
        out[v.view_id] = v.label + (f" ({parts[0]})" if len(parts) > 1 else "")
    return out


def _relation(store: Store, a: str, b: str) -> Relation | None:
    va, vb = resolve_view(store, a), resolve_view(store, b)
    if va is None or vb is None:
        return None
    key = tuple(sorted((va, vb)))
    return next((r for r in store.relations() if (r.view_a, r.view_b) == key), None)


def checkpoint(store: Store, groups: Groups) -> dict[str, bool | None]:
    """The deterministic checkpoint outcomes plus the case-61 regression. None means the
    input needed for the check is not in this corpus (a degraded run), not that it failed."""
    rel = {(r.view_a, r.view_b): r.type for r in store.relations()}

    def view(alias: str) -> str | None:
        return resolve_view(store, alias)

    def typed(a: str, b: str, expected: RelationType) -> bool | None:
        va, vb = view(a), view(b)
        if va is None or vb is None:
            return None
        return rel.get(tuple(sorted((va, vb)))) is expected

    def same_campaign(a: str | None, b: str | None) -> bool | None:
        if a is None or b is None:
            return None
        return groups.campaign_of.get(a) == groups.campaign_of.get(b)

    article, chat, business = view("61:article"), view("61:chat"), view("61:business")
    v48 = view("48:page")
    family = None
    if article and v48:
        family = (
            typed("48:page", "61:article", RelationType.SAME_FAMILY)
            and not same_campaign(v48, article)
            and groups.family_of_view(v48) == groups.family_of_view(article)
        )
    funnel = None
    if article and chat:
        edges = groups.funnel_edges.get("61", ([], []))[0]
        funnel = {(e.source, e.target) for e in edges} == {(article, chat)}
    folder2 = None
    pages2 = [v for v in store.views("2") if v.kind is ViewKind.PAGE]
    if len(pages2) >= 2:
        folder2 = len({groups.campaign_of[v.view_id] for v in pages2}) == 2
    decoy = None
    if business and (article or chat):
        decoy = not any(same_campaign(business, x) for x in (article, chat) if x)
    return {
        "1↔2 same campaign": typed("1:page", "2:page", RelationType.SAME_CAMPAIGN),
        "2 healthlinkq↔3 duplicate": typed("2:healthlinkq", "3:page", RelationType.DUPLICATE),
        "48↔61 same family, two campaigns": family,
        "61 article↔chat same campaign": typed("61:article", "61:chat", RelationType.SAME_CAMPAIGN),
        "61 article→chat funnel edge": funnel,
        "folder 2 in two campaigns": folder2,
        "61 business page outside the article campaign": decoy,
    }


@dataclass
class SampleRun:
    name: str
    report: Report
    checks: dict[str, bool | None]
    layers_on: int
    views: int
    note: str = ""
    types: Counter = field(default_factory=Counter)

    @property
    def checks_text(self) -> str:
        ok = sum(v is True for v in self.checks.values())
        na = sum(v is None for v in self.checks.values())
        text = f"{ok}/{len(self.checks) - na}"
        return text + (f" ({na} n/a)" if na else "")

    @property
    def failed_checks(self) -> list[str]:
        return [k for k, v in self.checks.items() if v is False]

    def flipped_from(self, base: SampleRun) -> list[str]:
        mine = {(r.ref.a, r.ref.b): r.predicted for r in self.report.results}
        theirs = {(r.ref.a, r.ref.b): r.predicted for r in base.report.results}

        def name(t: RelationType | None) -> str:
            return t.value if t else "not evaluated"

        return [
            f"{a}↔{b}: {name(theirs.get((a, b)))} → {name(mine[(a, b)])}"
            for (a, b) in mine
            if mine[(a, b)] != theirs.get((a, b))
        ]


def method_store(
    base: Store, config: FusionConfig = DEFAULT, off: Iterable[str] = ()
) -> tuple[Store, Groups]:
    """A fresh store with fingerprints, relations and groups. The caller closes it and
    resets the weight table."""
    store = clone(base)
    with disabled_layers(off):
        pipeline.run_fingerprint(store)
        groups = run_method(store, config)
    return store, groups


def run_sample(
    name: str,
    base: Store,
    config: FusionConfig = DEFAULT,
    off: Iterable[str] = (),
    note: str = "",
) -> SampleRun:
    load_plugins()
    off = list(off)
    with model_off(), disabled_layers(off):
        store, groups = method_store(base, config)
        run = SampleRun(
            name=name,
            report=evaluate(store),
            checks=checkpoint(store, groups),
            layers_on=len(layers.all()),
            views=len(store.views()),
            note=note,
            types=Counter(r.type.value for r in store.relations()),
        )
        store.close()
    weights.reset()
    return run


# -- confidence and coverage against the supporting groups -----------------------------------


@dataclass
class Support:
    view_a: str
    view_b: str
    type: RelationType
    confidence: str
    coverage: float
    comparable: int
    total: int
    groups: dict[str, list[str]]  # tier -> groups scoring >= 0.5

    @property
    def independent(self) -> int:
        return sum(len(g) for g in self.groups.values())


def support(store: Store, relation: Relation, config: FusionConfig = DEFAULT) -> Support:
    """Recompute which independent groups carry a stored relation. Call while the weight
    table fitted for `store` is still in place (straight after `run_method`)."""
    f = summarise(pipeline.compare_views(store, relation.view_a, relation.view_b), config)
    return Support(
        view_a=relation.view_a,
        view_b=relation.view_b,
        type=relation.type,
        confidence=relation.confidence,
        coverage=relation.coverage,
        comparable=f.comparable_groups,
        total=f.total_groups,
        groups={
            t.value: f.tiers[t].agreeing
            for t in (Tier.IDENTITY, Tier.ARTIFACT, Tier.KIT, Tier.PLAYBOOK)
            if f.tiers[t].agreeing
        },
    )


def linked_support(store: Store, config: FusionConfig = DEFAULT) -> list[Support]:
    return [support(store, r, config) for r in store.relations() if r.type in LINKED]


def confidence_problems(store: Store, config: FusionConfig = DEFAULT) -> list[str]:
    """Rule violations: a confidence label the evidence does not carry. Empty when sound."""
    problems = []
    with_fp = {f.view_id for f in store.fingerprints()}
    for r in store.relations():
        pair = f"{r.view_a} ~ {r.view_b}"
        if (r.view_a not in with_fp or r.view_b not in with_fp) and (
            r.type is not RelationType.INSUFFICIENT or r.confidence
        ):
            problems.append(f"{pair}: {r.type.value} although one side has no evidence at all")
        if r.type is RelationType.INSUFFICIENT and r.confidence:
            problems.append(f"{pair}: insufficient evidence carries a confidence label")
        if (
            r.type is RelationType.UNRELATED
            and r.confidence == "high"
            and r.coverage < config.unrelated_high_coverage
        ):
            problems.append(f"{pair}: unrelated/high at coverage {r.coverage:.2f}")
        if r.type in LINKED and r.confidence == "high":
            s = support(store, r, config)
            anchored = r.type is RelationType.DUPLICATE
            if s.independent < 2 and not anchored:
                problems.append(f"{pair}: {r.type.value}/high on {s.independent} group(s)")
        if r.type in (RelationType.DUPLICATE, RelationType.SAME_CAMPAIGN):
            s = support(store, r, config)
            solo = set(s.groups.get(Tier.IDENTITY.value, [])) <= set(config.identity_solo_groups)
            if r.type is RelationType.SAME_CAMPAIGN and solo and s.independent < 2:
                problems.append(f"{pair}: campaign link on {s.groups or 'no group'} alone")
    return problems


# -- degradation on real files ----------------------------------------------------------------

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_SUFFIXES = {".har", ".html", ".htm", ".js", ".css", ".json", ".txt", ".pobrane"}

Rewrite = Callable[[Path, Path], bool]  # (source file, destination) -> True when it wrote one


def _place(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)  # originals are never opened for writing
    except OSError:
        shutil.copy2(src, dst)


def derive_tree(
    src: Path,
    dst: Path,
    keep: Callable[[Path], bool] = lambda rel: True,
    rewrite: Rewrite | None = None,
    rename: Callable[[Path], Path] = lambda rel: rel,
) -> int:
    """Build a damaged copy of a sample folder. Returns the number of files placed."""
    count = 0
    for path in sorted(Path(src).rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        rel = path.relative_to(src)
        if not keep(rel):
            continue
        target = dst / rename(rel)
        if rewrite is None or not rewrite(path, target):
            _place(path, target)
        count += 1
    return count


def reencode_bytes(data: bytes, fmt: str, hue_shift: int = 12) -> bytes | None:
    """A hue-shifted, re-encoded copy of an image: other bytes, nearly the same picture.
    `fmt` is a Pillow format name. None when Pillow cannot read the bytes or the image
    is animated (then the caller keeps the original)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, "is_animated", False):
                return None
            h, s, v = image.convert("RGB").convert("HSV").split()
            shifted = Image.merge("HSV", (h.point(lambda x: (x + hue_shift) % 256), s, v))
            out = io.BytesIO()
            options = {} if fmt == "PNG" else {"quality": 82}
            shifted.convert("RGB").save(out, format=fmt, **options)
            return out.getvalue()
    except (OSError, ValueError, SyntaxError):
        return None


def _image_format(suffix: str) -> str:
    return {".png": "PNG", ".webp": "WEBP"}.get(suffix.lower(), "JPEG")


def reencode_image(src: Path, dst: Path, hue_shift: int = 12) -> bool:
    """Write a hue-shifted, re-encoded copy of an image file, or of every image body a HAR
    carries. The asset extractor hashes HAR response bodies too, so a scenario that rewrites
    only the image files would leave the original hashes reachable through the HAR."""
    suffix = src.suffix.lower()
    if suffix == ".har":
        return reencode_har_images(src, dst, hue_shift)
    if suffix not in IMAGE_SUFFIXES:
        return False
    data = reencode_bytes(src.read_bytes(), _image_format(suffix), hue_shift)
    if data is None:  # not an image Pillow can read: keep the bytes
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return True


HAR_IMAGE_FORMATS = {
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
}


def reencode_har_images(src: Path, dst: Path, hue_shift: int = 12) -> bool:
    """Rewrite every base64 image body in a HAR the same way the image files are rewritten.
    A body Pillow cannot decode (SVG, GIF, a truncated capture) is dropped rather than kept:
    the point of the scenario is that no original image bytes survive anywhere."""
    try:
        with open(src, encoding="utf-8", errors="replace") as fh:
            har = json.load(fh)
        entries = har["log"]["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    changed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = (entry.get("response") or {}).get("content")
        if not isinstance(content, dict):
            continue
        mime = str(content.get("mimeType") or "").lower().split(";")[0].strip()
        text = content.get("text")
        if not mime.startswith("image/") or not isinstance(text, str) or not text:
            continue
        data = None
        if content.get("encoding") == "base64":
            try:
                raw = base64.b64decode(text)
            except ValueError:
                raw = b""
            data = reencode_bytes(raw, HAR_IMAGE_FORMATS.get(mime, "PNG"), hue_shift)
        if data is None:
            content.pop("text", None)
            content.pop("encoding", None)
        else:
            content["text"] = base64.b64encode(data).decode("ascii")
            content["encoding"] = "base64"
        content["size"] = len(data or b"")
        changed = True
    if not changed:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(har, fh, ensure_ascii=False)
    return True


def swap_text(mapping: dict[str, str]) -> Rewrite:
    pairs = [(old.encode(), new.encode()) for old, new in mapping.items()]

    def rewrite(src: Path, dst: Path) -> bool:
        if src.suffix.lower() not in TEXT_SUFFIXES:
            return False
        data = src.read_bytes()
        changed = data
        for old, new in pairs:
            changed = changed.replace(old, new)
        if changed == data:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(changed)
        return True

    return rewrite


def _in_incident(rel: Path, incidents: Iterable[str]) -> bool:
    return rel.parts[0] in set(incidents)


def incident_domains(base: Store, incident_id: str) -> list[str]:
    return sorted(
        {
            str(o.value)
            for v in base.views(incident_id)
            for o in base.observations(v.view_id)
            if o.namespace == "domain.registrable"
        }
    )


@dataclass
class Scenario:
    name: str
    note: str
    build: Callable[[Path, Path], int]


def scenarios(base: Store, reencode: tuple[str, ...] = ("2", "61"), swap: str = "2"):
    """The four file-level damages of HAC-54. `reencode` and `swap` name incident folders."""
    mapping = {d: f"swapped-{d}" for d in incident_domains(base, swap)}

    def rename(rel: Path) -> Path:
        name = rel.as_posix()
        if _in_incident(rel, [swap]):
            for old, new in mapping.items():
                name = name.replace(old, new)
        return Path(name)

    def swap_rewrite(src: Path, dst: Path) -> bool:
        return swap_text(mapping)(src, dst)

    return [
        Scenario(
            "HAR files removed",
            "requests, responses and HAR-only pages are gone; saved pages remain",
            lambda src, dst: derive_tree(src, dst, keep=lambda rel: rel.suffix.lower() != ".har"),
        ),
        Scenario(
            f"folder {swap} on swapped domains",
            "every occurrence of " + ", ".join(mapping) + " rewritten in HAR, HTML and file names",
            lambda src, dst: derive_tree(
                src,
                dst,
                rewrite=lambda s, d: (
                    _in_incident(s.relative_to(src), [swap]) and swap_rewrite(s, d)
                ),
                rename=rename,
            ),
        ),
        Scenario(
            f"images re-encoded and hue-shifted (folders {', '.join(reencode)})",
            "every image file and every image body inside a HAR rewritten with Pillow: exact "
            "hashes change everywhere they could be read from, the picture barely",
            lambda src, dst: derive_tree(
                src,
                dst,
                rewrite=lambda s, d: (
                    _in_incident(s.relative_to(src), reencode) and reencode_image(s, d)
                ),
            ),
        ),
        Scenario(
            "screenshots only",
            "only the image files at the top of each folder; no page, HAR or video. Reading "
            "what a screenshot shows needs the vision model: unavailable in this mode",
            lambda src, dst: derive_tree(
                src,
                dst,
                keep=lambda rel: len(rel.parts) == 2 and rel.suffix.lower() in IMAGE_SUFFIXES,
            ),
        ),
    ]


def degrade_sample(
    sample_dir: Path,
    base: Store,
    config: FusionConfig = DEFAULT,
    workdir: Path | None = None,
    only: Iterable[str] | None = None,
) -> list[SampleRun]:
    """Re-run the whole method on damaged copies of the files. The first row is the
    undamaged baseline. `workdir` should sit on the sample's filesystem so that unchanged
    files are hard-linked, not copied."""
    runs = [run_sample("full input", base, config)]
    if workdir is not None:
        Path(workdir).mkdir(parents=True, exist_ok=True)
    for scenario in scenarios(base):
        if only is not None and scenario.name not in set(only):
            continue
        with tempfile.TemporaryDirectory(dir=workdir, prefix="ftf-degrade-") as tmp:
            damaged = Path(tmp) / "sample"
            scenario.build(Path(sample_dir), damaged)
            store = extracted(damaged)
            runs.append(run_sample(scenario.name, store, config, note=scenario.note))
            store.close()
    return runs


# -- layer ablation --------------------------------------------------------------------------


def ablate_sample(base: Store, config: FusionConfig = DEFAULT) -> list[SampleRun]:
    """One layer off per run. Only layers that built a fingerprint on this corpus are
    ablated: switching off a layer nothing feeds cannot change anything."""
    load_plugins()
    with model_off():
        probe = clone(base)
        pipeline.run_fingerprint(probe)
        built = sorted({f.layer_id for f in probe.fingerprints()})
        probe.close()
    runs = [run_sample("all layers", base, config)]
    runs += [run_sample(f"without {name}", base, config, off=[name]) for name in built]
    return runs


def unbuilt_layers(base: Store) -> list[str]:
    load_plugins()
    with model_off():
        probe = clone(base)
        pipeline.run_fingerprint(probe)
        built = {f.layer_id for f in probe.fingerprints()}
        probe.close()
    return sorted(set(layers.names()) - built)


# -- leave-one-incident-out ------------------------------------------------------------------


@dataclass
class SampleHeldOut:
    incident_id: str
    headline: Assignment | None
    per_view: list[Assignment]
    labels: dict[str, str]


def loio_sample(
    base: Store, config: FusionConfig = DEFAULT, incidents: Iterable[str] | None = None
) -> list[SampleHeldOut]:
    """Hold one folder out, rebuild, then assign it.

    The corpus is rebuilt without the held-out folder: rarity weights, the learned stoplist,
    demotions, vector calibration, coverage denominators and the groups are all fitted
    without it and stay frozen while it is assigned, so its own evidence cannot shape the
    table it is judged with. (In live use a new item does count toward rarity, so this is
    slightly stricter than the running system.)"""
    load_plugins()
    wanted = list(incidents) if incidents else [i.incident_id for i in base.incidents()]
    out = []
    for iid in sorted(wanted, key=lambda x: (len(x), x)):
        with model_off():
            store = clone(base, exclude_incidents=[iid])
            pipeline.run_fingerprint(store)
            groups = run_method(store, config)
            clone(base, only_incidents=[iid], into=store)
            new_views = [v.view_id for v in store.views(iid)]
            pipeline.run_fingerprint(store, view_ids=new_views)
            headline, per_view = assign_incident(store, iid, groups, config, prepared=True)
            labels = view_labels(store)
            store.close()
        weights.reset()
        out.append(SampleHeldOut(iid, headline, per_view, labels))
    return out


# -- candidate generation against all pairs ---------------------------------------------------


@dataclass
class IndexVariant:
    """What one choice of index keys would propose, measured against the all-pairs result."""

    name: str
    pairs: int
    page_pairs: int
    linked_found: int
    linked_total: int
    scored_missed: list[tuple[str, str, float]]  # pairs with a non-zero tier score not proposed


@dataclass
class CandidateParity:
    views: int
    all_pairs: int
    compared: int
    by_kind: dict[str, tuple[int, int]]  # "page-page" -> (compared, all)
    by_key: list[tuple[str, int]]  # index key family -> pairs it proposes
    differences: list[str]  # empty when the two runs agree
    reasons_differ: int  # skipped pairs whose reasons differ beyond the marker
    groups_equal: bool
    variants: list[IndexVariant]
    skipped_types: dict[str, int]  # relation type (all-pairs run) -> skipped pairs of that type

    @property
    def holds(self) -> bool:
        return not self.differences and self.groups_equal and not self.reasons_differ


def _kind_pair(kinds: dict[str, str], a: str, b: str) -> str:
    return "-".join(sorted((kinds[a], kinds[b])))


def candidate_parity(base: Store, config: FusionConfig = DEFAULT) -> CandidateParity:
    """Run the method twice on fresh stores, all pairs and candidates only, and compare
    every relation and every group. Then measure what the index proposes with and without
    the catch-all buckets of the perceptual and vector layers."""
    from itertools import combinations

    from ..grouping.candidates import NOT_COMPARED, build_index, candidate_pairs

    load_plugins()
    with model_off():
        full = clone(base)
        pipeline.run_fingerprint(full)
        groups_full = run_method(full, config)
        rel_full = {(r.view_a, r.view_b): r for r in full.relations()}
        weights.reset()

        cand = clone(base)
        pipeline.run_fingerprint(cand)
        groups_cand = run_method(cand, config, candidates=True)
        rel_cand = {(r.view_a, r.view_b): r for r in cand.relations()}
        proposed = candidate_pairs(cand)
        index = build_index(cand)
        kinds = {v.view_id: v.kind.value for v in cand.views()}
        labels = view_labels(cand)
        cand.close()
        full.close()
    weights.reset()

    differences = []
    reasons_differ = 0
    for key in sorted(set(rel_full) | set(rel_cand)):
        a, b = rel_full.get(key), rel_cand.get(key)
        name = f"{labels[key[0]]} ~ {labels[key[1]]}"
        if a is None or b is None:
            differences.append(f"{name}: relation missing from one run")
            continue
        if (a.type, a.confidence) != (b.type, b.confidence):
            differences.append(
                f"{name}: {a.type.value}/{a.confidence or '-'} vs {b.type.value}/{b.confidence or '-'}"
            )
        elif abs(a.coverage - b.coverage) > 1e-6:
            differences.append(f"{name}: coverage {a.coverage} vs {b.coverage}")
        elif key in proposed and a.tier_scores != b.tier_scores:
            differences.append(f"{name}: tier scores differ on a compared pair")
        elif key not in proposed and max(a.tier_scores.values()) >= 0.1:
            differences.append(
                f"{name}: skipped although a tier scores {max(a.tier_scores.values()):.2f}"
            )
        if key not in proposed and [*a.reasons, NOT_COMPARED] != b.reasons:
            reasons_differ += 1
        if key in proposed and a.reasons != b.reasons:
            reasons_differ += 1

    all_by_kind: Counter = Counter(
        _kind_pair(kinds, a, b) for a, b in combinations(sorted(kinds), 2)
    )
    got_by_kind: Counter = Counter(_kind_pair(kinds, a, b) for a, b in proposed)
    pairs_by_key: dict[str, set] = defaultdict(set)
    for key, members in index.items():
        family = key[0] + (" (every holder)" if key[1] == "*" else "")
        pairs_by_key[family] |= {tuple(sorted(p)) for p in combinations(sorted(members), 2)}
    by_key = sorted(
        ((family, len(pairs)) for family, pairs in pairs_by_key.items() if pairs),
        key=lambda x: (-x[1], x[0]),
    )

    linked = {k for k, r in rel_full.items() if r.type in LINKED}
    scored = {k: max(r.tier_scores.values()) for k, r in rel_full.items()}
    skipped_types = Counter(r.type.value for k, r in rel_full.items() if k not in proposed)

    def variant(name: str, keep: Callable[[tuple], bool]) -> IndexVariant:
        pairs: set[tuple[str, str]] = set()
        for key, members in index.items():
            if keep(key):
                pairs |= {tuple(sorted(p)) for p in combinations(sorted(members), 2)}
        missed = sorted(
            (
                (labels[a], labels[b], scored[(a, b)])
                for (a, b) in scored
                if scored[(a, b)] > 0 and (a, b) not in pairs
            ),
            key=lambda x: -x[2],
        )
        return IndexVariant(
            name=name,
            pairs=len(pairs),
            page_pairs=sum(1 for a, b in pairs if _kind_pair(kinds, a, b) == "page-page"),
            linked_found=len(linked & pairs),
            linked_total=len(linked),
            scored_missed=missed,
        )

    return CandidateParity(
        views=len(kinds),
        all_pairs=len(rel_full),
        compared=len(proposed),
        by_kind={k: (got_by_kind.get(k, 0), n) for k, n in sorted(all_by_kind.items())},
        by_key=by_key,
        differences=differences,
        reasons_differ=reasons_differ,
        groups_equal=(
            groups_full.campaigns == groups_cand.campaigns
            and groups_full.families == groups_cand.families
            and groups_full.playbooks == groups_cand.playbooks
            and {
                i: [(e.source, e.target) for e in edges[0]]
                for i, edges in groups_full.funnel_edges.items()
            }
            == {
                i: [(e.source, e.target) for e in edges[0]]
                for i, edges in groups_cand.funnel_edges.items()
            }
        ),
        variants=[
            variant("as built: all index keys", lambda key: True),
            variant("identifier, set and MinHash keys only", lambda key: key[1] != "*"),
        ],
        skipped_types=dict(sorted(skipped_types.items())),
    )


def format_candidates(p: CandidateParity) -> list[str]:
    """Every sentence here is chosen from the measurement; the tables carry the numbers."""
    weak = {RelationType.UNRELATED.value, RelationType.INSUFFICIENT.value}
    same_kind = {k: v for k, v in p.by_kind.items() if len(set(k.split("-"))) == 1}
    cross_kind = {k: v for k, v in p.by_kind.items() if k not in same_kind}
    same_total = sum(total for _, total in same_kind.values())
    same_skipped = sum(total - got for got, total in same_kind.values())
    cross_compared = sum(got for got, _ in cross_kind.values())
    page = p.by_kind.get("page-page", (0, 0))
    index_only = next((v for v in p.variants if not v.name.startswith("as built")), None)

    # 1. the verdict, and what it rests on or what breaks it
    verdict = (
        f"Parity with the all-pairs run: **{'holds' if p.holds else 'BROKEN'}**. Both runs store "
        f"{p.all_pairs} relations for {p.views} views. "
    )
    if p.holds:
        verdict += (
            "Relation type, confidence label and coverage are equal for every pair, tier scores "
            "for every compared pair, and campaigns, families, playbooks and funnel edges are "
            "identical. Every skipped pair carries the same reasons as in the all-pairs run plus "
            "one line saying it was not compared in full."
        )
    else:
        broken = []
        if p.differences:
            broken.append(
                f"{len(p.differences)} pairs differ in type, confidence, coverage or tier score "
                "(listed below)"
            )
        if not p.groups_equal:
            broken.append("campaigns, families, playbooks or funnel edges differ")
        if p.reasons_differ:
            broken.append(
                f"{p.reasons_differ} pairs carry different reasons (a skipped pair should carry "
                "the all-pairs reasons plus the not-compared line)"
            )
        verdict += "What differs: " + "; ".join(broken) + "."

    # 2. how much was skipped, where, and what the skipped pairs were in the full run
    saving = f"Pairs compared in full: {p.compared} of {p.all_pairs}."
    per_kind = ", ".join(f"{k} {got} of {total}" for k, (got, total) in same_kind.items())
    partial = [k for k, (got, total) in same_kind.items() if 0 < got < total]
    if same_total and not partial:
        saving += (
            " This is **not a saving on this corpus**: within a view kind the index proposes "
            f"either every pair or none ({per_kind}), because the perceptual and vector layers "
            "put every view that holds them into one bucket."
        )
    elif same_total:
        saving += (
            f" Within a view kind, {same_skipped} of {same_total} pairs are skipped ({per_kind}), "
            "so the index is selective on this corpus."
        )
    if cross_kind and cross_compared == 0:
        saving += " No pair across view kinds is compared."
    elif cross_kind:
        saving += (
            f" Across view kinds, {cross_compared} of "
            f"{sum(total for _, total in cross_kind.values())} pairs are compared."
        )
    skipped_n = sum(p.skipped_types.values())
    if skipped_n and set(p.skipped_types) <= weak:
        saving += (
            f" All {skipped_n} skipped pairs are unrelated or insufficient evidence in the "
            "all-pairs run."
        )
    elif skipped_n:
        linked_skipped = {t: n for t, n in p.skipped_types.items() if t not in weak}
        saving += (
            f" **{sum(linked_skipped.values())} skipped pairs are linked in the all-pairs run** ("
            + ", ".join(f"{t} {n}" for t, n in linked_skipped.items())
            + ")."
        )

    # 3. what the identifier keys alone would do, and whether scaling was shown
    tail = []
    if index_only is not None and index_only.linked_total == 0:
        tail.append(
            "No pair is linked in the all-pairs run, so this corpus cannot show what the "
            "identifier keys alone would lose."
        )
    elif index_only is not None and index_only.linked_found == index_only.linked_total:
        tail.append(
            f"The identifier, set and MinHash keys alone find every linked pair here "
            f"({index_only.linked_found} of {index_only.linked_total}) while proposing "
            f"{index_only.pairs} pairs instead of {p.compared}"
            + (
                f"; the {len(index_only.scored_missed)} scored pairs they miss reach a tier "
                f"score of at most {index_only.scored_missed[0][2]:.2f}"
                if index_only.scored_missed
                else ""
            )
            + "."
        )
    elif index_only is not None:
        tail.append(
            f"The identifier, set and MinHash keys alone would miss "
            f"{index_only.linked_total - index_only.linked_found} of {index_only.linked_total} "
            "linked pairs here, so they cannot replace the buckets on this corpus."
        )
    if same_total and not partial:
        tail.append(
            "Scaling is therefore not demonstrated: at this size the catch-all buckets make "
            "candidate generation equal to all pairs within a view kind."
        )
    elif same_total:
        tail.append(
            f"The buckets skip {same_skipped} of {same_total} pairs within a view kind on this "
            "corpus; whether that holds at scale is not measured here."
        )
    tail.append(
        "The buckets stay because a link that rests only on vector similarity (a playbook "
        "link from real embeddings) has no identifier key to propose it."
    )

    return [
        verdict,
        *[f"- {d}" for d in p.differences],
        "",
        saving,
        "",
        *_table(
            ["View kinds", "Compared in full", "All pairs"],
            [[k, got, total] for k, (got, total) in p.by_kind.items()],
        ),
        "",
        *_table(
            ["Index key family (layer)", "Distinct pairs it proposes"],
            [[k, n] for k, n in p.by_key],
        ),
        "",
        (
            "What the index would propose under two key choices, measured against the all-pairs "
            "relations (a measurement only; the first row is what the code does):"
        ),
        "",
        *_table(
            [
                "Index keys",
                "Pairs proposed",
                "Page pairs",
                "Linked pairs found",
                "Pairs with a non-zero score not proposed",
            ],
            [
                [
                    v.name,
                    v.pairs,
                    f"{v.page_pairs} of {page[1]}",
                    f"{v.linked_found} of {v.linked_total}",
                    f"{len(v.scored_missed)}"
                    + (
                        f" (highest tier score {v.scored_missed[0][2]:.2f})"
                        if v.scored_missed
                        else ""
                    ),
                ]
                for v in p.variants
            ],
        ),
        "",
        " ".join(tail),
    ]


# -- markdown ----------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(str(c).replace("|", "/") for c in row) + " |" for row in rows]
    return lines


def format_runs(runs: list[SampleRun]) -> list[str]:
    base = runs[0]
    rows = []
    for r in runs:
        rep = r.report
        flips = [] if r is base else r.flipped_from(base)
        change = (
            "—"
            if r is base
            else ("none" if not flips else f"{len(flips)}: " + "; ".join(flips[:4]))
        )
        if len(flips) > 4:
            change += " …"
        if r.failed_checks:
            change += " **Checkpoint lost:** " + "; ".join(r.failed_checks)
        rows.append(
            [
                r.name,
                r.views,
                f"{len(rep.evaluated)}/{len(rep.results)}",
                len(rep.strict),
                len(rep.deviations),
                len(rep.disagreements),
                len(rep.false_merges),
                r.checks_text,
                change,
            ]
        )
    return _table(
        [
            "Run",
            "Views",
            "Pairs evaluated",
            "Strict",
            "Accepted alt.",
            "Misses",
            "False merges",
            "Checkpoint",
            "Reference pairs that changed",
        ],
        rows,
    )


def format_loio(results: list[SampleHeldOut]) -> list[str]:
    rows = []
    for r in results:
        a = r.headline
        if a is None:
            rows.append([r.incident_id, "—", "no views", "—"])
            continue
        target = a.target or "—"
        root = target.split(":", 1)[1] if target.startswith(("c:", "f:")) else target
        kind = {"c": "campaign of ", "f": "family of "}.get(target[0], "") if root != target else ""
        target = f"{kind}{r.labels[root]}" if root in r.labels else target
        outcomes = Counter(x.outcome.value for x in r.per_view)
        rest = ", ".join(f"{n}× {k}" for k, n in sorted(outcomes.items()))
        rows.append([r.incident_id, a.outcome.value, target, rest])
    return _table(["Held-out folder", "Headline outcome", "Target", "All its views"], rows)


def format_support(store: Store, rows: list[Support]) -> list[str]:
    labels = view_labels(store)
    order = {t: i for i, t in enumerate(RelationType)}
    out = []
    for s in sorted(rows, key=lambda s: (order[s.type], s.view_a, s.view_b)):
        groups = (
            "; ".join(f"{tier}: {', '.join(g)}" for tier, g in s.groups.items()) or "none ≥ 0.5"
        )
        out.append(
            [
                labels[s.view_a],
                labels[s.view_b],
                s.type.value,
                s.confidence or "—",
                f"{s.coverage:.2f} ({s.comparable}/{s.total})",
                groups,
            ]
        )
    return _table(
        [
            "View A",
            "View B",
            "Relation",
            "Confidence",
            "Coverage (groups)",
            "Agreeing groups ≥ 0.5",
        ],
        out,
    )


def format_review(store: Store) -> list[str]:
    rows = []
    for a, b, why in REVIEW_PAIRS:
        r = _relation(store, a, b)
        if r is None:
            rows.append([a, b, why, "not in corpus", "—", "—", "—"])
            continue
        rows.append(
            [
                a,
                b,
                why,
                r.type.value,
                r.confidence or "—",
                f"{r.coverage:.2f}",
                "; ".join(r.reasons)[:230],
            ]
        )
    return _table(["A", "B", "Why reviewed", "Relation", "Confidence", "Coverage", "Reasons"], rows)


def validation_report(
    sample_dir: Path,
    config: FusionConfig = DEFAULT,
    workdir: Path | None = None,
    parts: Iterable[str] = ("candidates", "degrade", "ablate", "loio"),
) -> str:
    """The whole real-sample validation as one Markdown document."""
    parts = set(parts)
    load_plugins()
    base = extracted(Path(sample_dir))
    with model_off():
        store, groups = method_store(base, config)
        info = manifest(base, Path(sample_dir), store, config)
        report = evaluate(store)
        checks = checkpoint(store, groups)
        review = format_review(store)
        supported = format_support(store, linked_support(store, config))
        problems = confidence_problems(store, config)
        with_fp = {f.view_id for f in store.fingerprints()}
        by_conf = Counter((r.type.value, r.confidence or "—") for r in store.relations())
        by_kind = []
        for kind in ViewKind:
            ids = {v.view_id for v in store.views() if v.kind is kind}
            built = sorted({f.layer_id for f in store.fingerprints() if f.view_id in ids})
            by_kind.append(
                f"- {kind.value} views: {len(ids)}; {len(ids & with_fp)} carry a fingerprint; "
                "layers built: " + (", ".join(f"`{n}`" for n in built) or "none") + "."
            )
        no_evidence = sum(
            1 for r in store.relations() if r.view_a not in with_fp or r.view_b not in with_fp
        )
        store.close()
    weights.reset()
    missing_layers = unbuilt_layers(base)

    out = [
        "# Method validation on the organiser sample (HAC-54)",
        "",
        "Generated by `uv run ftf validate testing_sample --out docs/method-results-real-sample.md`.",
        "Every number is from real organiser files, model off. The fixture tables of HAC-35",
        "(architecture §13a) are a separate, earlier measurement and are not repeated here.",
        "Results with real gateway embeddings belong to HAC-32 and are not in this file.",
        "",
        "## Run record",
        "",
        *info.lines(),
        "",
        "## Reference agreement",
        "",
        "The reference is one analyst's reading (docs/baseline-report.md), a yardstick and not",
        "ground truth. Strict agreement, accepted alternatives, misses and false merges are",
        "counted over evaluated pairs only; this is not classifier accuracy.",
        "",
        "```",
        format_report(report),
        "```",
        "",
        "## Checkpoint outcomes",
        "",
        *_table(
            ["Outcome", "Result"],
            [
                [k, {True: "holds", False: "**lost**", None: "not in corpus"}[v]]
                for k, v in checks.items()
            ],
        ),
        "",
        "## Negative and uncertain pairs",
        "",
        *review,
        "",
        "## Confidence and coverage against the supporting groups",
        "",
        "Confidence is a rule label, not a probability. Each linked pair is listed with the",
        "independent correlation groups that score at least 0.5 for it.",
        "",
        *supported,
        "",
        "Relations by type and confidence label:",
        "",
        *_table(
            ["Relation", "Confidence", "Pairs"],
            [[t, c, n] for (t, c), n in sorted(by_conf.items())],
        ),
        "",
        f"{no_evidence} pairs have a side with no fingerprint at all; all of them are",
        "*insufficient evidence* with no confidence label. Missing data is never read as",
        "absence or as separation.",
        "",
        "Rule check (confidence labels the evidence does not carry): "
        + ("none found." if not problems else f"{len(problems)} found:"),
        *[f"- {p}" for p in problems],
        "",
        "## Unavailable in this mode",
        "",
        "- Layers that built no fingerprint on this corpus: "
        + ", ".join(f"`{n}`" for n in missing_layers)
        + ".",
        "- Model-dependent layers (" + ", ".join(f"`{n}`" for n in MODEL_LAYERS) + "): the two",
        "  text vector layers ran on the local hashed-word embedder, which measures shared",
        "  wording only; the others need `ftf perceive` with a key and produced nothing.",
        *by_kind,
        "- Reading what a screenshot or a video shows, and so an ad-to-page funnel edge,",
        "  needs the vision model: unavailable in this mode.",
    ]
    if "candidates" in parts:
        out += [
            "",
            "## Candidate generation against all pairs (HAC-36)",
            "",
            *format_candidates(candidate_parity(base, config)),
        ]
    if "degrade" in parts:
        out += [
            "",
            "## Degradation on real files",
            "",
            "Each run rebuilds the corpus from a damaged copy of the files, on a fresh store.",
            "",
            *format_runs(runs := degrade_sample(Path(sample_dir), base, config, workdir)),
            "",
            *[f"- **{r.name}:** {r.note}." for r in runs if r.note],
        ]
    if "ablate" in parts:
        out += [
            "",
            "## Layer ablation",
            "",
            "One layer off per run; only layers that built a fingerprint on the sample.",
            "",
            *format_runs(ablate_sample(base, config)),
        ]
    if "loio" in parts:
        out += [
            "",
            "## Leave-one-incident-out",
            "",
            "The corpus is rebuilt without the held-out folder; weights, stoplist, demotions,",
            "calibration and groups are fitted without it and frozen while it is assigned.",
            "",
            *format_loio(loio_sample(base, config)),
        ]
    base.close()
    return "\n".join(out) + "\n"
