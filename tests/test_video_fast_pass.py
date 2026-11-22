import hashlib
import json

from ftf import pipeline
from ftf.contracts import Incident, Purpose, View, ViewKind


def test_fast_video_pass_has_identity_metadata_and_supplied_text_without_decoding(
    store, tmp_path, monkeypatch
):
    content = b"exact video file bytes"
    (tmp_path / "clip.mp4").write_bytes(content)
    (tmp_path / "clip.json").write_text(
        json.dumps({"caption": "An advertised offer", "access_token": "never a caption"})
    )
    store.put_incident(
        Incident(incident_id="one", root=str(tmp_path)),
        [],
        [
            View(
                view_id="v",
                incident_id="one",
                kind=ViewKind.VIDEO,
                label="Video",
                primary="clip.mp4",
                attachments=["clip.json"],
            ),
        ],
    )

    def no_decode(*args, **kwargs):
        raise AssertionError("The automatic pass must not decode video")

    monkeypatch.setattr("ftf.extract.video_media.read", no_decode)
    pipeline.run_extract(store, ["v"], fast=True)
    rows = store.observations("v")
    identity = next(o for o in rows if o.namespace == "asset.sha256.content")
    assert identity.value == hashlib.sha256(content).hexdigest()
    assert identity.purpose is Purpose.LINKAGE
    assert any(o.namespace == "video.file_size_bytes" and o.value == len(content) for o in rows)
    assert [o.value for o in rows if o.namespace == "video.sidecar_text"] == ["An advertised offer"]
    assert not any(o.extractor in ("video.media", "perception.video") for o in rows)
    pipeline.run_fingerprint(store, ["v"], local_only=True)
    assert store.fingerprints(view_id="v", layer_id="asset.exact")
