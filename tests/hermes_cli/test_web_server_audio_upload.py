"""Tests for dashboard audio upload endpoint."""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def upload_client(_isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_cli.web_server as web_server

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client, web_server


def test_audio_upload_requires_dashboard_token(_isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli.web_server import app

    client = TestClient(app)
    resp = client.post("/api/uploads/audio?filename=meeting.m4a", content=b"audio")
    assert resp.status_code == 401


def test_audio_upload_saves_file_and_metadata(upload_client, monkeypatch):
    client, web_server = upload_client
    started = []
    monkeypatch.setattr(web_server, "_start_audio_diarization", lambda path: started.append(path))

    resp = client.post(
        "/api/uploads/audio?filename=Team%20Meeting.m4a",
        content=b"fake audio bytes",
        headers={
            "content-type": "audio/mp4",
            "x-rolly-user": "deniz",
            web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["original_name"] == "Team Meeting.m4a"
    assert body["stored_name"].endswith("Team-Meeting.m4a")
    assert body["size_bytes"] == len(b"fake audio bytes")
    assert body["rolly_user"] in {"deniz", "unknown"}
    assert body["prompt"].endswith(body["path"])
    assert body["analysis_status"] == "queued"
    assert body["analysis_url"].endswith("/analysis")

    saved = Path(body["path"])
    assert started == [saved]
    assert saved.read_bytes() == b"fake audio bytes"
    metadata = json.loads(saved.with_suffix(saved.suffix + ".json").read_text())
    assert metadata["path"] == body["path"]
    assert saved.parent == web_server.get_hermes_home() / "uploads" / "audio"


def test_audio_upload_rejects_non_audio_extension(upload_client):
    client, _web_server = upload_client

    resp = client.post("/api/uploads/audio?filename=notes.txt", content=b"not audio")

    assert resp.status_code == 400
    assert "Unsupported audio file extension" in resp.text


def test_audio_analysis_speaker_name_assignment(upload_client, tmp_path):
    client, web_server = upload_client
    audio_path = web_server._audio_uploads_dir() / "call.m4a"
    audio_path.write_bytes(b"audio")
    web_server._write_audio_analysis(
        audio_path,
        {
            "status": "needs_speaker_names",
            "turns": [
                {"speaker": "SPEAKER_00", "text": "hello from one"},
                {"speaker": "SPEAKER_01", "text": "hello from two"},
            ],
            "speakers": [
                {"speaker": "SPEAKER_00", "example": "hello from one"},
                {"speaker": "SPEAKER_01", "example": "hello from two"},
            ],
            "transcript": "hello from one hello from two",
        },
    )

    resp = client.post(
        "/api/uploads/audio/call.m4a/speakers",
        json={"speakers": {"SPEAKER_00": "Deniz", "SPEAKER_01": "Arman"}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert "Deniz: hello from one" in body["named_transcript"]
    assert "Arman: hello from two" in body["named_transcript"]
    assert "Deniz: hello from one" in audio_path.with_suffix(audio_path.suffix + ".transcript.txt").read_text()


def test_audio_upload_streams_without_request_body(upload_client, monkeypatch):
    client, web_server = upload_client
    monkeypatch.setattr(web_server, "_start_audio_diarization", lambda path: None)

    def fail_body(self):
        raise AssertionError("request.body() should not be used for audio uploads")

    monkeypatch.setattr("starlette.requests.Request.body", fail_body)
    payload = b"streamed audio" * 1024

    resp = client.post(
        "/api/uploads/audio?filename=streamed.m4a",
        content=payload,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert resp.status_code == 200
    body = resp.json()
    saved = Path(body["path"])
    assert body["size_bytes"] == len(payload)
    assert saved.read_bytes() == payload
    assert not list(saved.parent.glob("*.uploading-*.tmp"))
