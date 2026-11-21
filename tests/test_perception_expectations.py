"""Expected-element checks. A fake model only: nothing here touches the network."""

import pytest

from ftf.contracts import Incident, Observation, Source, State, View, ViewKind
from ftf.layers.playbook import KitAbsence
from ftf.models import ModelResult
from ftf.perception import expectations as ex
from ftf.registry import Context

TEXT = "Oferta inwestycyjna. Regulamin serwisu jest tutaj. Administratorem danych jest ACME."


def element(name, *wordings):
    return ex.ProfileElement(
        name=name,
        description=name,
        basis="legal_requirement",
        basis_detail="RODO art. 13",
        polish_wordings=list(wordings),
        expected_location="footer",
    )


PROFILE = ex.KindProfile(
    elements=[
        element("Terms", "regulamin"),
        element("Controller", "administratorem danych"),
        element("Risk warning", "ostrzeżenie o ryzyku"),
    ]
)


def result(key, state, quote=None):
    return ex.ElementResult(element_id=key, state=state, quote=quote, searched=["footer"])


GOOD = [
    result("terms", "present", "Regulamin serwisu"),
    result("controller", "present", "Administratorem danych jest ACME"),
    result("risk_warning", "absent"),
]


class FakeModel:
    def __init__(self, check=GOOD, recheck=None, fail=None, kinds=None):
        self.check, self.recheck, self.fail, self.kinds = check, recheck, fail or {}, kinds
        self.tasks = []

    def __call__(self, task, version, system, content, schema, **kwargs):
        step = task.rsplit(".", 1)[1]
        self.tasks.append(step)
        if step in self.fail:
            return ModelResult.not_observable(self.fail[step])
        if step == "kinds":
            names = ex.json.loads(content)
            mapped = self.kinds or dict.fromkeys(names, "investment offer")
            return ModelResult.ok(
                ex.CanonicalKinds(
                    kinds=[ex.KindMapping(name=n, canonical=mapped[n]) for n in names]
                )
            )
        if step == "profile":
            return ModelResult.ok(PROFILE)
        answer = self.check if step == "check" else self.recheck
        return ModelResult.ok(ex.ViewCheck(results=answer))


def page_obs(view_id, kind="fake investment page", text=TEXT):
    out = [
        Observation(
            view_id=view_id,
            signal_id="F4",
            namespace="page.kind",
            value=kind,
            source=Source(file="page.html"),
            extractor="perception.page",
            version="1",
        )
    ]
    if text is not None:
        out.append(
            Observation(
                view_id=view_id,
                signal_id="F1",
                namespace="page.clean_text",
                value=text,
                source=Source(file="page.html"),
                extractor="page.text",
                version="1",
            )
        )
    return out


@pytest.fixture
def ctx(store):
    views = [
        View(view_id=v, incident_id="i", kind=ViewKind.PAGE, label=v, primary="page.html")
        for v in ("v1", "v2")
    ]
    store.put_incident(Incident(incident_id="i", root="."), [], views)
    return Context(store)


def seed(ctx, view_id="v1", **kwargs):
    rows = page_obs(view_id, **kwargs)
    for extractor in {o.extractor for o in rows}:
        ctx.store.put_observations(
            view_id, extractor, [o for o in rows if o.extractor == extractor]
        )


def run(ctx, monkeypatch, model, views=("v1",)):
    monkeypatch.setattr(ex, "read_structured", model)
    return ex.run_expectations(ctx, list(views))


def states(ctx, view_id="v1"):
    return {
        o.value: o.state
        for o in ctx.store.observations(view_id)
        if o.namespace == ex.ELEMENT_NAMESPACE
    }


def test_verified_output_reaches_the_kit_absence_layer(ctx, monkeypatch):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel())
    assert states(ctx) == {
        "terms": State.PRESENT,
        "controller": State.PRESENT,
        "risk_warning": State.ABSENT,
    }
    rows = ctx.store.observations("v1")
    absent = next(o for o in rows if o.value == "risk_warning")
    assert absent.raw.startswith("expected element not found: Risk warning")
    assert "legal requirement" in absent.raw
    present = next(o for o in rows if o.value == "terms")
    assert present.raw == "Regulamin serwisu"
    assert present.source.locator.startswith("page.clean_text:span[")
    fingerprint = KitAbsence().build(ctx.store.get_view("v1"), rows)
    assert fingerprint.payload["kind"] == "investment offer"
    assert len(fingerprint.payload["items"]) == 3


