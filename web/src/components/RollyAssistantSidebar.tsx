/**
 * RollyAssistantSidebar — the global, persistent, page-aware Rolly chat.
 *
 * A non-modal right-side panel mounted near the app shell so it survives route
 * changes. It is a structured (non-terminal) chat over the in-process gateway
 * (`/api/ws`): the SAME engine the TUI drives, so Rolly has its full
 * capabilities and can act on cards. The card execution / CC tmux terminal is
 * kept entirely separate (it lives in the Kanban card workspace).
 *
 * Persistence: one ongoing conversation per dashboard profile. We pin a stable
 * gateway session title `assistant:<profile>` and `session.resume` it on open
 * (rehydrating history from state.db), creating + titling a fresh one only the
 * first time. Switching profile swaps to that profile's conversation.
 *
 * Page-awareness: the sidebar reads the layered page-context bus
 * (lib/pageContext.ts). When the visible entity changes (e.g. you open a
 * different Kanban card) it prepends a compact context block to the next
 * message so Rolly knows what you're looking at — never a DOM dump.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ChevronRight,
  Loader2,
  MessageSquare,
  Plus,
  Send,
  ShieldCheck,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/Markdown";
import { ToolCall, type ToolEntry } from "@/components/ToolCall";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { getRollyUserSlug } from "@/lib/rollyIdentity";
import { subscribePageContext, type PageContext } from "@/lib/pageContext";

const COLLAPSED_KEY = "hermes-assistant-collapsed";
const WIDTH_KEY = "hermes-assistant-width";
const YOLO_KEY = "hermes-assistant-autorun";

const MIN_WIDTH = 320;
const MAX_WIDTH = 680;
const DEFAULT_WIDTH = 400;

// Separates the injected page-context preamble from the user's typed text so
// resumed history can show clean user bubbles.
const CTX_MARKER = "\n\n— — — message — — —\n";

// Safety net: if a turn is in flight but the gateway goes completely silent
// (no deltas, tools, or completion) for this long, release the composer so the
// UI can never deadlock on a dropped message.complete. Reset on every event,
// so long-but-active turns are unaffected.
const STREAM_IDLE_TIMEOUT_MS = 300_000;

type TurnRole = "user" | "assistant";

interface Turn {
  id: string;
  role: TurnRole;
  text: string;
  streaming?: boolean;
  reasoning?: string;
  tools?: ToolEntry[];
  error?: boolean;
}

type PromptKind = "approval" | "clarify" | "sudo" | "secret";

interface PendingPrompt {
  kind: PromptKind;
  requestId?: string;
  text: string;
}

// ── localStorage helpers (private browsing safe) ─────────────────────
function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "true";
  } catch {
    return fallback;
  }
}
function writeBool(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* ignore */
  }
}
function readWidth(): number {
  try {
    const v = Number(localStorage.getItem(WIDTH_KEY));
    if (Number.isFinite(v) && v >= MIN_WIDTH && v <= MAX_WIDTH) return v;
  } catch {
    /* ignore */
  }
  return DEFAULT_WIDTH;
}

let _idCounter = 0;
function nextId(prefix: string): string {
  _idCounter += 1;
  return `${prefix}-${_idCounter}`;
}

/** Compact, human-readable context block — never a DOM/database dump. */
function formatContext(ctx: PageContext): string {
  const lines: string[] = ["[dashboard context]"];
  if (ctx.entity_type && ctx.entity_id) {
    lines.push(
      `viewing ${ctx.entity_type} ${ctx.entity_id}` +
        (ctx.title ? ` — "${ctx.title}"` : ""),
    );
  } else {
    lines.push(`page: ${ctx.title ?? ctx.route} (${ctx.route})`);
  }
  if (ctx.summary) lines.push(`summary: ${ctx.summary}`);
  const extras: string[] = [];
  for (const key of ["status", "assignee", "priority", "tenant", "workspace_path"]) {
    const v = ctx[key];
    if (v != null && v !== "") extras.push(`${key}: ${v}`);
  }
  if (extras.length) lines.push(extras.join(" · "));
  if (ctx.selected_filters && Object.keys(ctx.selected_filters).length) {
    lines.push(`filters: ${JSON.stringify(ctx.selected_filters)}`);
  }
  return lines.join("\n");
}

function contextKey(ctx: PageContext | null): string {
  if (!ctx) return "";
  return `${ctx.route}|${ctx.entity_type ?? ""}|${ctx.entity_id ?? ""}`;
}

