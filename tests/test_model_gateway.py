"""Model gateway tests. Fake clients only: nothing here touches the network."""

import json
import math
import os
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import httpx2
import openai
import pytest
from google import genai
from pydantic import BaseModel, ConfigDict, Field

from ftf.config import Settings
from ftf.contracts import State
from ftf.models import (
    EmbedTask,
    chunk_text,
    embed,
    gemini,
    read_structured,
    read_video,
    redact_json,
)
from ftf.models.gemini import CHARS_PER_TOKEN, CHUNK_TOKENS, EMBEDDING_DIMENSIONS
from ftf.models.redact import hash_secret, is_secret_name, redact_text

from ._fakes import FakeOpenAI


class Answer(BaseModel):
    kind: str


def _message(text='{"kind": "news"}', stop_reason="end_turn"):
    part = (
        NS(type="refusal", refusal="no")
        if stop_reason == "refusal"
        else NS(type="output_text", text=text)
    )
    status = "incomplete" if stop_reason == "max_tokens" else "completed"
    return NS(status=status, output=[NS(type="message", phase=None, content=[part])])


class FakeEmbeddings:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.models = NS(embed_content=self._embed)

    def _embed(self, model, contents, config):
        self.calls.append(contents)
        if self.fail_on and self.fail_on in contents:
            raise httpx2.ReadTimeout("slow")
        axis = len(self.calls) % EMBEDDING_DIMENSIONS
        values = [0.0] * EMBEDDING_DIMENSIONS
        values[axis] = 1.0
        return NS(embeddings=[NS(values=values)])


class FakeVideo:
    def __init__(self, interaction):
        self.interaction = interaction
        self.deleted = []
        self.uploads = 0
        self.files = NS(upload=self._upload, get=None, delete=self._delete)
        self.interactions = NS(create=self._create)

    def _upload(self, file, config=None):
        self.uploads += 1
        self.upload_config = config
        return NS(name="files/1", uri="uri", mime_type="video/mp4", state=NS(name="ACTIVE"))

    def _delete(self, name):
        self.deleted.append(name)

    def _create(self, **request):
        self.request = request
        return self.interaction


def _call(client, settings, content="tekst"):
    return read_structured(
        "kind", "v1", "system", content, Answer, settings=settings, client=client
    )


def _cache_text(settings) -> str:
    files = Path(settings.model_cache_dir).rglob("*.json")
    return "".join(f.read_text(encoding="utf-8") for f in files)


def _cache_count(settings) -> int:
    return len(list(Path(settings.model_cache_dir).rglob("*.json")))


def test_second_identical_call_makes_no_request(settings):
    client = FakeOpenAI(_message())
    first = _call(client, settings)
    second = _call(client, settings)
    assert first.state is State.PRESENT and first.value == Answer(kind="news")
    assert second.value == first.value
    assert len(client.requests) == 1


def test_refusal_is_not_observable_and_cached(settings):
    client = FakeOpenAI(_message(text='{"ki', stop_reason="refusal"))
    assert _call(client, settings).reason == "refusal"
    second = _call(client, settings)
    assert second.state is State.NOT_OBSERVABLE and second.value is None
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (_message(text="not json"), "schema"),
        (_message(text='{"wrong": 1}'), "schema"),
        (_message(text='{"kind"', stop_reason="max_tokens"), "schema"),
        (openai.APITimeoutError(request=httpx2.Request("POST", "https://x.test")), "timeout"),
    ],
)
def test_transient_failure_is_not_cached_and_retries(settings, bad, reason):
    client = FakeOpenAI(bad, _message())
    failed = _call(client, settings)
    assert failed.state is State.NOT_OBSERVABLE and failed.reason == reason
    assert _cache_count(settings) == 0
    assert _call(client, settings).value == Answer(kind="news")
    assert len(client.requests) == 2


def test_missing_key_is_not_observable_but_cache_still_serves(settings):
    assert _call(None, settings).reason == "no_key"
    assert _cache_count(settings) == 0
    _call(FakeOpenAI(_message()), settings)
    assert _call(None, settings).value == Answer(kind="news")


def test_secrets_never_reach_the_model_or_the_cache(settings):
    client = FakeOpenAI(_message())
    _call(client, settings, content="https://x.pl/?access_token=SECRET123&pxl=998877")
    sent = json.dumps(client.requests[0]["input"])
    assert "SECRET123" not in sent and "998877" in sent
    assert "SECRET123" not in _cache_text(settings)


def test_embed_returns_a_vector_with_the_task_prefix(settings):
    client = FakeEmbeddings()
    [result] = embed(["text"], settings=settings, client=client)
    assert len(result.value.vector) == EMBEDDING_DIMENSIONS
    assert client.calls == ["task: sentence similarity | query: text"]
    embed(["text"], settings=settings, client=client)
    assert len(client.calls) == 1


