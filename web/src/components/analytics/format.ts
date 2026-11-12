const percent = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const usd = new Intl.NumberFormat("es-ES", { style: "currency", currency: "USD", maximumSignificantDigits: 3 });

export const EMPTY = "—";

export function formatPercent(share: number | null): string {
  return share === null ? EMPTY : percent.format(share);
}

/** "45 s", "9 min 50 s", "2 h 5 min". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return EMPTY;
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    const rest = total % 60;
    return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  }
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} h ${rest} min` : `${Math.floor(minutes / 60)} h`;
}

export function formatDecimal(value: number | null): string {
  return value === null ? EMPTY : decimal.format(value);
}

export function formatUsd(value: number | null): string {
  return value === null ? EMPTY : usd.format(value);
}

/** "850 ms" or "1,2 s". */
export function formatLatency(ms: number | null): string {
  if (ms === null) return EMPTY;
  return ms < 1000 ? `${Math.round(ms)} ms` : `${decimal.format(ms / 1000)} s`;
}

/** The unanswered questions card's subtitle: "7 preguntas sin respuesta, 3 temas distintos." */
export function faqMissesSummary(total: number, groups: number, truncated: boolean): string {
  if (total === 0) return "Ana ha sabido responder todas las preguntas de este periodo.";
  const questions = total === 1 ? "1 pregunta" : `${total.toLocaleString("es-ES")} preguntas`;
  const topics = groups === 1 ? "1 tema" : `${groups.toLocaleString("es-ES")} temas distintos`;
  const latest = truncated ? " (las más recientes)" : "";
  return `${questions}${latest} sin respuesta, ${topics}.`;
}
