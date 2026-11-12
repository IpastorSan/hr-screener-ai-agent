import { format, subDays } from "date-fns";
import type { AnalyticsFilters, Country, Language } from "@/lib/recruiter-types";

export const RANGES = { "7d": "7 días", "30d": "30 días", "90d": "90 días", all: "Todo" } as const;
export type Range = keyof typeof RANGES | "custom";
export const DEFAULT_RANGE: Range = "30d";

/** What the URL holds: a preset range, or `custom` with its own days. */
export type DashboardQuery = { range: Range; from?: string; to?: string; country?: Country; language?: Language };

const DAYS: Record<"7d" | "30d" | "90d", number> = { "7d": 7, "30d": 30, "90d": 90 };

function isoDay(day: Date): string {
  return format(day, "yyyy-MM-dd");
}

/** The dashboard's URL contract. Anything unknown falls back to the default. */
export function readQuery(params: URLSearchParams): DashboardQuery {
  const range = params.get("range");
  const country = params.get("country");
  const language = params.get("language");
  return {
    // `Object.hasOwn`, not `in`: `?range=constructor` must not pass for a range.
    range: range && (Object.hasOwn(RANGES, range) || range === "custom") ? (range as Range) : DEFAULT_RANGE,
    from: params.get("from") || undefined,
    to: params.get("to") || undefined,
    country: country === "ES" || country === "MX" ? (country as Country) : undefined,
    language: language === "es" || language === "en" ? (language as Language) : undefined,
  };
}

/** The API filters for a query. Preset ranges end today, in the viewer's calendar. */
export function toFilters(query: DashboardQuery, today: Date = new Date()): AnalyticsFilters {
  const { range, country, language } = query;
  if (range === "all") return { country, language };
  if (range === "custom") return { from: query.from, to: query.to, country, language };
  return { from: isoDay(subDays(today, DAYS[range] - 1)), to: isoDay(today), country, language };
}
