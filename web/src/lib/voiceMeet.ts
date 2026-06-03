const ROLLY_WAKE_NAMES = "(?:rolly|rollie|rowley|rowly|rowy|roley|rally|raleigh)";
const HEY_ROLLY_RE = new RegExp(`\\bhey\\s+${ROLLY_WAKE_NAMES}\\b`);

export function normalizeMeetTranscript(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function isRollyWakePhrase(text: string): boolean {
  const normalized = normalizeMeetTranscript(text);
  return HEY_ROLLY_RE.test(normalized);
}

// ---------------------------------------------------------------------------
// Pure helpers for the Meet-mode peer mesh (perfect-negotiation state machine).
// Kept here, framework-free, so they are unit-testable without a browser.
// ---------------------------------------------------------------------------

export interface IceServerConfig {
  urls: string | string[];
  username?: string;
  credential?: string;
}

export interface IceConfigResponse {
  ice_servers: IceServerConfig[];
  ice_transport_policy?: "relay" | "all";
}

/**
 * Perfect-negotiation politeness. The lexicographically-greater participant id
 * is the "polite" peer (it rolls back on an offer collision); the lesser id is
 * "impolite" (it ignores a colliding offer and is the one that drives ICE
 * restart). Deterministic and symmetric, so join order is irrelevant.
 */
export function computePoliteRole(speaker: string, remoteUser: string): boolean {
  return speaker > remoteUser;
}

/**
 * Whether to ignore an inbound offer under perfect negotiation: only the
 * impolite peer ignores, and only when it would collide with our own in-flight
 * offer (we're making an offer, or the connection isn't stable).
 */
export function shouldIgnoreOffer(args: { polite: boolean; makingOffer: boolean; signalingState: string }): boolean {
  const collision = args.makingOffer || args.signalingState !== "stable";
  return !args.polite && collision;
}

/** An inbound answer is only valid while we have a local offer outstanding. */
export function shouldApplyAnswer(signalingState: string): boolean {
  return signalingState === "have-local-offer";
}

/**
 * Bounded ICE-restart backoff: 1s, 2s, 4s, then give up (undefined). Only the
 * impolite peer restarts, so two peers never fire dueling restarts.
 */
export function nextRestartBackoffMs(attempt: number): number | undefined {
  return [1000, 2000, 4000][attempt];
}

/**
 * Drop offers whose signaling index predates our own join. A peer that joins or
 * reconnects must not re-apply historical offers from before it existed (even
 * if the server still surfaces them), which would trigger phantom glare.
 */
export function isStaleOffer(signalIndex: number | undefined, myJoinIndex: number | undefined): boolean {
  return (signalIndex ?? 0) < (myJoinIndex ?? 0);
}

/**
 * Mesh audio sink key. Keyed by (remoteUser, stream id) — NOT by user alone —
 * so two inbound streams from one peer (e.g. their mic and a relayed Rolly mix)
 * never collide and overwrite each other in the sink map.
 */
export function meshSinkKey(remoteUser: string, streamId: string): string {
  return `${remoteUser}:${streamId}`;
}

/**
 * Translate the server's /api/voice/ice response into an RTCConfiguration.
 * Only sets iceTransportPolicy when the server explicitly returned one (it only
 * does so for the mesh when a TURN relay is configured), so a missing/broken
 * TURN degrades to STUN candidates instead of bricking the call.
 */
export function parseIceConfig(resp: IceConfigResponse | null | undefined): RTCConfiguration {
  const iceServers = (resp?.ice_servers ?? []).map((server) => ({
    urls: server.urls,
    ...(server.username ? { username: server.username } : {}),
    ...(server.credential ? { credential: server.credential } : {}),
  })) as RTCIceServer[];
  const config: RTCConfiguration = {
    iceServers: iceServers.length ? iceServers : [{ urls: "stun:stun.l.google.com:19302" }],
  };
  if (resp?.ice_transport_policy) config.iceTransportPolicy = resp.ice_transport_policy;
  return config;
}
