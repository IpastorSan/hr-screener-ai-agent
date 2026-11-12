"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type Loaded<T> = { key: string; data?: T; error?: unknown };

/** A failed load of `key`: keeps the data only if it is `key`'s own, never another key's. */
export function failedLoad<T>(previous: Loaded<T> | null, key: string, error: unknown): Loaded<T> {
  return { key, data: previous?.key === key ? previous.data : undefined, error };
}

/**
 * Load `key`'s data, then reload it every `intervalMs` while the tab is visible and whenever
 * the window regains focus. While a new key loads, the previous data stays available so the
 * view can dim it instead of flashing a skeleton.
 */
export function usePolling<T>(key: string, load: (signal: AbortSignal) => Promise<T>, intervalMs = 15_000) {
  const [state, setState] = useState<Loaded<T> | null>(null);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    const controller = new AbortController();
    loadRef.current(controller.signal).then(
      (data) => setState({ key, data }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState((previous) => failedLoad(previous, key, error));
      },
    );
    return () => controller.abort();
  }, [key, tick]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") setTick((value) => value + 1);
    };
    const timer = window.setInterval(refresh, intervalMs);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [intervalMs]);

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  const error = state?.key === key ? state.error : undefined;
  return {
    data: state?.data,
    error,
    /** True until the current key has loaded at least once. */
    pending: state?.key !== key,
    /** The last refresh failed: `data` is what the current key had before it. */
    stale: error !== undefined && state?.data !== undefined,
    refresh,
  };
}
