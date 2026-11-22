"""Inspection reads stored evidence and case files without analysis or external access."""

import hashlib
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from ftf.contracts import Artifact, Incident, Observation, Source, State, View, ViewKind
from ftf.store import Store
from ftf.web import media
from ftf.web.inspection import artifact_ref, evidence_ref


def obs(
    view="a", ns="fb_pixel", value="PIXEL-123", file="page.html", locator="html:script[1]", **kw
):
    return Observation(
        view_id=view,
        namespace=ns,
        value=value,
        signal_id="A3",
        extractor="test",
        version="1",
        source=Source(file=file, locator=locator),
        **kw,
    )


@pytest.fixture
def inspection(tmp_path, monkeypatch):
    case = tmp_path / "case"
    case.mkdir()
    (case / "page.html").write_text(
        '<html><script src="first.js"></script>\n<script>pixel="PIXEL-123";'
        'token="private-token-123";</script><body><img src="https://host.invalid/x">'
        "Not executed</body></html>"
    )
    (case / "page.har").write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {
                                "url": "https://example.invalid/?pixel=PIXEL-123&token=private-token-123",
                                "headers": [{"name": "Cookie", "value": "unobserved-cookie"}],
                            },
                            "response": {"content": {"text": "<html>archived</html>"}},
                        }
                    ]
                }
            }
        )
    )
    (case / "manifest.json").write_text('{"final_url":"https://example.invalid/"}')
    (case / "ad.mp4").write_bytes(b"not a video yet")
    data = tmp_path / "data"
    store = Store(data / "ftf.sqlite")
    views = [
        View(view_id="a", incident_id="1", kind=ViewKind.PAGE, label="Page A", primary="page.html"),
        View(view_id="b", incident_id="1", kind=ViewKind.PAGE, label="Page B", primary="page.html"),
        View(view_id="v", incident_id="1", kind=ViewKind.VIDEO, label="Video", primary="ad.mp4"),
    ]
    artifacts = [
        Artifact(
            incident_id="1",
            path=p.name,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            size=p.stat().st_size,
            media_type="video/mp4" if p.suffix == ".mp4" else "text/plain",
            role="primary",
        )
        for p in case.iterdir()
    ]
    store.put_incident(Incident(incident_id="1", root=str(case)), artifacts, views)
    rows = [
        obs(),
        obs(ns="ad.access_token", value="private-token-123"),
        obs(ns="page.body_text", value="Not executed"),
        obs(ns="har.url", file="page.har", locator="har:entries[0].request.url"),
        obs(ns="manifest.url", file="manifest.json", locator="manifest.json:final_url"),
        obs(ns="missing", value=None, state=State.NOT_OBSERVABLE),
        obs(ns="absent", value="PIXEL-123", state=State.ABSENT),
    ]
    store.put_observations("a", "test", rows)
    store.put_observations("b", "test", [obs(view="b"), obs(view="b", ns="other.id")])
    store.close()
    monkeypatch.setenv("FTF_DATA_DIR", str(data))
    monkeypatch.setenv("FTF_WEB_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setattr("ftf.previews.render_missing", lambda *a: 0)
    from ftf.web.app import create_app

    with TestClient(create_app()) as client:
        yield client, data, case, rows


def test_evidence_source_and_pivot_keep_secrets_out_of_responses(inspection):
    client, _data, case, rows = inspection
    result = client.get("/api/items/a/evidence")
    assert result.status_code == 200
    assert "private-token-123" not in result.text
    values = result.json()["rows"]
    assert values[0]["pivot"] is True
    assert values[2]["pivot"] is False  # full-page text
    assert values[5]["pivot"] is False  # unavailable
    assert values[6]["pivot"] is False  # absent
    source = client.get(f"/api/items/a/evidence/{evidence_ref(rows[0])}").json()
    assert source["resolution"] == "exact"
    assert '<script>pixel="PIXEL-123";' in source["highlight"]
    assert "private-token-123" not in json.dumps(source)
    assert source["download"]
    download = client.get(source["download"])
    assert download.content == (case / "page.html").read_bytes()
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment")
    assert client.get(source["download"] + "?preview=true").status_code == 400
    for all_namespaces in (False, True):
        result = client.post(
            "/api/lookup",
            json={"viewId": "a", "ref": values[0]["ref"], "allNamespaces": all_namespaces},
        ).json()
        assert {h["id"] for h in result["hits"]} == {"a", "b"}
        b = next(h for h in result["hits"] if h["id"] == "b")
        assert b["namespaces"] == (["fb_pixel", "other.id"] if all_namespaces else ["fb_pixel"])
        assert all(h["exact"] for h in result["hits"])
    secret = client.post("/api/lookup", json={"viewId": "a", "ref": values[1]["ref"]})
    assert secret.status_code == 200 and "private-token-123" not in secret.text
    for row in values[2:3] + values[5:]:
        assert (
            client.post("/api/lookup", json={"viewId": "a", "ref": row["ref"]}).status_code == 400
        )


def test_har_manifest_and_unresolved_sources(inspection):
    client, data, _case, rows = inspection
    har = client.get(f"/api/items/a/evidence/{evidence_ref(rows[3])}").json()
    assert har["resolution"] == "exact" and "PIXEL-123" in har["highlight"]
    assert "private-token-123" not in json.dumps(har)
    manifest = client.get(f"/api/items/a/evidence/{evidence_ref(rows[4])}").json()
    assert manifest["resolution"] == "exact" and "example.invalid" in manifest["highlight"]
    extra = [
        obs(ns="broad", locator="html>text:body"),
        obs(ns="unknown", locator="html:script[99]"),
        obs(ns="har.entry", file="page.har", locator="har:entries[0]"),
        obs(ns="missing.file", file="gone.html"),
    ]
    with_store = Store(data / "ftf.sqlite")
    with_store.put_observations("a", "more", extra)
    with_store.close()
    for row in extra[:2]:
        source = client.get(f"/api/items/a/evidence/{evidence_ref(row)}").json()
        assert source["resolution"] == "region"
        assert "Exact location unavailable" in source["message"]
        assert "Not executed" in source["after"]
    entry = client.get(f"/api/items/a/evidence/{evidence_ref(extra[2])}").json()
    assert "unobserved-cookie" not in json.dumps(entry)
    missing = client.get(f"/api/items/a/evidence/{evidence_ref(extra[3])}").json()
    assert missing["resolution"] == "unavailable" and missing["download"] is None
    assert client.get("/api/items/a/evidence/expired").status_code == 404
    assert client.get(f"/api/items/b/evidence/{evidence_ref(rows[0])}").status_code == 404


def test_artifact_paths_cannot_escape_the_case(inspection):
    client, data, case, _rows = inspection
    secret = case.parent / "outside.txt"
    secret.write_text("outside")
    (case / "link.txt").symlink_to(secret)
    store = Store(data / "ftf.sqlite")
    for name in ("../outside.txt", str(secret), "link.txt"):
        artifact = Artifact(
            incident_id="1", path=name, sha256="x", size=7, media_type="text/plain", role="other"
        )
        store.db.execute(
            "insert into artifacts values (?,?,?,?)", ("1", name, "x", artifact.model_dump_json())
        )
    store.db.commit()
    store.close()
    for name in ("../outside.txt", str(secret), "link.txt", "never-stored.txt"):
        assert client.get(f"/api/items/a/artifacts/{artifact_ref(name)}").status_code == 404
    (case / "page.html").unlink()
    assert client.get(f"/api/items/a/artifacts/{artifact_ref('page.html')}").status_code == 404


def test_lookup_fallback_and_stale_references(inspection):
    client, data, _case, rows = inspection
    store = Store(data / "ftf.sqlite")
    # Whitespace is normalised by existing lookup, so only the case-insensitive target matches.
    row = obs(value="  pixel-123  ")
    store.put_observations("a", "test", [row])
    store.close()
    found = client.post("/api/lookup", json={"viewId": "a", "ref": evidence_ref(row)}).json()
    assert [h["id"] for h in found["hits"]] == ["b"]
    assert found["hits"][0]["exact"] is False
    assert (
        client.post("/api/lookup", json={"viewId": "a", "ref": evidence_ref(rows[0])}).status_code
        == 404
    )


def test_comparison_adds_source_actions_without_changing_downloads(inspection):
    client, _data, _case, _rows = inspection
    result = client.get("/api/compare/a/b").json()
    assert result["markdown"] and result["sources"]
    assert "private-token-123" not in json.dumps(result)
    assert set(result["sharedRefs"]) <= result["sources"].keys()
    for row in result["sources"].values():
        assert client.get(f"/api/items/{row['viewId']}/evidence/{row['ref']}").status_code == 200
    assert client.get("/api/compare/a/b?format=json").json()["schema_version"] == "1"
    assert client.get("/api/compare/a/b?format=md").text.startswith("# Pair comparison report")


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is not installed")
    path = tmp_path_factory.mktemp("video") / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=5:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path


def test_video_ranges_stored_analysis_and_cached_frames(inspection, recording, monkeypatch):
    client, data, case, _rows = inspection
    shutil.copyfile(recording, case / "ad.mp4")
    store = Store(data / "ftf.sqlite")
    store.put_observations(
        "v",
        "test",
        [
            obs(
                view="v",
                ns="video.transcript",
                value="Stored speech.",
                file="ad.mp4",
                locator="video:audio",
            ),
            obs(
                view="v",
                ns="video.on_screen_text",
                value="Caption",
                file="ad.mp4",
                locator="video:picture",
            ),
            obs(
                view="v",
                ns="video.scene",
                value="A blue screen",
                file="ad.mp4",
                locator="video:t=1.0s",
            ),
        ],
    )
    store.close()
    payload = client.get("/api/items/v/media").json()
    assert payload["compatible"] and payload["analysis"] == "available"
    assert payload["transcript"] == "Stored speech." and payload["onScreen"] == ["Caption"]
    assert payload["frames"] == [{"seconds": 1.0, "sampled": False}]
    ranged = client.get("/api/items/v/media/video", headers={"Range": "bytes=0-31"})
    assert ranged.status_code == 206 and len(ranged.content) == 32
    assert ranged.headers["content-range"].startswith("bytes 0-31/")
    assert (
        client.get("/api/items/v/media/video", headers={"Range": "bytes=9999999-"}).status_code
        == 416
    )
    frame = client.get("/api/items/v/media/frame?seconds=1")
    assert frame.status_code == 200 and frame.content.startswith(b"\xff\xd8\xff")
    monkeypatch.setattr(
        "ftf.perception.techniques._video_frame",
        lambda *a: pytest.fail("cached frame decoded again"),
    )
    assert client.get("/api/items/v/media/frame?seconds=1").content == frame.content
    thumbnail = client.get("/api/items/v/media/thumbnail")
    assert thumbnail.status_code == 200 and thumbnail.headers["content-type"] == "image/jpeg"
    assert thumbnail.content == frame.content  # no second decode or separate thumbnail cache
    assert client.get("/api/items/v/media/frame?seconds=-1").status_code == 422
    assert client.get("/api/items/v/media/frame?seconds=nan").status_code == 422


def test_video_conversion_cache_and_missing_analysis(inspection, recording, monkeypatch):
    client, data, case, _rows = inspection
    shutil.copyfile(recording, case / "ad.mp4")
    payload = client.get("/api/items/v/media").json()
    assert payload["analysis"] == "not_run" and len(payload["frames"]) == 8
    assert all(f["sampled"] for f in payload["frames"])
    thumbnail = client.get("/api/items/v/media/thumbnail")
    first = payload["frames"][0]["seconds"]
    assert thumbnail.status_code == 200
    assert thumbnail.content == client.get(f"/api/items/v/media/frame?seconds={first}").content
    assert client.post("/api/items/v/media/prepare").status_code == 200
    assert (
        client.get(
            "/api/items/v/media/video?prepared=true", headers={"Range": "bytes=0-9"}
        ).status_code
        == 206
    )
    monkeypatch.setattr(
        "ftf.models.gemini._convert_to_mp4", lambda *a: pytest.fail("cached video converted again")
    )
    assert client.post("/api/items/v/media/prepare").status_code == 200
    path = case / "ad.mp4"
    old = media.converted_path(path, data)
    path.write_bytes(path.read_bytes() + b"changed")
    assert media.converted_path(path, data) != old
    assert client.get("/api/items/v/media/video?prepared=true").status_code == 404


def test_video_failure_states(inspection, monkeypatch):
    client, _data, case, _rows = inspection
    corrupt = client.get("/api/items/v/media").json()
    assert "could not be read" in corrupt["error"] and corrupt["download"]
    assert client.get("/api/items/v/media/thumbnail").status_code == 404
    assert client.post("/api/items/v/media/prepare").status_code == 422
    media._probe.cache_clear()

    def missing(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("ftf.web.media.subprocess.run", missing)
    unavailable = client.get("/api/items/v/media").json()
    assert "not installed" in unavailable["error"]
    (case / "ad.mp4").unlink()
    missing_file = client.get("/api/items/v/media").json()
    assert missing_file["available"] is False and "missing" in missing_file["error"]
    assert client.get("/api/items/v/media/video").status_code == 404
    assert client.get("/api/items/v/media/thumbnail").status_code == 404


def test_hevc_uses_explicit_cached_conversion(inspection, recording):
    client, data, case, _rows = inspection
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(recording),
            "-c:v",
            "libx265",
            "-x265-params",
            "log-level=error",
            "-y",
            str(case / "ad.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    before = client.get("/api/items/v/media").json()
    assert before["available"] and not before["compatible"] and not before["prepared"]
    assert not (data / "media").exists()  # inspection does not implicitly transcode
    assert client.post("/api/items/v/media/prepare").status_code == 200
    after = client.get("/api/items/v/media").json()
    assert after["prepared"]
    converted = media.converted_path(case / "ad.mp4", data)
    assert media._probe(str(converted), media.identity(converted))["compatible"]


def test_missing_analysis_is_distinct_from_unavailable(inspection, recording):
    client, data, case, _rows = inspection
    shutil.copyfile(recording, case / "ad.mp4")
    store = Store(data / "ftf.sqlite")
    store.put_observations(
        "v",
        "test",
        [
            obs(
                view="v",
                ns="video.transcript",
                value=None,
                state=State.NOT_OBSERVABLE,
                raw="no_key",
                file="ad.mp4",
                locator="video:audio",
            )
        ],
    )
    store.close()
    data = client.get("/api/items/v/media").json()
    assert data["analysis"] == "unavailable" and data["analysisReason"] == "no_key"
    assert data["compatible"] and len(data["frames"]) == 8
