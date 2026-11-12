"use client";

import { useEffect, useState } from "react";

/**
 * A value fetched once per page load and shared by every component that reads it. `use()`
 * is `empty` until it loads, and the loaded value from the first render of any component
 * mounted after that; a failed load is retried by the next reader. `invalidate()` makes the
 * next reader fetch it again.
 */
export function cached<T>(load: () => Promise<T>, empty: T) {
  let cache: Promise<T> | null = null;
  let loaded: { value: T } | null = null;

  function use(): T {
    const [value, setValue] = useState<T>(loaded ? loaded.value : empty);
    useEffect(() => {
      let active = true;
      cache ??= load().then(
        (value) => {
          loaded = { value };
          return value;
        },
        (error: unknown) => {
          cache = null;
          throw error;
        },
      );
      cache.then(
        (value) => active && setValue(value),
        () => undefined,
      );
      return () => {
        active = false;
      };
    }, []);
    return value;
  }

  return {
    use,
    invalidate: () => {
      cache = null;
    },
  };
}

/** A list fetched once per page load (`cached`): empty until it loads. */
export function cachedList<T>(load: () => Promise<T[]>) {
  return cached<readonly T[]>(load, []);
}