function contextChip(ctx: PageContext | null): string {
  if (!ctx) return "";
  if (ctx.entity_type && ctx.entity_id) {
    return ctx.title ? `${ctx.entity_type}: ${ctx.title}` : `${ctx.entity_type} ${ctx.entity_id}`;
  }
  return ctx.title ?? ctx.route;
}

export function RollyAssistantSidebar() {
  const [collapsed, setCollapsed] = useState(() => readBool(COLLAPSED_KEY, false));
  const [width, setWidth] = useState(() => readWidth());
  const [autoRun, setAutoRun] = useState(() => readBool(YOLO_KEY, true));

  const [user, setUser] = useState(() => getRollyUserSlug());
  const [state, setState] = useState<ConnectionState>("idle");
  const [messages, setMessages] = useState<Turn[]>([]);
  const [pending, setPending] = useState<PendingPrompt | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ctx, setCtx] = useState<PageContext | null>(null);

  // `version` bumps to force a fresh GatewayClient (reconnect / profile swap).
  const [version, setVersion] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const gw = useMemo(() => new GatewayClient(), [version]);

  const sidRef = useRef<string | null>(null);
  const currentTurnRef = useRef<string | null>(null);
  const lastCtxKeyRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const disarmWatchdog = useCallback(() => {
    if (watchdogRef.current) {
      clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
  }, []);
  // (Re)start the inactivity watchdog. Called on send and on every streaming
  // event so it only fires after a genuine silent stall.
  const armWatchdog = useCallback(() => {
    if (watchdogRef.current) clearTimeout(watchdogRef.current);
    watchdogRef.current = setTimeout(() => {
      watchdogRef.current = null;
      currentTurnRef.current = null;
      setBusy(false);
      setError("Rolly has gone quiet — it may still be working. Send again or reconnect.");
    }, STREAM_IDLE_TIMEOUT_MS);
  }, []);

  // ── Push page content left so the panel is non-modal (Cursor-style) ──
  useEffect(() => {
    const pad = collapsed ? "0px" : `${width}px`;
    document.documentElement.style.setProperty("--hermes-assistant-pad", pad);
    return () => {
      document.documentElement.style.removeProperty("--hermes-assistant-pad");
    };
  }, [collapsed, width]);

  // ── Track the active dashboard profile ───────────────────────────────
  useEffect(() => {
    const sync = () => setUser(getRollyUserSlug());
    window.addEventListener("rolly-user-change", sync);
    return () => window.removeEventListener("rolly-user-change", sync);
  }, []);

  // ── Subscribe to the page-context bus ────────────────────────────────
  useEffect(() => subscribePageContext(setCtx), []);

  // ── Auto-scroll to the newest content ────────────────────────────────
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending, busy]);

  const persist = {
    collapsed: useCallback((v: boolean) => {
      setCollapsed(v);
      writeBool(COLLAPSED_KEY, v);
    }, []),
  };

  // ── Turn-model mutation helpers ──────────────────────────────────────
  const upsertCurrentAssistant = useCallback(
    (mut: (t: Turn) => Turn) => {
      setMessages((prev) => {
        const id = currentTurnRef.current;
        const idx = id ? prev.findIndex((t) => t.id === id) : -1;
        if (idx < 0) {
          const fresh: Turn = { id: nextId("a"), role: "assistant", text: "", streaming: true, tools: [] };
          currentTurnRef.current = fresh.id;
          return [...prev, mut(fresh)];
        }
        const next = [...prev];
        next[idx] = mut(next[idx]);
        return next;
      });
    },
    [],
  );

  // ── Gateway lifecycle: connect, bootstrap per-profile session, stream ─
  useEffect(() => {
    let cancelled = false;
    const offState = gw.onState(setState);

    const onMsgStart = gw.on("message.start", () => {
      armWatchdog();
      const fresh: Turn = { id: nextId("a"), role: "assistant", text: "", streaming: true, tools: [] };
      currentTurnRef.current = fresh.id;
      setMessages((prev) => [...prev, fresh]);
    });

    const onMsgDelta = gw.on<{ text?: string }>("message.delta", (ev) => {
      armWatchdog();
      const text = ev.payload?.text ?? "";
      if (!text) return;
      upsertCurrentAssistant((t) => ({ ...t, text: t.text + text, streaming: true }));
    });

    const onMsgComplete = gw.on<{ text?: string; reasoning?: string }>("message.complete", (ev) => {
      disarmWatchdog();
      const finalText = ev.payload?.text ?? "";
      const reasoning = ev.payload?.reasoning;
      upsertCurrentAssistant((t) => ({
        ...t,
        text: finalText || t.text,
        reasoning: reasoning || t.reasoning,
        streaming: false,
      }));
      currentTurnRef.current = null;
      setBusy(false);
    });

    const onToolStart = gw.on<{ tool_id?: string; name?: string; context?: string }>("tool.start", (ev) => {
      armWatchdog();
      const p = ev.payload;
      if (!p?.tool_id) return;
      const toolId = p.tool_id;
      const name = p.name ?? "tool";
      const context = p.context;
      upsertCurrentAssistant((t) => ({
        ...t,
        tools: [
          ...(t.tools ?? []),
          {
            kind: "tool",
            id: `tool-${toolId}`,
            tool_id: toolId,
            name,
            context,
            status: "running",
            startedAt: Date.now(),
          },
        ],
      }));
    });

    const onToolProgress = gw.on<{ name?: string; preview?: string }>("tool.progress", (ev) => {
      armWatchdog();
      const p = ev.payload;
      if (!p?.name || !p.preview) return;
      upsertCurrentAssistant((t) => ({
        ...t,
        tools: (t.tools ?? []).map((tool) =>
          tool.status === "running" && tool.name === p.name ? { ...tool, preview: p.preview } : tool,
        ),
      }));
    });

    const onToolComplete = gw.on<{ tool_id?: string; summary?: string; error?: string; inline_diff?: string }>(
      "tool.complete",
      (ev) => {
        armWatchdog();
        const p = ev.payload;
        if (!p?.tool_id) return;
        upsertCurrentAssistant((t) => ({
          ...t,
          tools: (t.tools ?? []).map((tool) =>
            tool.tool_id === p.tool_id
              ? {
                  ...tool,
                  status: p.error ? "error" : "done",
                  summary: p.summary,
                  error: p.error,
                  inline_diff: p.inline_diff,
                  completedAt: Date.now(),
                }
              : tool,
          ),
        }));
      },
    );

    const onError = gw.on<{ message?: string }>("error", (ev) => {
      disarmWatchdog();
      if (ev.payload?.message) setError(ev.payload.message);
      setBusy(false);
      currentTurnRef.current = null;
    });

    const promptHandler = (kind: PromptKind) =>
      gw.on<{ request_id?: string; message?: string; prompt?: string; question?: string; text?: string; command?: string }>(
        `${kind}.request`,
        (ev) => {
          const p = ev.payload ?? {};
          const text = p.message ?? p.prompt ?? p.question ?? p.text ?? p.command ?? `${kind} requested`;
          setPending({ kind, requestId: p.request_id, text });
        },
      );
    const offApproval = promptHandler("approval");
    const offClarify = promptHandler("clarify");
    const offSudo = promptHandler("sudo");
    const offSecret = promptHandler("secret");

    const title = `assistant:${user}`;

    gw.connect()
      .then(async () => {
        if (cancelled) return;
        // Resume the profile's ongoing conversation, or start (and title) one.
        let sid: string | null = null;
        try {
          const resumed = await gw.request<{ session_id: string; messages?: Array<{ role: string; text?: string; name?: string; context?: string }> }>(
            "session.resume",
            { session_id: title },
          );
          sid = resumed.session_id;
          setMessages(historyToTurns(resumed.messages ?? []));
        } catch {
          if (cancelled) return;
          const created = await gw.request<{ session_id: string }>("session.create", {});
          sid = created.session_id;
          // Stamp the stable per-profile title so future opens resume this one.
          await gw.request("session.title", { session_id: sid, title }).catch(() => {});
          setMessages([]);
        }
        if (cancelled || !sid) return;
        sidRef.current = sid;
        lastCtxKeyRef.current = "";
        await reconcileYolo(gw, sid, autoRun);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });

    return () => {
      cancelled = true;
      offState();
      onMsgStart();
      onMsgDelta();
      onMsgComplete();
      onToolStart();
      onToolProgress();
      onToolComplete();
      onError();
      offApproval();
      offClarify();
      offSudo();
      offSecret();
      disarmWatchdog();
      gw.close();
      sidRef.current = null;
      currentTurnRef.current = null;
    };
    // autoRun is reconciled separately below; re-running bootstrap on profile
    // change or reconnect (version) is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gw, user]);

  // Keep the gateway session's auto-approve setting in sync with the toggle.
  useEffect(() => {
    writeBool(YOLO_KEY, autoRun);
    const sid = sidRef.current;
    if (sid && state === "open") void reconcileYolo(gw, sid, autoRun);
  }, [autoRun, gw, state]);

  const reconnect = useCallback(() => {
    setError(null);
    setMessages([]);
    currentTurnRef.current = null;
    setBusy(false);
    setPending(null);
    setVersion((v) => v + 1);
  }, []);

  const newChat = useCallback(() => {
    const oldSid = sidRef.current;
    if (!oldSid || state !== "open") return;
    setBusy(false);
    setPending(null);
    setError(null);
    void (async () => {
      // The per-profile title is unique (set_session_title rejects duplicates),
      // so we must FREE it from the current session BEFORE reusing it on the new
      // one — otherwise the retitle is silently rejected and the old conversation
      // keeps resuming. If we can't free it, abort rather than orphan a session:
      // the current conversation stays the profile's ongoing chat.
      try {
        await gw.request("session.title", { session_id: oldSid, title: `assistant:${user}:${Date.now()}` });
      } catch {
        setError("Couldn't start a new conversation — the current one is still active.");
        return;
      }
      try {
        const created = await gw.request<{ session_id: string }>("session.create", {});
        sidRef.current = created.session_id;
        // Title is now free, so this should succeed; surface it if it doesn't so
        // the user knows the new chat may not persist across reloads.
        try {
          await gw.request("session.title", { session_id: created.session_id, title: `assistant:${user}` });
        } catch {
          setError("New conversation started, but it couldn't be pinned and may not persist on reload.");
        }
        await reconcileYolo(gw, created.session_id, autoRun);
        currentTurnRef.current = null;
        lastCtxKeyRef.current = "";
        setMessages([]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [gw, state, user, autoRun]);

  const send = useCallback(() => {
    const sid = sidRef.current;
    const text = draft.trim();
    if (!sid || !text || busy || state !== "open") return;

    // Inject the compact page context only when the visible entity changed,
    // so Rolly stays oriented without spamming every turn.
    const current = ctx;
    const key = contextKey(current);
    let sent = text;
    if (current && key !== lastCtxKeyRef.current) {
      sent = `${formatContext(current)}${CTX_MARKER}${text}`;
      lastCtxKeyRef.current = key;
    }

    setMessages((prev) => [...prev, { id: nextId("u"), role: "user", text }]);
    setDraft("");
    setBusy(true);
    setError(null);
    armWatchdog();
    gw.request("prompt.submit", { session_id: sid, text: sent }).catch((e: Error) => {
      disarmWatchdog();
      setError(e.message);
      setBusy(false);
    });
  }, [gw, draft, busy, state, ctx, armWatchdog, disarmWatchdog]);

  const respondPrompt = useCallback(
    (answer: string, approve?: boolean) => {
      const sid = sidRef.current;
      if (!pending) return;
      const { kind, requestId } = pending;
      if (kind === "approval") {
        if (sid) void gw.request("approval.respond", { session_id: sid, choice: approve ? "approve" : "deny" }).catch(() => {});
      } else if (kind === "clarify") {
        void gw.request("clarify.respond", { request_id: requestId, answer }).catch(() => {});
      } else if (kind === "sudo") {
        void gw.request("sudo.respond", { request_id: requestId, password: answer }).catch(() => {});
      } else if (kind === "secret") {
        void gw.request("secret.respond", { request_id: requestId, value: answer }).catch(() => {});
      }
      setPending(null);
    },
    [gw, pending],
  );

  // ── Resize via the left-edge drag handle ─────────────────────────────
  const onResizeStart = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = width;
      let finalW = startW;
      const onMove = (ev: PointerEvent) => {
        finalW = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startW + (startX - ev.clientX)));
        setWidth(finalW);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        try {
          localStorage.setItem(WIDTH_KEY, String(finalW));
        } catch {
          /* ignore */
        }
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [width],
  );

  // ── Collapsed launcher ───────────────────────────────────────────────
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => persist.collapsed(false)}
        aria-label="Open Rolly assistant"
        className={cn(
          "fixed right-0 top-1/2 z-40 -translate-y-1/2",
          "flex items-center gap-1.5 rounded-l-md px-2 py-3",
          "border border-r-0 border-current/20 bg-background-base/95 backdrop-blur-sm shadow-lg",
          "text-text-secondary hover:text-midground transition-colors",
        )}
      >
        <Sparkles className="h-4 w-4" />
        <span className="font-mondwest text-display text-xs uppercase tracking-[0.12em] [writing-mode:vertical-rl]">
          Rolly
        </span>
      </button>
    );
  }

  const statusTone =
    state === "open" ? "bg-success" : state === "connecting" ? "bg-warning" : "bg-destructive";
  const chip = contextChip(ctx);
  // Show a "thinking…" placeholder while a turn is in flight but the assistant
  // hasn't started streaming its reply yet (last item is still the user turn).
  const awaitingFirstToken =
    busy && (messages.length === 0 || messages[messages.length - 1].role !== "assistant");

  return (
    <aside
      aria-label="Rolly assistant"
      className={cn(
        "fixed right-0 top-0 z-40 flex h-dvh max-h-dvh min-h-0 flex-col",
        "border-l border-current/20 bg-background-base/95 backdrop-blur-sm shadow-2xl",
      )}
      style={{ width }}
    >
      {/* Resize handle */}
      <div
        onPointerDown={onResizeStart}
        className="absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize hover:bg-midground/20"
        aria-hidden
      />

      {/* Header */}
      <div className="flex shrink-0 items-center gap-2 border-b border-current/20 px-3 py-2">
        <Sparkles className="h-4 w-4 shrink-0 text-midground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="font-mondwest text-display text-sm uppercase tracking-[0.12em] text-midground">
              Rolly
            </span>
            <span className={cn("h-1.5 w-1.5 rounded-full", statusTone)} aria-hidden />
          </div>
          {chip && (
            <div className="truncate text-xs text-text-tertiary" title={chip}>
              {chip}
            </div>
          )}
        </div>

        <Button
          ghost
          size="icon"
          onClick={() => setAutoRun((v) => !v)}
          title={autoRun ? "Auto-approve actions: ON (click to require approval)" : "Auto-approve actions: OFF (click to let Rolly act freely)"}
          aria-label="Toggle auto-approve"
          className={cn("h-7 w-7", autoRun ? "text-warning hover:text-warning" : "text-text-secondary hover:text-midground")}
        >
          {autoRun ? <ShieldAlert className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}
        </Button>
        <Button
          ghost
          size="icon"
          onClick={newChat}
          disabled={state !== "open"}
          title="New conversation"
          aria-label="New conversation"
          className="h-7 w-7 text-text-secondary hover:text-midground"
        >
          <Plus className="h-4 w-4" />
        </Button>
        <Button
          ghost
          size="icon"
          onClick={() => persist.collapsed(true)}
          title="Collapse"
          aria-label="Collapse assistant"
          className="h-7 w-7 text-text-secondary hover:text-midground"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 py-3">
        {messages.length === 0 && !busy ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-text-secondary">
            <MessageSquare className="h-6 w-6 opacity-50" />
            <p className="text-sm">Ask Rolly about what you're looking at.</p>
            <p className="text-xs text-text-tertiary">
              It can read and act on cards, run tools, and answer questions — with your full Hermes capabilities.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((m) => (
              <TurnView key={m.id} turn={m} />
            ))}
            {awaitingFirstToken && (
              <div className="flex items-center gap-2 text-xs text-text-tertiary">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                thinking…
              </div>
            )}
          </div>
        )}
      </div>

      {/* Pending interactive prompt */}
      {pending && <PromptView prompt={pending} onRespond={respondPrompt} />}

      {/* Error banner */}
      {error && (
        <div className="flex shrink-0 items-start gap-2 border-t border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
          <span className="min-w-0 flex-1 wrap-break-word text-destructive">{error}</span>
          {state !== "open" && (
            <button type="button" onClick={reconnect} className="shrink-0 underline hover:text-midground">
              reconnect
            </button>
          )}
        </div>
      )}

      {/* Composer */}
      <div className="shrink-0 border-t border-current/20 p-2">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={1}
            placeholder={state === "open" ? "Message Rolly…" : "Connecting…"}
            disabled={state !== "open"}
            className={cn(
              "min-h-9 max-h-40 flex-1 resize-none rounded-md border border-current/20 bg-background-base/60 px-2.5 py-2",
              "text-sm text-text-primary placeholder:text-text-tertiary",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
              "disabled:opacity-50",
            )}
          />
          <Button
            size="icon"
            onClick={send}
            disabled={state !== "open" || busy || !draft.trim()}
            aria-label="Send"
            className="h-9 w-9 shrink-0"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </aside>
  );
}