def test_tasks_do_not_share_cache_entries(settings):
    client = FakeEmbeddings()
    embed(["text"], EmbedTask.SIMILARITY, settings=settings, client=client)
    embed(["text"], EmbedTask.CLUSTERING, settings=settings, client=client)
    assert client.calls[1] == "task: clustering | query: text"


def test_long_text_is_chunked_averaged_and_normalised(settings):
    text = "słowo " * 12000
    pieces = chunk_text(text)
    assert len(pieces) > 1
    assert all(len(p) <= CHUNK_TOKENS * CHARS_PER_TOKEN for p in pieces)
    client = FakeEmbeddings()
    [result] = embed([text], settings=settings, client=client)
    assert len(result.value.chunks) == len(pieces)
    assert math.isclose(math.sqrt(sum(v * v for v in result.value.vector)), 1.0)


def test_one_failed_text_does_not_lose_the_others(settings):
    results = embed(["good", "bad"], settings=settings, client=FakeEmbeddings(fail_on="bad"))
    assert results[0].state is State.PRESENT
    assert results[1].state is State.NOT_OBSERVABLE and results[1].reason == "timeout"


def test_embed_empty_text_makes_no_call(settings):
    client = FakeEmbeddings()
    [result] = embed(["  "], settings=settings, client=client)
    assert result.reason == "empty" and client.calls == []


def test_video_is_cached_and_upload_deleted(settings, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"video")
    client = FakeVideo(NS(status="completed", output_text='{"kind": "ad"}'))
    args = ("video", "v1", video, "opisz", Answer)
    assert read_video(*args, settings=settings, client=client).value == Answer(kind="ad")
    assert read_video(*args, settings=settings, client=client).value == Answer(kind="ad")
    assert client.uploads == 1 and client.deleted == ["files/1"]
    assert client.request["input"][0]["type"] == "video"


def test_failed_video_is_not_cached_and_upload_deleted(settings, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"video")
    client = FakeVideo(NS(status="failed", output_text=None))
    result = read_video("video", "v1", video, "opisz", Answer, settings=settings, client=client)
    assert result.state is State.NOT_OBSERVABLE and result.reason == "error"
    assert _cache_count(settings) == 0 and client.deleted == ["files/1"]


@pytest.mark.parametrize(
    "name", ["auth_token", "sessionId", "PHPSESSID", "csrf-token", "Cookie", "password"]
)
def test_secret_names(name):
    assert is_secret_name(name)


@pytest.mark.parametrize("name", ["author", "authority", "fb_pixel", "gclid", "tokenize"])
def test_non_secret_names(name):
    assert not is_secret_name(name)


def test_redact_text_covers_every_location():
    text = (
        "Authorization: Bearer abc.def.ghi\n"
        "Cookie: a=1; b=2\n"
        '<input type="hidden" name="csrf_token" value="FORMSECRET">\n'
        '{"session_id": "JSONSECRET", "author": "Jan"}\n'
        "https://x.pl/a?token=URLSECRET&author=Jan"
    )
    out = redact_text(text)
    for secret in ("abc.def.ghi", "a=1", "FORMSECRET", "JSONSECRET", "URLSECRET"):
        assert secret not in out
    assert out.count("Jan") == 2


def test_redact_json_handles_har_entries_and_keeps_hashes_equal():
    data = {
        "headers": [{"name": "Cookie", "value": "S1"}, {"name": "Accept", "value": "*/*"}],
        "token": "S1",
        "author": "Jan",
    }
    out = redact_json(data)
    assert out["headers"][0]["value"] == out["token"] == hash_secret("S1")
    assert out["headers"][1]["value"] == "*/*" and out["author"] == "Jan"


def test_redaction_is_idempotent():
    once = redact_text("https://x.pl/?token=abc")
    assert redact_text(once) == once


@pytest.mark.parametrize(
    "text",
    [
        "https://x.pl/?api_key=LEAK",
        "client_secret=LEAK",
        "X-Api-Key: LEAK",
        "APIToken=LEAK",
        "curl -H 'Cookie: sid=LEAK' https://x.pl",
        "https://admin:LEAK@x.pl/",
        '<meta name="csrf-token" content="LEAK">',
        'var token = "LEAK";',
        '{csrfToken: "LEAK"}',
        '<div data-token="LEAK">',
        '{\\"token\\":\\"LEAK\\"}',
        '{"token": "abc\\"LEAK"}',
        '<input data-name="x" name="csrf_token" value="LEAK">',
        '<input name="csrf_token" data-value="x" value="LEAK">',
        '<input placeholder="a>b" name="csrf_token" value="LEAK">',
    ],
)
def test_review_secret_locations_are_redacted(text):
    assert "LEAK" not in redact_text(text)


