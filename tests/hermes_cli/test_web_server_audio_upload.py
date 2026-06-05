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


def test_audio_labelled_text_parses_openai_speaker_letters(upload_client):
    _client, web_server = upload_client

    turns = web_server._turns_from_labelled_text(
        "Speaker A: hello there\n"
        "continued thought\n"
        "Speaker B: hi back\n"
        "[SPEAKER_02] - third voice"
    )

    assert turns == [
        {"speaker": "Speaker A", "text": "hello there continued thought"},
        {"speaker": "Speaker B", "text": "hi back"},
        {"speaker": "Speaker 02", "text": "third voice"},
    ]


def test_openai_audio_diarization_provider_path(upload_client, monkeypatch, tmp_path):
    _client, web_server = upload_client
    audio_path = tmp_path / "call.m4a"
    audio_path.write_bytes(b"fake audio")
    calls = []

    monkeypatch.setattr(web_server, "_ffmpeg_audio_chunks", lambda path: [b"mp3 one", b"mp3 two"])

    def fake_openai(chunk, *, chunk_index, total_chunks):
        calls.append((chunk, chunk_index, total_chunks))
        if chunk_index == 0:
            return "Speaker A: hello\nSpeaker B: hey"
        return "Speaker A: following up"

    monkeypatch.setattr(web_server, "_openai_audio_chat_completion", fake_openai)

    analysis = web_server._run_openai_audio_diarization(audio_path)

    assert calls == [(b"mp3 one", 0, 2), (b"mp3 two", 1, 2)]
    assert analysis["provider"] == "openai:gpt-audio-mini"
    assert analysis["status"] == "needs_speaker_names"
    assert analysis["chunks"] == 2
    assert analysis["turns"] == [
        {"speaker": "Speaker A", "text": "hello"},
        {"speaker": "Speaker B", "text": "hey"},
        {"speaker": "Speaker A", "text": "following up"},
    ]
    assert {speaker["speaker"] for speaker in analysis["speakers"]} == {"Speaker A", "Speaker B"}


def test_run_audio_diarization_uses_openai_and_writes_transcript(upload_client, monkeypatch):
    _client, web_server = upload_client
    audio_path = web_server._audio_uploads_dir() / "call.m4a"
    audio_path.write_bytes(b"audio")
    called = []

    def fake_openai(path):
        called.append(path)
        return {
            "status": "needs_speaker_names",
            "provider": "openai:gpt-audio-mini",
            "transcript": "hello\nhey",
            "turns": [
                {"speaker": "Speaker A", "text": "hello"},
                {"speaker": "Speaker B", "text": "hey"},
            ],
            "speakers": [
                {"speaker": "Speaker A", "example": "hello"},
                {"speaker": "Speaker B", "example": "hey"},
            ],
            "speaker_names": {},
            "named_transcript": "Speaker A: hello\nSpeaker B: hey",
            "updated_at": "now",
        }

    monkeypatch.setattr(web_server, "_run_openai_audio_diarization", fake_openai)
    monkeypatch.setattr(web_server, "_run_audio_cli", lambda path: (_ for _ in ()).throw(AssertionError("xAI CLI should not run")))

    web_server._run_audio_diarization(audio_path)

    assert called == [audio_path]
    analysis = web_server._read_audio_analysis(audio_path)
    assert analysis["provider"] == "openai:gpt-audio-mini"
    assert audio_path.with_suffix(audio_path.suffix + ".transcript.txt").read_text() == "Speaker A: hello\nSpeaker B: hey"