// ── Subcomponents ──────────────────────────────────────────────────────

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[88%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-midground/10 px-3 py-2 text-sm text-text-primary">
          {turn.text}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      {turn.tools && turn.tools.length > 0 && (
        <div className="flex flex-col gap-1">
          {turn.tools.map((t) => (
            <ToolCall key={t.id} tool={t} />
          ))}
        </div>
      )}
      {turn.reasoning && (
        <details className="text-xs text-text-tertiary">
          <summary className="cursor-pointer select-none">reasoning</summary>
          <div className="mt-1 whitespace-pre-wrap border-l border-current/20 pl-2 opacity-80">{turn.reasoning}</div>
        </details>
      )}
      {(turn.text || turn.streaming) && <Markdown content={turn.text} streaming={turn.streaming} />}
    </div>
  );
}

function PromptView({
  prompt,
  onRespond,
}: {
  prompt: PendingPrompt;
  onRespond: (answer: string, approve?: boolean) => void;
}) {
  const [value, setValue] = useState("");
  const isApproval = prompt.kind === "approval";
  const isSecret = prompt.kind === "sudo" || prompt.kind === "secret";
  return (
    <div className="shrink-0 border-t border-warning/30 bg-warning/5 px-3 py-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-warning">
        <ShieldAlert className="h-3.5 w-3.5" />
        {prompt.kind} request
      </div>
      <div className="mb-2 whitespace-pre-wrap text-xs text-text-secondary">{prompt.text}</div>
      {isApproval ? (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => onRespond("approve", true)}>
            Approve
          </Button>
          <Button size="sm" outlined onClick={() => onRespond("deny", false)}>
            Deny
          </Button>
        </div>
      ) : (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            onRespond(value);
            setValue("");
          }}
        >
          <input
            type={isSecret ? "password" : "text"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
            className="min-w-0 flex-1 rounded-md border border-current/20 bg-background-base/60 px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground"
          />
          <Button size="sm" type="submit">
            Send
          </Button>
        </form>
      )}
    </div>
  );
}