def test_ordinary_page_text_is_left_alone():
    text = '<a href="/x?pxl=123&author=Jan" title="Autor">Sesja: wiosna</a> {tokens: count()}'
    assert redact_text(text) == text


def test_secret_in_a_json_key_is_redacted():
    out = redact_json({"https://x/?token=LEAK": 1, "token": {"https://y/?auth=LEAK": "v"}})
    assert "LEAK" not in json.dumps(out)


def test_same_cookie_hashes_equally_across_line_endings():
    crlf = redact_text("Cookie: a=1\r\nAccept: x")
    lf = redact_text("Cookie: a=1\nAccept: x")
    assert crlf.replace("\r", "") == lf
    assert "\r\n" in crlf


@pytest.mark.parametrize(
    "text",
    [
        "[" * 100_000,
        "Cookie:" + " " * 40_000 + "x" + " " * 40_000 + "y",
        "<input " * 20_000,
        '<a b="' * 20_000,
        "a" * 200_000 + "=",
        'token: "' + "\\" * 100_000,
    ],
)
def test_redaction_stays_fast_on_hostile_input(text):
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


def test_damaged_cache_entry_is_a_miss(settings):
    _call(FakeOpenAI(_message()), settings)
    [entry] = Path(settings.model_cache_dir).rglob("*.json")
    entry.write_text('{"state": "pres', encoding="utf-8")
    client = FakeOpenAI(_message())
    assert _call(client, settings).value == Answer(kind="news")
    assert len(client.requests) == 1
    assert json.loads(entry.read_text(encoding="utf-8"))["state"] == "present"


