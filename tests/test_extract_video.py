"""HAC-19: video key frames and container metadata (M1, M5), HLS playlists (M7, C12)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from ftf.contracts import Purpose, State, View, ViewKind
from ftf.extract import _material, video_media
from ftf.extract.video_media import VideoMedia, container_facts
from ftf.extract.video_stream import VideoStream, shape, zone_id
from ftf.ingest import ingest_folder
from ftf.registry import Context, comparators, layers, load_plugins

from .test_extract_identifiers import page, values

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def _fresh_cache():
    _material.clear_cache()


def make_video(path: Path, *sources: str, extra: tuple[str, ...] = ()) -> Path:
    """A video made of lavfi sources played one after another, 3 s each."""
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for src in sources:
        inputs += ["-f", "lavfi", "-t", "3", "-i", f"{src}=size=320x240:rate=10"]
    chain = "".join(f"[{i}:v]" for i in range(len(sources)))
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", *inputs,
        "-filter_complex", f"{chain}concat=n={len(sources)}:v=1:a=0[v]", "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", *extra, str(path),
    ]  # fmt: skip
    subprocess.run(command, check=True)
    return path


def reencode(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-vf", "scale=240:-2", "-c:v", "libx264", "-crf", "35", str(target),
    ]  # fmt: skip
    subprocess.run(command, check=True)
    return target


def video_view(store, folder: Path) -> tuple[View, Context]:
    _, views = ingest_folder(folder, store)
    (view,) = [v for v in views if v.kind is ViewKind.VIDEO]
    return view, Context(store)


def frames(observations) -> list[str]:
    return values(observations, "video.frame_phash")


def fingerprint(view, observations):
    load_plugins()
    return layers.get("media.video").build(view, observations)


# --- M1, M5 on synthetic video --------------------------------------------------------------


@ffmpeg
def test_key_frames_and_metadata(store, tmp_path):
    clip = make_video(tmp_path / "case" / "ad.mp4", "testsrc", "smptebars", "mandelbrot")
    view, ctx = video_view(store, tmp_path / "case")
    out = VideoMedia().run(view, ctx)
    hashes = frames(out)
    assert len(hashes) >= 3  # one per scene at least
    assert all(len(h) == 16 for h in hashes)
    at = [o.raw for o in out if o.namespace == "video.frame_phash"]
    assert at[0] == "0.00s" and all(a.endswith("s") for a in at)
    assert values(out, "video.resolution") == ["320x240"]
    assert values(out, "video.codec") == ["h264"]
    assert values(out, "video.duration") == [9.0]
    assert values(out, "video.fps") == [10.0]
    assert any(e.startswith("Lavf") for e in values(out, "video.encoder"))
    assert {o.signal_id for o in out} == {"M1", "M5"}
    assert clip.is_file()


@ffmpeg
def test_reencoded_copy_matches_and_another_video_does_not(store, tmp_path):
    # rgbtestsrc's thin bands do not survive a 240-px CRF-35 re-encode; natural pictures do
    original = make_video(tmp_path / "a" / "ad.mp4", "testsrc", "smptebars", "mandelbrot")
    reencode(original, tmp_path / "b" / "ad-small.mp4")
    make_video(tmp_path / "c" / "other.mp4", "life", "cellauto", "testsrc2")
    prints = {}
    for name in "abc":
        view, ctx = video_view(store, tmp_path / name)
        prints[name] = fingerprint(view, VideoMedia().run(view, ctx))
    phash = comparators.get("phash")
    assert phash.compare(prints["a"], prints["b"]).score == 1.0
    assert phash.compare(prints["a"], prints["c"]).score == 0.0


@ffmpeg
def test_single_shot_video_still_gets_a_fingerprint(store, tmp_path):
    """A talking head samples to near-identical frames; they are kept, not collapsed to one."""
    make_video(tmp_path / "case" / "head.mp4", "testsrc", extra=("-t", "3"))
    view, ctx = video_view(store, tmp_path / "case")
    assert fingerprint(view, VideoMedia().run(view, ctx)) is not None


def test_without_ffmpeg_the_view_says_why(store, tmp_path, monkeypatch):
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "ad.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr(video_media.shutil, "which", lambda name: None)
    view, ctx = video_view(store, folder)
    out = VideoMedia().run(view, ctx)
    assert {o.namespace for o in out} == {"video.frame_phash", "video.encoder"}
    assert all(o.state is State.NOT_OBSERVABLE and "not installed" in o.raw for o in out)


@ffmpeg
def test_a_broken_file_is_not_observable(store, tmp_path):
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "ad.mp4").write_bytes(b"not a video at all" * 100)
    view, ctx = video_view(store, folder)
    out = VideoMedia().run(view, ctx)
    assert out and all(o.state is State.NOT_OBSERVABLE for o in out)


def test_container_facts_from_a_phone_screen_recording():
    info = {
        "format": {
            "duration": "249.160000",
            "tags": {"major_brand": "mp42", "creation_time": "2026-08-12T06:38:33.000000Z"},
        },
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "width": 1170, "height": 2532,
             "avg_frame_rate": "10800/249", "tags": {"handler_name": "Core Media Video"}},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }  # fmt: skip
    facts = {ns: value for ns, value, _ in container_facts(info)}
    assert facts == {
        "video.handler": "Core Media Video",
        "video.codec": "hevc",
        "video.container": "mp42",
        "video.duration": 249.2,
        "video.resolution": "1170x2532",
        "video.fps": 43.37,
        "video.created_at": "2026-08-12T06:38:33.000000Z",
    }


@ffmpeg
def test_container_metadata_never_drives_grouping(store, tmp_path):
    make_video(tmp_path / "case" / "ad.mp4", "testsrc")
    view, ctx = video_view(store, tmp_path / "case")
    out = VideoMedia().run(view, ctx)
    m5 = [o for o in out if o.signal_id == "M5"]
    assert m5 and all(o.purpose is Purpose.RISK for o in m5)
    assert all(o.purpose is Purpose.LINKAGE for o in out if o.signal_id == "M1")


@ffmpeg
def test_frames_lost_are_said_even_when_metadata_was_read(store, tmp_path, monkeypatch):
    make_video(tmp_path / "case" / "ad.mp4", "testsrc")
    view, ctx = video_view(store, tmp_path / "case")
    monkeypatch.setattr(video_media, "key_frames", lambda path, duration: [])
    out = VideoMedia().run(view, ctx)
    assert values(out, "video.codec") == ["h264"]
    (missing,) = [o for o in out if o.namespace == "video.frame_phash"]
    assert missing.state is State.NOT_OBSERVABLE and missing.raw == "no frame decoded"


def test_an_ffmpeg_that_cannot_run_does_not_stop_the_run(store, tmp_path, monkeypatch):
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "ad.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr(video_media.shutil, "which", lambda name: "/usr/bin/" + name)

    def refused(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(video_media.subprocess, "run", refused)
    view, ctx = video_view(store, folder)
    out = VideoMedia().run(view, ctx)
    assert out and all(o.state is State.NOT_OBSERVABLE for o in out)
    assert "Permission denied" in out[0].raw


@ffmpeg
def test_non_utf8_tags_keep_the_frames(store, tmp_path):
    clip = make_video(tmp_path / "src.mp4", "testsrc", "smptebars")
    folder = tmp_path / "case"
    folder.mkdir()
    command = [b"ffmpeg", b"-nostdin", b"-v", b"error", b"-y", b"-i", str(clip).encode(),
               b"-c", b"copy", b"-metadata", b"title=\xe9t\xe9 latin-1",
               str(folder / "ad.mp4").encode()]  # fmt: skip
    subprocess.run(command, check=True)
    view, ctx = video_view(store, folder)
    out = VideoMedia().run(view, ctx)
    assert len(frames(out)) >= 2 and values(out, "video.codec") == ["h264"]


@ffmpeg
def test_frames_written_before_ffmpeg_failed_are_kept(store, tmp_path, monkeypatch):
    make_video(tmp_path / "case" / "ad.mp4", "testsrc", "smptebars", "mandelbrot")
    view, ctx = video_view(store, tmp_path / "case")
    real = video_media._run

    def damaged(command, **kwargs):
        done = real(command, **kwargs)
        if command[0] == "ffmpeg":  # frames are on disk; the file "broke" near the end
            raise subprocess.CalledProcessError(1, command, done.stdout, done.stderr)
        return done

    monkeypatch.setattr(video_media, "_run", damaged)
    out = VideoMedia().run(view, ctx)
    assert len(frames(out)) >= 3
    assert all(o.raw.endswith("s") and o.raw != "?" for o in out if o.signal_id == "M1")


def test_frame_times_pair_by_index_and_allow_a_negative_start():
    log = (
        "[Parsed_showinfo_2 @ 0x1] n:   0 pts:  -1001 pts_time:-0.0333667 duration:1001\n"
        "[Parsed_showinfo_2 @ 0x1] config in time_base: 1/30000\n"
        "[Parsed_showinfo_2 @ 0x1] n:   1 pts:  60060 pts_time:2.002   duration:1001\n"
        "[Parsed_showinfo_2 @ 0x1] n:   2 pts:  1e+06 pts_time:3.33667e+01 duration:1001\n"
    )
    found = {int(n): float(t) for n, t in video_media._SHOWINFO.findall(log)}
    assert found == {0: -0.0333667, 1: 2.002, 2: 33.3667}


def test_no_layer_links_on_container_metadata():
    """M5 is evidence to read: an encoder or a resolution is shared by unrelated videos."""
    load_plugins()
    read = {ns for layer in layers.all() for ns in getattr(layer, "namespaces", ())}
    for ns in ("video.encoder", "video.handler", "video.codec", "video.container",
               "video.duration", "video.resolution", "video.fps", "video.created_at",
               "video.hls_shape", "video.hls_url"):  # fmt: skip
        assert ns not in read, ns


# --- M7: HLS --------------------------------------------------------------------------------

MASTER = (
    "#EXTM3U\n#EXT-X-VERSION:3\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=1412837,RESOLUTION=360x640,CODECS="avc1.64001e,mp4a.40.2"\n'
    "360p/video.m3u8\n"
)
MEDIA = (
    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
    "#EXTINF:4.000000,\nvideo0.ts\n#EXTINF:4.000000,\nvideo1.ts\n#EXT-X-ENDLIST\n"
)
LIBRARY = "https://vz-6110f311-389.b-cdn.net/30d2cd25-51cb-46d9-be2a-ce87f7bf4be0"


def test_playlist_shape():
    assert shape(MASTER) == ("master;v3;variants=360x640", ["360p/video.m3u8"])
    assert shape(MEDIA) == ("media;v3;target=4;segments=video999.ts", ["video0.ts", "video1.ts"])
    assert shape("<html>not a playlist</html>") == (None, [])


def test_zone_id_only_for_a_library_host():
    assert zone_id("vz-6110f311-389.b-cdn.net") == "vz-6110f311-389"
    assert zone_id("cdn.b-cdn.net") is None
    assert zone_id("eu-vi-stream.org") is None


def _har_entry(url: str, body: str) -> dict:
    return {
        "request": {"url": url, "method": "GET"},
        "response": {
            "status": 200,
            "content": {"mimeType": "application/vnd.apple.mpegurl", "text": body},
        },
    }


def test_hls_from_the_har(store, tmp_path):
    from .test_extract_identifiers import run

    har = [_har_entry(f"{LIBRARY}/playlist.m3u8", MASTER),
           _har_entry(f"{LIBRARY}/360p/video.m3u8", MEDIA)]  # fmt: skip
    out = run(VideoStream(), store, page(tmp_path / "p", har=har))
    assert values(out, "cdn.zone_id") == ["vz-6110f311-389"]
    # the library's host is an account, kept as the zone; b-cdn.net itself is every
    # customer's and would link unrelated pages through infra.host
    assert values(out, "video.hls_host") == []
    assert values(out, "video.hls_shape") == [
        "master;v3;variants=360x640",
        "media;v3;target=4;segments=video999.ts",
    ]
    assert values(out, "video.hls_url") == [
        f"{LIBRARY}/playlist.m3u8",
        f"{LIBRARY}/360p/video.m3u8",
    ]


def test_hls_from_html_and_a_saved_playlist(store, tmp_path):
    from .test_extract_identifiers import run

    body = (
        '<video id="v"></video><script>player.load('
        '"https:\\/\\/eu-vi-stream.org\\/videos\\/01kw\\/output.m3u8")</script>'
    )
    saved = "#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\noutput_000.ts\n"
    root = page(tmp_path / "p", body=body, files={"output_0128ef29.m3u8": saved})
    out = run(VideoStream(), store, root)
    assert values(out, "video.hls_host") == ["eu-vi-stream.org"]
    assert values(out, "video.hls_url") == ["https://eu-vi-stream.org/videos/01kw/output.m3u8"]
    assert values(out, "video.hls_shape") == ["media;v6;target=10;segments=output_999.ts"]
    assert not values(out, "cdn.zone_id")


def test_a_playlist_with_a_bom_or_in_base64_is_read(store, tmp_path):
    import base64

    from .test_extract_identifiers import run

    entry = _har_entry("https://media.example-stream.net/v/1/playlist.m3u8", "")
    entry["response"]["content"].update(
        text=base64.b64encode(MEDIA.encode()).decode(), encoding="base64"
    )
    saved = "\ufeff" + MEDIA.replace("video", "clip")
    root = page(tmp_path / "p", har=[entry], files={"saved.m3u8": saved})
    out = run(VideoStream(), store, root)
    assert values(out, "video.hls_shape") == [
        "media;v3;target=4;segments=video999.ts",
        "media;v3;target=4;segments=clip999.ts",
    ]


def test_hls_host_links_weakly_through_infra(store, tmp_path):
    load_plugins()
    infra = layers.get("infra.host")
    assert "video.hls_host" in infra.namespaces
    from ftf.compare.weights import BASE_WEIGHTS

    assert BASE_WEIGHTS["video.hls_host"] <= 0.4
    from ftf.compare.stoplist import HOST_NAMESPACES

    assert "video.hls_host" in HOST_NAMESPACES  # youtube.com and the like never link


def test_page_without_video_gets_nothing(store, tmp_path):
    from .test_extract_identifiers import run

    assert run(VideoStream(), store, page(tmp_path / "p", body="<p>text</p>", har=[])) == []


def test_a_broken_har_entry_is_skipped(store, tmp_path):
    from .test_extract_identifiers import run

    har = [{"request": {"url": f"{LIBRARY}/x.m3u8"}, "response": None},
           _har_entry(f"{LIBRARY}/playlist.m3u8", MASTER)]  # fmt: skip
    out = run(VideoStream(), store, page(tmp_path / "p", har=har))
    assert "vz-6110f311-389" in values(out, "cdn.zone_id")


# --- Done when: the three sample videos --------------------------------------------------------


@ffmpeg
def test_hac19_done_when(sample_store):
    s = sample_store
    videos = {v.incident_id: v for v in s.views() if v.kind is ViewKind.VIDEO}
    assert set(videos) == {"5", "14", "47"}
    for incident, view in videos.items():
        hashes = [o for o in s.observations(view.view_id) if o.namespace == "video.frame_phash"]
        assert len(hashes) >= 2, incident
        assert all(o.state is State.PRESENT for o in hashes)
        prints = {f.layer_id for f in s.fingerprints(view_id=view.view_id)}
        assert "media.video" in prints, incident
    zone = [
        o.value
        for v in s.views("47")
        for o in s.observations(v.view_id)
        if o.namespace == "cdn.zone_id"
    ]
    assert zone == ["vz-6110f311-389"]
    hosts = {
        o.value
        for v in s.views("14")
        for o in s.observations(v.view_id)
        if o.namespace == "video.hls_host"
    }
    assert hosts == {"eu-vi-stream.org"}
