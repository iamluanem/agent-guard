"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export const BASE = "/api/ag";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: init?.method ?? "GET",
    headers: init?.body !== undefined ? { "content-type": "application/json" } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = data?.message ?? (Array.isArray(data?.detail) ? data.detail.map((d: { msg: string }) => d.msg).join("; ") : data?.detail) ?? res.statusText;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

/** Fetch + refetch whenever the live event stream reports activity. */
export function useApi<T>(path: string | null, { live = true }: { live?: boolean } = {}) {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string | null }>(
    { path: null, data: null, error: null });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(async () => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    api<T>(path).then(
      (data) => !cancelled && setState({ path, data, error: null }),
      (e: Error) => !cancelled && setState((s) => ({ path, data: s.path === path ? s.data : null, error: e.message })),
    );
    return () => { cancelled = true; };
  }, [path, nonce]);

  useEffect(() => {
    if (!live || !path) return;
    return onLiveEvent(() => setNonce((n) => n + 1));
  }, [live, path]);

  const fresh = state.path === path;
  return {
    data: fresh ? state.data : null,
    error: fresh ? state.error : null,
    loading: !fresh || (state.data === null && state.error === null),
    reload,
  };
}

/** Read a query parameter once, hydration-safe (null during prerender). */
export function useQueryParam(name: string): string | null {
  return useSyncExternalStore(
    () => () => undefined,
    () => new URLSearchParams(window.location.search).get(name),
    () => null,
  );
}

// ---- one shared EventSource for the whole app ----
type Listener = (e: LiveEvent) => void;
export type LiveEvent = { id: number; type: string; entity_id: string; correlation_id: string; created_at: string; payload: Record<string, unknown> };
const listeners = new Set<Listener>();
let source: EventSource | null = null;
let debounce: ReturnType<typeof setTimeout> | null = null;
const pending: LiveEvent[] = [];

function ensureSource() {
  if (source || typeof window === "undefined") return;
  source = new EventSource(`${BASE}/v1/stream`);
  source.onmessage = (msg: MessageEvent) => {
    try {
      pending.push(JSON.parse(msg.data));
    } catch {
      return;
    }
    if (debounce) return;
    debounce = setTimeout(() => {
      debounce = null;
      const batch = pending.splice(0);
      batch.forEach((ev) => listeners.forEach((l) => l(ev)));
    }, 300);
  };
  source.onerror = () => {
    source?.close();
    source = null;
    setTimeout(ensureSource, 3000);
  };
}

export function onLiveEvent(l: Listener): () => void {
  ensureSource();
  listeners.add(l);
  return () => listeners.delete(l);
}