def test_unwritable_cache_still_returns_the_paid_answer(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    broken = Settings(blocked, "", "", "openai-test", "embed-test", "video-test")
    assert _call(FakeOpenAI(_message()), broken).value == Answer(kind="news")


class Aliased(BaseModel):
    kind: str = Field(alias="type")


class StrictDate(BaseModel):
    model_config = ConfigDict(strict=True)
    day: date


@pytest.mark.parametrize(
    ("schema", "text", "expected"),
    [
        (Aliased, '{"type": "x"}', Aliased(type="x")),
        (StrictDate, '{"day": "2026-11-21"}', StrictDate(day=date(2026, 11, 21))),
    ],
)
def test_alias_and_strict_schemas_survive_the_cache(settings, schema, text, expected):
    client = FakeOpenAI(_message(text=text))
    for _ in range(2):
        result = read_structured("k", "v1", "s", "c", schema, settings=settings, client=client)
        assert result.value == expected
    assert len(client.requests) == 1


def test_timeout_is_not_retried_but_overload_is(settings, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    request = httpx2.Request("POST", "https://x.test")
    client = FakeOpenAI(httpx2.ReadTimeout("silent", request=request))
    assert _call(client, settings).reason == "timeout"
    assert len(client.requests) == 1

    overloaded = openai.InternalServerError(
        "busy", response=httpx2.Response(529, request=request), body=None
    )
    client = FakeOpenAI(overloaded, _message())
    assert _call(client, settings).value == Answer(kind="news")
    assert len(client.requests) == 2


def test_whitespace_runs_make_no_empty_chunks():
    assert chunk_text("a" + " " * 50_000 + "b") == ["a b"]


@pytest.mark.parametrize(
    "text",
    [
        '<a href="https://x.pl/?token=LEAK">',
        '{"url": "https://x.pl/?access_token=LEAK"}',
        "https://x.pl/?next=/a?token=LEAK",
        "?u=https%3A%2F%2Fy.pl%3Ftoken%3DLEAK",
        '<input name="csrf_token" value="LEAK" data-x="' + "y" * 9000 + '">',
        '<input name="csrf_token" value="LEAK" "x">',
        "secret=LEAK",
        "pwd=LEAK",
        "pass=LEAK",
        "jwt=LEAK",
        "private_key=LEAK",
        "aws_secret_access_key=LEAK",
        "refreshtoken=LEAK",
        "token2=LEAK",
        '<input type="password" name="pw" value="LEAK">',
        "curl -H 'X-Api-Key: LEAK' https://x.pl",
        "> X-Auth-Token: LEAK",
        '{"tokens": ["LEAK"]}',
        "localStorage['token'] = 'LEAK';",
        'localStorage.setItem("auth_token", "LEAK");',
    ],
)
def test_second_review_secret_locations_are_redacted(text):
    assert "LEAK" not in redact_text(text)


def test_numeric_session_id_is_redacted_and_json_stays_valid():
    out = redact_text('{"session_id": 987654321, "price": 199}')
    assert "987654321" not in out
    assert json.loads(out)["price"] == 199


def test_url_structure_and_linkage_ids_survive():
    out = redact_text('<a href="https://x.pl/l?pxl=123&token=LEAK&gclid=abc">')
    assert "LEAK" not in out and "https://x.pl/l?pxl=123&token=" in out and "gclid=abc" in out


@pytest.mark.parametrize(
    "text",
    [
        "a=" * 100_000,
        "token=" * 30_000,
        "%26token%3D" * 15_000,
        "<a " + '"' * 100_000,
        "<a " + " " * 100_000 + '"',
        "setItem('token','" * 10_000,
    ],
)
def test_new_passes_stay_fast_on_hostile_input(text):
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0


def test_lone_surrogate_does_not_crash(settings):
    client = FakeOpenAI(_message())
    assert _call(client, settings, content="bad \ud83d text").value == Answer(kind="news")
    assert "\ud83d" not in json.dumps(client.requests[0]["input"], ensure_ascii=False)
    results = embed(["ok", "bad \ud83d text"], settings=settings, client=FakeEmbeddings())
    assert all(r.state is State.PRESENT for r in results)


@pytest.mark.parametrize(
    "error",
    [AssertionError("order"), RuntimeError("state"), ValueError("json"), httpx2.DecodingError("x")],
)
def test_broken_stream_is_not_observable(settings, error):
    result = _call(FakeOpenAI(error), settings)
    assert result.state is State.NOT_OBSERVABLE and result.reason == "error"
    assert _cache_count(settings) == 0


def test_cache_files_honour_the_umask(settings):
    previous = os.umask(0o022)
    try:
        _call(FakeOpenAI(_message()), settings)
    finally:
        os.umask(previous)
    [entry] = Path(settings.model_cache_dir).rglob("*.json")
    assert entry.stat().st_mode & 0o777 == 0o644


def _make_video(path):
    command = "ffmpeg -nostdin -v error -f lavfi -i testsrc=size=65x48:rate=5:duration=1"
    command += " -f lavfi -i sine=duration=1 -c:v libx264 -c:a aac -shortest -y"
    subprocess.run([*command.split(), str(path)], check=True, timeout=30)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("suffix", [".mkv", ".m4v"])
def test_mkv_and_m4v_are_converted_to_mp4(settings, tmp_path, suffix):
    video = tmp_path / f"a{suffix}"
    _make_video(video)
    seen = {}
    client = FakeVideo(NS(status="completed", output_text='{"kind": "ad"}'))
    upload = client.files.upload

    def inspect_upload(file, config=None):
        seen.update(path=Path(file), head=Path(file).read_bytes()[4:8])
        return upload(file, config)

    client.files.upload = inspect_upload
    args = ("video", "v1", video, "opisz", Answer)
    assert read_video(*args, settings=settings, client=client).value == Answer(kind="ad")
    assert seen["path"].suffix == ".mp4" and seen["head"] == b"ftyp"
    assert client.upload_config == {"mime_type": "video/mp4"}
    assert not seen["path"].exists()
    assert read_video(*args, settings=settings, client=client).value == Answer(kind="ad")
    assert client.uploads == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_broken_video_is_not_uploaded_or_cached(settings, tmp_path):
    video = tmp_path / "a.mkv"
    video.write_bytes(b"not a video")
    client = FakeVideo(NS(status="completed", output_text='{"kind": "ad"}'))
    result = read_video("video", "v1", video, "opisz", Answer, settings=settings, client=client)
    assert result.reason == "error" and client.uploads == 0 and _cache_count(settings) == 0


def test_other_video_format_is_not_uploaded(settings, tmp_path):
    video = tmp_path / "a.avi"
    video.write_bytes(b"video")
    client = FakeVideo(NS(status="completed", output_text='{"kind": "ad"}'))
    result = read_video("video", "v1", video, "opisz", Answer, settings=settings, client=client)
    assert result.reason == "unsupported" and client.uploads == 0


def test_video_upload_sends_the_documented_mime_type(settings, tmp_path):
    video = tmp_path / "a.MOV"
    video.write_bytes(b"video")
    client = FakeVideo(NS(status="completed", output_text='{"kind": "ad"}'))
    read_video("video", "v1", video, "opisz", Answer, settings=settings, client=client)
    assert client.upload_config == {"mime_type": "video/mov"}


def test_video_client_makes_one_attempt(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: seen.update(kw) or NS())
    keyed = Settings(settings.data_dir, "", "key", "c", "e", "v")
    gemini._make_client(keyed, 1000, 1)
    assert seen["http_options"].retry_options.attempts == 1


def test_polish_video_name_is_uploaded_under_an_ascii_name(tmp_path):
    from ftf.models.gemini import _as_uploadable

    video = tmp_path / "Czytać [1].mp4"
    video.write_bytes(b"video")
    with _as_uploadable(video) as (path, mime_type):
        assert path.name.isascii() and mime_type == "video/mp4"
        assert path.read_bytes() == b"video"