def test_invented_quote_is_rejected(ctx, monkeypatch):
    seed(ctx)
    check = [result("terms", "present", "Licencja KNF nr 123"), *GOOD[1:]]
    run(ctx, monkeypatch, FakeModel(check=check))
    assert states(ctx)["terms"] is State.NOT_OBSERVABLE


def test_absent_claim_is_corrected_by_rejudgement(ctx, monkeypatch):
    seed(ctx)
    check = [result("terms", "absent"), *GOOD[1:]]
    model = FakeModel(check=check, recheck=[result("terms", "present", "Regulamin serwisu")])
    run(ctx, monkeypatch, model)
    assert model.tasks.count("recheck") == 1
    assert states(ctx)["terms"] is State.PRESENT


@pytest.mark.parametrize("recheck", [[result("terms", "absent")], []])
def test_contradicted_absent_never_stays_absent(ctx, monkeypatch, recheck):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel(check=[result("terms", "absent"), *GOOD[1:]], recheck=recheck))
    assert states(ctx)["terms"] is State.NOT_OBSERVABLE


def test_failed_rejudgement_is_not_observable(ctx, monkeypatch):
    seed(ctx)
    model = FakeModel(check=[result("terms", "absent"), *GOOD[1:]], fail={"recheck": "timeout"})
    run(ctx, monkeypatch, model)
    assert states(ctx)["terms"] is State.NOT_OBSERVABLE


def test_truncated_page_cannot_prove_absence(ctx, monkeypatch):
    seed(ctx, text=TEXT + " x" * ex.MAX_TEXT_CHARS)
    run(ctx, monkeypatch, FakeModel())
    assert states(ctx)["risk_warning"] is State.NOT_OBSERVABLE
    assert states(ctx)["terms"] is State.PRESENT


def test_unavailable_content_is_not_observable(ctx, monkeypatch):
    seed(ctx, text=None)
    run(ctx, monkeypatch, FakeModel())
    assert set(states(ctx).values()) == {State.NOT_OBSERVABLE}


def test_element_the_model_skipped_is_not_observable(ctx, monkeypatch):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel(check=GOOD[:2]))
    assert states(ctx)["risk_warning"] is State.NOT_OBSERVABLE


@pytest.mark.parametrize("step", ["kinds", "profile", "check"])
@pytest.mark.parametrize("reason", ["no_key", "refusal", "timeout", "schema"])
def test_missing_key_or_refusal_never_becomes_absent(ctx, monkeypatch, step, reason):
    seed(ctx)
    summary = run(ctx, monkeypatch, FakeModel(fail={step: reason}))
    assert summary["checked"] == 0
    assert State.ABSENT not in states(ctx).values()
    assert KitAbsence().build(ctx.store.get_view("v1"), ctx.store.observations("v1")) is None


def test_passing_failure_keeps_the_earlier_check(ctx, monkeypatch):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel())
    summary = run(ctx, monkeypatch, FakeModel(fail={"check": "timeout"}))
    assert summary["kept"] == 1
    assert states(ctx)["risk_warning"] is State.ABSENT


def test_rerun_recovers_the_named_kind(ctx, monkeypatch):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel())
    seen = []

    class Recording(FakeModel):
        def __call__(self, task, version, system, content, schema, **kwargs):
            if task.endswith("kinds"):
                seen.append(ex.json.loads(content))
            return super().__call__(task, version, system, content, schema, **kwargs)

    run(ctx, monkeypatch, Recording())
    assert seen == [["fake investment page"]]


def test_same_kind_views_compare_and_other_kinds_do_not(ctx, monkeypatch):
    from ftf.compare.pattern import AbsenceAgreement

    seed(ctx, "v1", kind="crypto offer")
    seed(ctx, "v2", kind="trading platform")
    same = {"crypto offer": "investment offer", "trading platform": "investment offer"}
    run(ctx, monkeypatch, FakeModel(kinds=same), views=("v1", "v2"))

    def fingerprint(view_id):
        view = ctx.store.get_view(view_id)
        return KitAbsence().build(view, ctx.store.observations(view_id))

    assert AbsenceAgreement().compatible(fingerprint("v1"), fingerprint("v2"))
    other = {"crypto offer": "investment offer", "trading platform": "news article"}
    for view_id, kind in (("v1", "crypto offer"), ("v2", "trading platform")):
        seed(ctx, view_id, kind=kind)
    run(ctx, monkeypatch, FakeModel(kinds=other), views=("v1", "v2"))
    assert not AbsenceAgreement().compatible(fingerprint("v1"), fingerprint("v2"))
