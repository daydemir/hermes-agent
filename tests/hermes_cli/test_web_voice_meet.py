"""Regression tests for dashboard Meet-mode voice behavior."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"


def test_meet_wake_detection_requires_hey_rolly():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    if not (WEB_DIR / "node_modules" / "typescript").exists():
        pytest.skip("web TypeScript dependency is not installed")

    script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const webDir = process.argv[1];
const ts = require(path.join(webDir, 'node_modules', 'typescript'));
const source = fs.readFileSync(path.join(webDir, 'src/lib/voiceMeet.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const module = { exports: {} };
vm.runInNewContext(compiled, { module, exports: module.exports, RegExp, String });
const { isRollyWakePhrase } = module.exports;
const cases = [
  ['Hey Rolly, summarize this', true],
  ['hey rowley can you hear me', true],
  ['hey raleigh should also wake', true],
  ['Rolly, can you hear me?', false],
  ['Rollie should we ship?', false],
  ['Hey, can you hear me?', false],
  ['we should ask rolly later', false]
];
const failures = cases.filter(([text, expected]) => isRollyWakePhrase(text) !== expected);
if (failures.length) {
  console.error(JSON.stringify(failures));
  process.exit(1);
}
"""
    result = subprocess.run(["node", "-e", script, str(WEB_DIR)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_meet_perfect_negotiation_pure_helpers():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    if not (WEB_DIR / "node_modules" / "typescript").exists():
        pytest.skip("web TypeScript dependency is not installed")

    script = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const webDir = process.argv[1];
const ts = require(path.join(webDir, 'node_modules', 'typescript'));
const source = fs.readFileSync(path.join(webDir, 'src/lib/voiceMeet.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const module = { exports: {} };
vm.runInNewContext(compiled, { module, exports: module.exports, RegExp, String, Object, Array });
const m = module.exports;

// Politeness is deterministic and antisymmetric: exactly one peer is polite.
assert.strictEqual(m.computePoliteRole('deniz', 'arman'), true);
assert.strictEqual(m.computePoliteRole('arman', 'deniz'), false);
assert.notStrictEqual(m.computePoliteRole('a', 'b'), m.computePoliteRole('b', 'a'));

// Only the impolite peer ignores a colliding offer.
assert.strictEqual(m.shouldIgnoreOffer({ polite: false, makingOffer: true, signalingState: 'stable' }), true);
assert.strictEqual(m.shouldIgnoreOffer({ polite: false, makingOffer: false, signalingState: 'have-local-offer' }), true);
assert.strictEqual(m.shouldIgnoreOffer({ polite: false, makingOffer: false, signalingState: 'stable' }), false);
assert.strictEqual(m.shouldIgnoreOffer({ polite: true, makingOffer: true, signalingState: 'have-local-offer' }), false);

// Answers only apply while we have a local offer outstanding.
assert.strictEqual(m.shouldApplyAnswer('have-local-offer'), true);
assert.strictEqual(m.shouldApplyAnswer('stable'), false);

// Bounded restart backoff, then give up.
assert.strictEqual(m.nextRestartBackoffMs(0), 1000);
assert.strictEqual(m.nextRestartBackoffMs(2), 4000);
assert.strictEqual(m.nextRestartBackoffMs(3), undefined);

// Stale-offer gate drops offers predating our join; tolerant of undefined.
assert.strictEqual(m.isStaleOffer(2, 5), true);
assert.strictEqual(m.isStaleOffer(7, 5), false);
assert.strictEqual(m.isStaleOffer(undefined, 5), true);

// Sink keys are per (user, stream) so two streams from one peer never collide.
assert.strictEqual(m.meshSinkKey('arman', 's1'), 'arman:s1');
assert.notStrictEqual(m.meshSinkKey('arman', 's1'), m.meshSinkKey('arman', 's2'));

// parseIceConfig: STUN fallback, TURN passthrough, relay policy only when present.
const stunOnly = m.parseIceConfig({ ice_servers: [{ urls: 'stun:stun.l.google.com:19302' }] });
assert.strictEqual(stunOnly.iceTransportPolicy, undefined);
assert.strictEqual(stunOnly.iceServers.length, 1);
const withTurn = m.parseIceConfig({ ice_servers: [{ urls: 'stun:x' }, { urls: ['turn:y'], username: 'u', credential: 'c' }], ice_transport_policy: 'relay' });
assert.strictEqual(withTurn.iceTransportPolicy, 'relay');
assert.strictEqual(withTurn.iceServers[1].username, 'u');
assert.strictEqual(withTurn.iceServers[1].credential, 'c');
const empty = m.parseIceConfig(null);
assert.strictEqual(empty.iceServers.length, 1);
assert.ok(String(empty.iceServers[0].urls).indexOf('stun:') === 0);
"""
    result = subprocess.run(["node", "-e", script, str(WEB_DIR)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_start_meeting_invite_starts_meet_call_path():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")

    assert "const startMeetingInvite = useCallback" in source
    assert "await startCall(\"meet\", true)" in source
    assert "preserveCallId = false" in source
    assert "if (!preserveCallId && !new URLSearchParams(window.location.search).get(\"call_id\"))" in source
    assert "hasMeetInvite ? () => void startCall(\"meet\", true) : startMeetingInvite" in source
    assert "Join meeting" in source
    assert "startMeetPeerAudio(callIdRef.current, stream)" in source
    assert "api.postVoiceMeetSignal({ call_id: roomCallId, type: \"join\", user: speaker }" in source
    assert "api.getVoiceMeetSignals(roomCallId, voiceSignalCursorRef.current, 200, speaker, 10000)" in source
    # Mesh PCs use the server-provided ICE config (TURN relay) rather than a
    # hard-coded STUN-only list, and negotiate via perfect negotiation.
    assert "new RTCPeerConnection(iceConfigRef.current ?? STUN_FALLBACK)" in source
    assert "parseIceConfig(await api.getVoiceIce(speaker))" in source
    assert "pc.addTransceiver(\"audio\", { direction: \"sendrecv\" })" in source
    assert "pc.onnegotiationneeded" in source
    assert "onClick={() => void startCall()}" in source


def test_meet_mesh_uses_perfect_negotiation_and_ice_buffering():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")
    # Perfect-negotiation primitives wired from the pure helpers.
    assert "computePoliteRole(speaker, remoteUser)" in source
    assert "shouldIgnoreOffer({ polite: peer.polite, makingOffer: peer.makingOffer, signalingState: pc.signalingState })" in source
    assert "shouldApplyAnswer(pc.signalingState)" in source
    assert "isStaleOffer(signal.index, myJoinIndexRef.current)" in source
    # ICE candidates are buffered until the remote description exists, not dropped.
    assert "peer.pendingCandidates.push(candidate)" in source
    assert "drainCandidates(peer)" in source
    # Cursor seeded from the join high-water index, never reset to 0 mid-call.
    assert "myJoinIndexRef.current = joinResp.signal.index" in source
    assert "voiceSignalCursorRef.current = joinResp.signal.index" in source


def test_meet_audio_playback_is_explicit_play_not_autoplay_attribute():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")
    # The OpenAI sink must not rely on the autoplay attribute; playback is driven
    # by an explicit play() with a tap-to-enable fallback on rejection.
    assert "<audio ref={audioRef} autoPlay />" not in source
    assert "<audio ref={audioRef} />" in source
    assert "await el.play()" in source
    assert "setAudioBlocked(true)" in source
    assert "Tap to enable call audio" in source
    # Rolly fan-out mixes mic+Rolly and replaceTrack's the existing sender.
    assert "createMediaStreamDestination()" in source
    assert "enableRollyFanout()" in source
    assert "peer.micSender?.replaceTrack(mixedTrack)" in source


def test_start_meeting_invite_mints_fresh_call_id_before_invite_and_blocks_double_tap():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")
    start = source.index("const startMeetingInvite = useCallback")
    end = source.index("const toggleMute", start)
    body = source[start:end]

    assert "const [invitePending, setInvitePending] = useState(false)" in source
    assert "const busy = invitePending ||" in source
    assert body.index("setInvitePending(true)") < body.index("api.createVoiceMeetInvite")
    assert body.index("callIdRef.current = `voice-${Date.now()}-${Math.random().toString(16).slice(2)}`") < body.index("api.createVoiceMeetInvite")
    assert "setCallIdDisplay(callIdRef.current)" in body
    assert "setInvitePending(false)" in body


def test_meet_transcripts_use_active_call_mode_not_stale_react_state():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")

    assert "const activeCallModeRef = useRef<\"solo\" | \"meet\">(\"solo\")" in source
    assert "activeCallModeRef.current = callMode" in source
    assert "const currentMode = activeCallModeRef.current" in source
    assert "currentMode === \"meet\"" in source
    assert "mode === \"meet\"\n              ? { mode" not in source


def test_start_call_owns_mic_permission_and_live_mic_switching():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")

    assert "Enable mic list" not in source
    assert "onClick={enableMicList}" not in source
    assert "navigator.mediaDevices.getUserMedia({ audio })" in source
    assert "if (live) void switchMicrophone(next)" in source
    assert "disabled={busy}" in source
    assert "replaceTrack(nextTrack)" in source


def test_voice_page_filters_realtime_noise_until_verbose_and_uses_compact_rows():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")

    assert "const [verboseEvents, setVerboseEvents] = useState(false)" in source
    assert "isRealtimeSpeechEvent" in source
    assert "verboseEvents || !isRealtimeSpeechEvent(entry)" in source
    assert "Verbose: {verboseEvents ? \"on\" : \"off\"}" in source
    assert "bg-background-base/50 px-2 py-1" in source
    assert "bg-background-base/50 p-3" not in source


def test_voice_page_transcript_and_events_stick_to_latest_without_forcing_manual_scroll():
    source = (WEB_DIR / "src/pages/VoiceCallPage.tsx").read_text(encoding="utf-8")

    assert "const AUTO_SCROLL_NEAR_BOTTOM_PX = 64" in source
    assert "function isNearScrollBottom(element: HTMLElement): boolean" in source
    assert "element.scrollHeight - element.scrollTop - element.clientHeight <= AUTO_SCROLL_NEAR_BOTTOM_PX" in source
    assert "const transcriptScrollRef = useRef<HTMLDivElement | null>(null)" in source
    assert "const eventsScrollRef = useRef<HTMLDivElement | null>(null)" in source
    assert "const [transcriptAtLatest, setTranscriptAtLatest] = useState(true)" in source
    assert "const [eventsAtLatest, setEventsAtLatest] = useState(true)" in source
    assert "if (transcriptAtLatest) scrollColumnToBottom(transcriptScrollRef.current)" in source
    assert "if (eventsAtLatest) scrollColumnToBottom(eventsScrollRef.current)" in source
    assert "onScroll={(event) => updateScrollLock(\"transcript\", event.currentTarget)}" in source
    assert "onScroll={(event) => updateScrollLock(\"events\", event.currentTarget)}" in source
    assert "!transcriptAtLatest" in source
    assert "!eventsAtLatest" in source
    assert source.count("Jump to latest") == 2


@pytest.fixture()
def voice_client(monkeypatch, _isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_cli.web_server as web_server

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client, web_server


def test_voice_call_endpoint_passes_meet_mode(voice_client, monkeypatch):
    client, web_server = voice_client
    seen = {}

    def fake_create_call(sdp, user=None, mode="solo"):
        seen.update({"sdp": sdp, "user": user, "mode": mode})
        return "answer-sdp"

    monkeypatch.setattr(web_server, "_create_openai_realtime_call", fake_create_call)

    resp = client.post(
        "/api/voice/call?mode=meet",
        data="offer-sdp",
        headers={"Content-Type": "application/sdp", "X-Rolly-User": "deniz"},
    )

    assert resp.status_code == 200
    assert resp.text == "answer-sdp"
    assert seen == {"sdp": "offer-sdp", "user": "deniz", "mode": "meet"}
    assert resp.headers["content-type"].startswith("application/sdp")


def test_voice_ice_endpoint_stun_only_without_turn(voice_client, monkeypatch):
    client, _web_server = voice_client
    for var in ("HERMES_VOICE_TURN_URLS", "HERMES_VOICE_TURN_USERNAME", "HERMES_VOICE_TURN_CREDENTIAL", "HERMES_VOICE_ICE_RELAY_ONLY", "HERMES_VOICE_STUN_URLS"):
        monkeypatch.delenv(var, raising=False)

    resp = client.get("/api/voice/ice")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["ice_servers"][0]["urls"] == "stun:stun.l.google.com:19302"
    assert all("username" not in server for server in body["ice_servers"])
    assert "ice_transport_policy" not in body


def test_voice_ice_endpoint_advertises_turn_and_relay_when_configured(voice_client, monkeypatch):
    client, _web_server = voice_client
    monkeypatch.setenv("HERMES_VOICE_TURN_URLS", "turn:100.122.202.20:3478?transport=udp")
    monkeypatch.setenv("HERMES_VOICE_TURN_USERNAME", "rolly")
    monkeypatch.setenv("HERMES_VOICE_TURN_CREDENTIAL", "secret-pw")
    monkeypatch.setenv("HERMES_VOICE_ICE_RELAY_ONLY", "1")

    body = client.get("/api/voice/ice").json()
    turn = [server for server in body["ice_servers"] if server.get("username") == "rolly"]
    assert turn, "expected a TURN entry when TURN env is configured"
    assert turn[0]["credential"] == "secret-pw"
    assert turn[0]["urls"] == ["turn:100.122.202.20:3478?transport=udp"]
    # relay-only forces all mesh media through the relay both peers can reach.
    assert body["ice_transport_policy"] == "relay"


def test_voice_ice_relay_policy_requires_turn(voice_client, monkeypatch):
    """relay-only must NOT brick the call when no TURN is configured."""
    client, _web_server = voice_client
    for var in ("HERMES_VOICE_TURN_URLS", "HERMES_VOICE_TURN_USERNAME", "HERMES_VOICE_TURN_CREDENTIAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HERMES_VOICE_ICE_RELAY_ONLY", "1")

    body = client.get("/api/voice/ice").json()
    assert "ice_transport_policy" not in body  # degrades to STUN, not relay-only


def test_meet_signal_channel_suppresses_self_directed_echo_and_routes_by_addressee(voice_client):
    """End-to-end signaling: directed offers/answers reach only the addressee and
    never echo back to the sender; broadcast joins return a high-water index used
    to seed each client's cursor so a (re)joiner never replays its own history."""
    client, _web_server = voice_client
    room = "voice-test-roundtrip"

    def post(user, type_, to_user=None, payload=None):
        body = {"call_id": room, "type": type_, "user": user}
        if to_user is not None:
            body["to_user"] = to_user
        if payload is not None:
            body["payload"] = payload
        resp = client.post("/api/voice/meet/signal", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["signal"]

    def signals(user, since=0):
        resp = client.get(
            f"/api/voice/meet/signals?call_id={room}&since={since}&limit=200",
            headers={"X-Rolly-User": user},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    join_d = post("deniz", "join")
    join_a = post("arman", "join")
    # The join POST returns the high-water index each client seeds its cursor from.
    assert join_a["index"] > join_d["index"]

    # Directed offer deniz -> arman.
    post("deniz", "offer", to_user="arman", payload={"type": "offer", "sdp": "x"})

    # The addressee sees the offer...
    assert any(s["type"] == "offer" for s in signals("arman")["signals"])
    # ...the sender never re-receives its own directed offer (the self-echo fix)...
    assert not any(s["type"] == "offer" for s in signals("deniz")["signals"])
    # ...and an unrelated third party never sees a directed signal.
    assert not any(s["type"] == "offer" for s in signals("carol")["signals"])

    # Cursor seeded from the join index: a (re)joiner polling from its own join
    # high-water mark never replays its own join, but still sees a later peer's.
    deniz_after_join = signals("deniz", since=join_d["index"])
    seen = [(s["from_user"], s["type"]) for s in deniz_after_join["signals"]]
    assert ("deniz", "join") not in seen
    assert ("arman", "join") in seen
