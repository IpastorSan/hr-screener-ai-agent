export type ExperienceLanguage = "es" | "en";

const UNITS = {
  es: { year: ["año", "años"], month: ["mes", "meses"], joiner: " y ", none: "Sin experiencia" },
  en: { year: ["year", "years"], month: ["month", "months"], joiner: " ", none: "No experience" },
} as const;

/**
 * Years and months, or only months under a year: "6 meses", "1 año y 6 meses", "2 años";
 * "6 months", "1 year 6 months". Experience is stored as years (a float) and shown to the
 * nearest month, the same as the chat's read-back (`format_years` in `sazon.engine.ui`).
 */
export function formatExperience(years: number | null, language: ExperienceLanguage = "es"): string {
  if (years === null) return "—";
  const units = UNITS[language];
  const total = Math.round(years * 12);
  if (total <= 0) return units.none;
  const whole = Math.floor(total / 12);
  const months = total % 12;
  const parts: string[] = [];
  if (whole) parts.push(`${whole} ${units.year[whole === 1 ? 0 : 1]}`);
  if (months) parts.push(`${months} ${units.month[months === 1 ? 0 : 1]}`);
  return parts.join(units.joiner);
}
