// Pure formatting, free of runtime imports so `node --test` runs it.

const localShortFormat = new Intl.DateTimeFormat("es-ES", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

/** "jue, 22 oct · 09:00": an ISO 8601 time with its offset, at the time written there (the
 * location's), not shifted to the viewer's time zone. */
export function formatLocalShort(localIso: string): string {
  const day = localShortFormat.format(new Date(`${localIso.slice(0, 10)}T12:00:00Z`));
  return `${day} · ${localIso.slice(11, 16)}`;
}