// ── Pure helpers ───────────────────────────────────────────────────────

/** Repaint resumed gateway history into chat turns (see _history_to_messages). */
function historyToTurns(
  history: Array<{ role: string; text?: string; name?: string; context?: string }>,
): Turn[] {
  const turns: Turn[] = [];
  for (const m of history) {
    if (m.role === "user") {
      let text = m.text ?? "";
      const split = text.lastIndexOf(CTX_MARKER);
      if (split >= 0) text = text.slice(split + CTX_MARKER.length);
      turns.push({ id: nextId("u"), role: "user", text });
    } else if (m.role === "assistant") {
      turns.push({ id: nextId("a"), role: "assistant", text: m.text ?? "" });
    } else if (m.role === "tool") {
      // Attach the tool to the most recent assistant turn, or a standalone one.
      const tool: ToolEntry = {
        kind: "tool",
        id: nextId("tool"),
        tool_id: nextId("tid"),
        name: m.name ?? "tool",
        context: m.context,
        status: "done",
        startedAt: 0,
      };
      const last = turns[turns.length - 1];
      if (last && last.role === "assistant") {
        last.tools = [...(last.tools ?? []), tool];
      } else {
        turns.push({ id: nextId("a"), role: "assistant", text: "", tools: [tool] });
      }
    }
    // system messages are dropped from the visible transcript
  }
  return turns;
}

/** Drive the gateway session's auto-approve (yolo) to the desired state. */
async function reconcileYolo(gw: GatewayClient, sid: string, desired: boolean): Promise<void> {
  try {
    // config.set key:yolo is a toggle returning the new value ("1"/"0").
    const res = await gw.request<{ value?: string }>("config.set", { session_id: sid, key: "yolo" });
    const isOn = res?.value === "1";
    if (isOn !== desired) {
      await gw.request("config.set", { session_id: sid, key: "yolo" });
    }
  } catch {
    /* best-effort — auto-approve simply stays at its current state */
  }
}
