"""A small client for a private CompreFace service. Standard library only."""

from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from ..config import get_settings

TIMEOUT_SECONDS = 60
OAUTH_CLIENT = "CommonClientId:password"  # CompreFace's fixed public client id
MODEL_NAME = "ftf-reference"


class FaceServiceError(Exception):
    """The service could not answer. `reason` is safe to store."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def service_url() -> str | None:
    return os.environ.get("FTF_COMPREFACE_URL", "").rstrip("/") or None


def state_path() -> Path:
    return get_settings().data_dir / "faces" / "compreface.json"


def _multipart(data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="image.jpg"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return (
        head + data + f"\r\n--{boundary}--\r\n".encode(),
        f"multipart/form-data; boundary={boundary}",
    )


def _send(url: str, method: str, body: bytes | None, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        raise FaceServiceError(f"http_{exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FaceServiceError("unreachable") from exc
    try:
        return json.loads(raw) if raw else {}
    except ValueError as exc:
        raise FaceServiceError("bad_response") from exc


class FaceClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _call(self, method: str, path: str, params=None, image: bytes | None = None) -> Any:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        headers = {"x-api-key": self.api_key}
        body = None
        if image is not None:
            body, headers["Content-Type"] = _multipart(image)
        return _send(f"{self.base_url}/api/v1/recognition/{path}{query}", method, body, headers)

    def recognize(self, image: bytes, threshold: float = 0.8) -> list[dict[str, Any]]:
        """Faces with their best subject. An image without a face is an empty list."""
        try:
            answer = self._call(
                "POST",
                "recognize",
                {"limit": 0, "prediction_count": 1, "det_prob_threshold": threshold},
                image,
            )
        except FaceServiceError as exc:
            if exc.reason.startswith("http_400") and "no face" in exc.reason.casefold():
                return []
            raise
        return answer.get("result", [])

    def add_example(self, subject: str, image: bytes) -> str:
        answer = self._call("POST", "faces", {"subject": subject}, image)
        return str(answer["image_id"])

    def delete_example(self, image_id: str) -> None:
        self._call("DELETE", f"faces/{image_id}")


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    temp.replace(path)


def service_client() -> FaceClient | None:
    """The configured client, or None when face matching is not set up here."""
    url = service_url()
    key = _read_state().get("api_key")
    return FaceClient(url, key) if url and key else None


def bootstrap(base_url: str) -> FaceClient:
    """Create the private admin account, app and recognition service once."""
    state = _read_state()
    if state.get("api_key"):
        return FaceClient(base_url, state["api_key"])
    email = state.get("email") or f"ftf-{uuid.uuid4().hex[:10]}@localhost.invalid"
    password = state.get("password") or secrets.token_urlsafe(24)
    write_state({**state, "email": email, "password": password})  # before it can be lost
    json_headers = {"Content-Type": "application/json"}
    if not state.get("email"):
        body = {
            "email": email,
            "password": password,
            "firstName": "Follow",
            "lastName": "Fake",
            "isAllowStatistics": False,
        }
        _send(f"{base_url}/admin/user/register", "POST", json.dumps(body).encode(), json_headers)
    form = urllib.parse.urlencode(
        {"grant_type": "password", "username": email, "password": password, "scope": "all"}
    ).encode()
    basic = base64.b64encode(OAUTH_CLIENT.encode()).decode()
    token = _send(
        f"{base_url}/admin/oauth/token",
        "POST",
        form,
        {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )["access_token"]
    auth = {**json_headers, "Authorization": f"Bearer {token}"}
    app = _send(f"{base_url}/admin/app", "POST", json.dumps({"name": "ftf"}).encode(), auth)
    model = _send(
        f"{base_url}/admin/app/{app['id']}/model",
        "POST",
        json.dumps({"name": MODEL_NAME, "type": "RECOGNITION"}).encode(),
        auth,
    )
    key = model.get("apiKey") or model.get("id")
    if not key:
        raise FaceServiceError("no_api_key")
    write_state({"email": email, "password": password, "api_key": key, "enrolled": {}})
    return FaceClient(base_url, key)
