/**
 * Page-context bus — the compact, route-aware payload the global Rolly
 * assistant sidebar reads so it knows what the user is currently looking at.
 *
 * Two bundles publish here: the main dashboard app (route + title) and the
 * Kanban IIFE plugin (rich card/list context). They live in separate bundles
 * and cannot share a React context, so the bus is a tiny window-backed
 * pub/sub. The effective context is a LAYERED merge:
 *
 *   route layer   — set by the app shell on every navigation ({route, title}).
 *   entity layer  — set by a page/plugin while a specific entity is in view
 *                   (e.g. an open Kanban card). Cleared on unmount / close.
 *
 * effective = { ...route, ...entity }. The entity layer wins where they
 * overlap; navigating to a different route auto-drops a stale entity layer so
 * the sidebar never reports a card the user already left.
 *
 * Nothing here sends data anywhere — it only mirrors visible UI state into a
 * structured object. The sidebar decides when (and whether) to forward a
 * compact slice of it to Rolly.
 */

export interface PageContext {
  /** Current dashboard route, normalized without a trailing slash (e.g. "/kanban"). */
  route: string;
  /** Owning plugin name when a plugin route is active (e.g. "kanban"). */
  plugin?: string;
  /** Kind of entity in view: "card" | "list" | "board" | "session" | etc. */
  entity_type?: string;
  /** Stable id of the entity in view (e.g. a card/task id). */
  entity_id?: string;
  /** Human title for the page or entity. */
  title?: string;
  /** Short, already-summarized description of the visible state (NOT a DOM dump). */
  summary?: string;
  /** Active filters/selection the user has applied (tenant, assignee, search, board). */
  selected_filters?: Record<string, unknown>;
  /** Explicit actions the assistant may offer for this context (advisory labels). */
  actions_available?: string[];
  /** Extra, context-specific fields (status, assignee, workspace_path, …). */
  [key: string]: unknown;
}

const EVENT_NAME = "hermes-page-context";

declare global {
  interface Window {
    /** Current effective page context (layered route+entity merge). */
    __HERMES_PAGE_CONTEXT__?: PageContext | null;
  }
}

let routeLayer: PageContext | null = null;
let entityLayer: PageContext | null = null;

function effective(): PageContext | null {
  if (!routeLayer && !entityLayer) return null;
  return { ...(routeLayer ?? {}), ...(entityLayer ?? {}) } as PageContext;
}

function emit(): void {
  const ctx = effective();
  if (typeof window !== "undefined") {
    window.__HERMES_PAGE_CONTEXT__ = ctx;
    window.dispatchEvent(new CustomEvent<PageContext | null>(EVENT_NAME, { detail: ctx }));
  }
}

/**
 * Publish the route layer — called by the app shell on navigation. A route
 * change to a different `route` drops any stale entity layer so the sidebar
 * cannot keep reporting an entity from the page the user just left.
 */
export function setRouteContext(ctx: PageContext): void {
  routeLayer = ctx;
  if (entityLayer && entityLayer.route !== ctx.route) {
    entityLayer = null;
  }
  emit();
}

/**
 * Publish (or clear, with null) the entity layer — called by a page/plugin
 * while a specific entity is in view. Pass null on unmount/close to revert to
 * the bare route context.
 */
export function setEntityContext(ctx: PageContext | null): void {
  entityLayer = ctx;
  emit();
}

/** Current effective page context, or null when nothing has been published. */
export function getPageContext(): PageContext | null {
  return effective();
}

/**
 * Subscribe to effective page-context changes. Fires immediately with the
 * current value, then on every change. Returns an unsubscribe function.
 */
export function subscribePageContext(
  cb: (ctx: PageContext | null) => void,
): () => void {
  const handler = (ev: Event) => cb((ev as CustomEvent<PageContext | null>).detail ?? null);
  window.addEventListener(EVENT_NAME, handler);
  cb(effective());
  return () => window.removeEventListener(EVENT_NAME, handler);
}
