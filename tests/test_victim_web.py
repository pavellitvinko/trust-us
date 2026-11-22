"""The victim visitor API behind Add material: runs, their files, and starting a run."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ftf.web import victim as web

RUN = "victim-20261122-093426-e133"


def client(tmp_path, monkeypatch, docker=False):
    monkeypatch.setattr(web, "in_container", lambda: docker)
    changed = []
    app = FastAPI()
    app.include_router(
        web.victim_router(tmp_path / "runs", tmp_path / "none.db", lambda: changed.append(1))
    )
    return TestClient(app), changed


def finished_run(tmp_path):
    files = tmp_path / "runs" / RUN / "run_files"
    (files / "recording").mkdir(parents=True)
    (files / "recording" / "clip-01.webm").write_bytes(b"\x1aE\xdf\xa3" + b"0" * 2000)
    (files / "recording" / "clip-01.vtt").write_text("WEBVTT\n")
    (files / "steps.jsonl").write_text(
        json.dumps(
            {
                "actor": "agent",
                "step": 1,
                "chosen": "[1] link: Dalej",
                "chosen_kind": "link",
                "reason": "follows the call to action",
                "screenshot": "run_files/steps/step-001.png",
            }
        )
        + "\n"
    )
    (files / "run.json").write_text(
        json.dumps(
            {
                "persona": {"id": "retiree-income", "version": "1"},
                "start_url": "https://offer.example/?fbclid=x",
                "steps": 1,
                "stop_reason": "offsite_handoff",
                "stop_detail": "konsultant zadzwoni",
                "stages": [
                    {
                        "index": 1,
                        "dir": "stage-01",
                        "url": "https://offer.example/",
                        "kinds": ["advertorial"],
                        "arrived_from": {"how": "start", "step": 0, "http_redirects": []},
                    }
                ],
                "recording": {
                    "clips": [
                        {
                            "video": "run_files/recording/clip-01.webm",
                            "captions": "run_files/recording/clip-01.vtt",
                        }
                    ],
                    "size": {"width": 1280, "height": 860},
                },
            }
        )
    )
    return files


def test_a_finished_run_lists_and_reads_with_its_video(tmp_path, monkeypatch):
    finished_run(tmp_path)
    c, _ = client(tmp_path, monkeypatch)
    overview = c.get("/api/victim").json()
    assert overview["canStart"] and overview["runs"][0]["id"] == RUN
    assert overview["runs"][0]["stopWords"].startswith("the next step happens off the site")
    run = c.get(f"/api/victim/{RUN}").json()
    assert run["status"] == "finished" and run["startHost"] == "offer.example"
    assert run["steps"][0]["screenshot"] == "run_files/steps/step-001.png"
    video = c.get(
        f"/api/victim/{RUN}/file/run_files/recording/clip-01.webm", headers={"Range": "bytes=0-9"}
    )
    assert video.status_code == 206 and video.headers["content-type"] == "video/webm"


def test_only_the_video_captions_and_screenshots_are_served(tmp_path, monkeypatch):
    files = finished_run(tmp_path)
    (files / "forms.jsonl").write_text("{}")
    c, _ = client(tmp_path, monkeypatch)
    for rel in ("run_files/run.json", "run_files/forms.jsonl", "run_files/session.har"):
        assert c.get(f"/api/victim/{RUN}/file/{rel}").status_code == 404
    assert c.get("/api/victim/victim-not-a-run").status_code == 404


def test_a_live_site_needs_the_approval_and_a_screen(tmp_path, monkeypatch):
    c, _ = client(tmp_path, monkeypatch)
    refused = c.post("/api/victim/start", json={"url": "https://offer.example/"})
    assert refused.status_code == 400 and "approved" in refused.json()["detail"]
    assert (
        c.post(
            "/api/victim/start", json={"url": "x", "persona": "nobody", "approved": True}
        ).status_code
        == 400
    )
    c, _ = client(tmp_path, monkeypatch, docker=True)
    docker = c.post("/api/victim/start", json={"url": "https://offer.example/", "approved": True})
    assert docker.status_code == 409 and "ftf victim run" in docker.json()["detail"]


def test_one_run_at_a_time(tmp_path, monkeypatch):
    files = tmp_path / "runs" / RUN / "run_files"
    files.mkdir(parents=True)
    (files / "live.json").write_text(json.dumps({"status": "running"}))
    c, _ = client(tmp_path, monkeypatch)
    busy = c.post("/api/victim/start", json={"url": "https://offer.example/", "approved": True})
    assert busy.status_code == 409


def test_a_live_run_is_started_with_live_and_reported(tmp_path, monkeypatch):
    started = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            started.append(cmd)
            (tmp_path / "runs" / RUN / "run_files").mkdir(parents=True)

    monkeypatch.setattr(web.subprocess, "Popen", FakePopen)
    c, changed = client(tmp_path, monkeypatch)
    out = c.post(
        "/api/victim/start",
        json={"url": "offer.example/x", "persona": "small-saver", "approved": True, "quick": True},
    ).json()
    assert out == {"id": RUN}
    cmd = started[0]
    assert cmd[cmd.index("run") + 1] == "https://offer.example/x"
    assert {"--live", "--demo"} <= set(cmd) and cmd[cmd.index("--persona") + 1] == "small-saver"
    # When it finishes, the Library is told to reload, once.
    (tmp_path / "runs" / RUN / "run_files" / "run.json").write_text("{}")
    (tmp_path / "runs" / RUN / "run_files" / "run.json").write_text(json.dumps({"steps": 1}))
    c.get(f"/api/victim/{RUN}")
    c.get("/api/victim")
    assert changed == [1]
