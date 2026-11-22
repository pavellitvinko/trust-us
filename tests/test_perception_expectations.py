"""Expected-element checks. A fake model only: nothing here touches the network."""

import pytest

from ftf.compare.pattern import AbsenceAgreement
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
            names = ex.json.loads(content)["names"]
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
def ctx(store, tmp_path):
    (tmp_path / "page.html").write_text(
        '<p>x</p><footer><a href="/kontakt">Kontakt</a></footer>', encoding="utf-8"
    )
    views = [
        View(view_id=v, incident_id="i", kind=ViewKind.PAGE, label=v, primary="page.html")
        for v in ("v1", "v2")
    ]
    store.put_incident(Incident(incident_id="i", root=str(tmp_path)), [], views)
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
    assert present.source.locator.startswith("expect.read_text:span[")
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


def test_rerun_reuses_the_stored_kind_mapping(ctx, monkeypatch):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel())
    model = FakeModel()
    run(ctx, monkeypatch, model)
    assert "kinds" not in model.tasks


def fingerprint(ctx, view_id):
    return KitAbsence().build(ctx.store.get_view(view_id), ctx.store.observations(view_id))


def test_same_kind_views_compare(ctx, monkeypatch):
    seed(ctx, "v1", kind="crypto offer")
    seed(ctx, "v2", kind="trading platform")
    same = {"crypto offer": "investment offer", "trading platform": "investment offer"}
    run(ctx, monkeypatch, FakeModel(kinds=same), views=("v1", "v2"))
    assert AbsenceAgreement().compatible(fingerprint(ctx, "v1"), fingerprint(ctx, "v2"))


def test_other_kinds_do_not_compare(ctx, monkeypatch):
    seed(ctx, "v1", kind="crypto offer")
    seed(ctx, "v2", kind="news page")
    other = {"crypto offer": "investment offer", "news page": "news article"}
    run(ctx, monkeypatch, FakeModel(kinds=other), views=("v1", "v2"))
    assert not AbsenceAgreement().compatible(fingerprint(ctx, "v1"), fingerprint(ctx, "v2"))


def test_inflected_wording_contradicts_an_absent_claim(ctx, monkeypatch):
    seed(ctx, text="Zapoznaj się z Regulaminem serwisu. Administratorem danych jest ACME.")
    check = [result("terms", "absent"), *GOOD[1:]]
    run(ctx, monkeypatch, FakeModel(check=check, recheck=[]))
    assert states(ctx)["terms"] is State.NOT_OBSERVABLE


def test_body_text_alone_cannot_prove_absence(ctx, monkeypatch):
    rows = page_obs("v1", text=None)
    body = page_obs("v1")[1].model_copy(update={"namespace": "page.body_text"})
    ctx.store.put_observations("v1", "perception.page", rows)
    ctx.store.put_observations("v1", "page.text", [body])
    run(ctx, monkeypatch, FakeModel())
    assert states(ctx)["risk_warning"] is State.NOT_OBSERVABLE


def test_links_reach_the_model_and_the_absence_search(ctx, monkeypatch, tmp_path):
    (tmp_path / "page.html").write_text(
        '<footer><a href="/regulamin.pdf"><img alt=""></a>'
        '<a style="display:none" href="/ukryty">Ukryty</a></footer>',
        encoding="utf-8",
    )
    seed(ctx, text="Oferta inwestycyjna. Administratorem danych jest ACME.")
    check = [result("terms", "absent"), *GOOD[1:]]
    answer = [result("terms", "present", "/regulamin.pdf")]
    run(ctx, monkeypatch, FakeModel(check=check, recheck=answer))
    assert states(ctx)["terms"] is State.PRESENT
    assert ex.visible_links((tmp_path / "page.html").read_text()) == ["-> /regulamin.pdf"]


def test_missing_markup_cannot_prove_absence(ctx, monkeypatch, tmp_path):
    (tmp_path / "page.html").unlink()
    seed(ctx)
    run(ctx, monkeypatch, FakeModel())
    assert states(ctx)["risk_warning"] is State.NOT_OBSERVABLE


def test_failed_rejudgement_keeps_the_earlier_check(ctx, monkeypatch):
    seed(ctx)
    run(ctx, monkeypatch, FakeModel())
    check = [result("terms", "absent"), *GOOD[1:]]
    summary = run(ctx, monkeypatch, FakeModel(check=check, fail={"recheck": "timeout"}))
    assert summary["kept"] == 1
    assert states(ctx)["terms"] is State.PRESENT


def test_kept_check_restores_the_canonical_kind(ctx, monkeypatch):
    seed(ctx, kind="crypto offer")
    run(ctx, monkeypatch, FakeModel())
    seed(ctx, kind="crypto offer")
    summary = run(ctx, monkeypatch, FakeModel(fail={"check": "timeout"}))
    assert summary["kept"] == 1
    assert fingerprint(ctx, "v1").payload["kind"] == "investment offer"


def test_skipped_kind_name_becomes_its_own_kind(ctx, monkeypatch):
    seed(ctx, kind="crypto offer")

    class Skips(FakeModel):
        def __call__(self, task, version, system, content, schema, **kwargs):
            if task.endswith("kinds"):
                return ModelResult.ok(ex.CanonicalKinds(kinds=[]))
            return super().__call__(task, version, system, content, schema, **kwargs)

    assert run(ctx, monkeypatch, Skips())["checked"] == 1
    assert fingerprint(ctx, "v1").payload["kind"] == "crypto offer"


def test_refused_kind_name_does_not_wipe_other_pages(ctx, monkeypatch):
    seed(ctx, "v1", kind="crypto offer")
    run(ctx, monkeypatch, FakeModel())
    seed(ctx, "v2", kind="hostile name")

    class Refuses(FakeModel):
        def __call__(self, task, version, system, content, schema, **kwargs):
            if task.endswith("kinds") and "hostile" in content:
                return ModelResult.not_observable("refusal")
            return super().__call__(task, version, system, content, schema, **kwargs)

    summary = run(ctx, monkeypatch, Refuses(), views=("v1", "v2"))
    assert summary["checked"] == 1
    assert states(ctx, "v1")["risk_warning"] is State.ABSENT


def test_partial_run_keeps_stored_kinds(ctx, monkeypatch):
    seed(ctx, "v1", kind="crypto offer")
    run(ctx, monkeypatch, FakeModel())
    seed(ctx, "v2", kind="forex offer")
    run(ctx, monkeypatch, FakeModel(kinds={"forex offer": "trading offer"}), views=("v2",))
    assert fingerprint(ctx, "v1").payload["kind"] == "investment offer"


def test_stable_failure_keeps_the_named_kind(ctx, monkeypatch):
    seed(ctx, kind="crypto offer")
    run(ctx, monkeypatch, FakeModel())
    run(ctx, monkeypatch, FakeModel(fail={"check": "refusal"}))
    rows = ctx.store.observations("v1")
    assert ex._named_kind(rows) == "crypto offer"


def test_locator_points_at_the_stored_text(ctx, monkeypatch):
    seed(ctx, text="https://x.pl/?token=abcdefghijkl Regulamin serwisu. " + TEXT)
    run(ctx, monkeypatch, FakeModel())
    rows = ctx.store.observations("v1")
    stored = next(o.value for o in rows if o.namespace == ex.READ_TEXT_NAMESPACE)
    present = next(o for o in rows if o.value == "terms")
    start, end = map(int, present.source.locator.split("[")[1].rstrip("]").split(","))
    assert stored[start:end] == present.raw
