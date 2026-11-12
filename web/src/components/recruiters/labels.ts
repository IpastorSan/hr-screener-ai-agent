import type {
  CalendarIssue,
  Channel,
  Country,
  DisqualifyReason,
  FunnelStage,
  InterviewMode,
  Language,
  Outcome,
  QualityFlag,
  ReviewStatus,
  ScreeningStatus,
  Sentiment,
} from "@/lib/recruiter-types";

export const SCREENING_LABEL: Record<ScreeningStatus, string> = {
  qualified: "Apto",
  disqualified: "No apto",
  in_progress: "En curso",
  abandoned: "Abandonó",
  withdrawn: "Retirada",
};

/** A qualified candidate with a booked interview. */
export const INTERVIEW_LABEL = "Entrevista";

export const SCREENING_TAB_LABEL: Record<ScreeningStatus | "all", string> = {
  all: "Todos",
  qualified: "Aptos",
  disqualified: "No aptos",
  in_progress: "En curso",
  abandoned: "Abandonados",
  withdrawn: "Retiradas",
};

export const REVIEW_LABEL: Record<ReviewStatus, string> = {
  new: "Nuevo",
  contacted: "Contactado",
  hired: "Contratado",
  rejected: "Descartado",
};

export const SENTIMENT_LABEL: Record<Sentiment, string> = {
  positive: "Positivo",
  neutral: "Neutral",
  confused: "Confundido",
  frustrated: "Frustrado",
};

export const QUALITY_FLAG_LABEL: Record<QualityFlag, string> = {
  unresolved_field: "Campo sin resolver",
  frustration: "Frustración",
  abuse: "Abuso",
  unknown_faq: "Pregunta sin respuesta",
  injection_suspected: "Posible inyección",
  gate_flag: "Requisito pendiente",
};

export const COUNTRY_LABEL: Record<Country, string> = { ES: "España", MX: "México" };

/** Candidates who agreed to keep their data for future vacancies (asked out of area). */
export const TALENT_POOL_LABEL = "Bolsa de candidatos";

export const MODE_LABEL: Record<InterviewMode, string> = { onsite: "Presencial", video: "Videollamada" };

/** Who runs a booked interview: the recruiter by video, the location's manager in person. */
export function interviewerLabel(item: {
  recruiter_name?: string | null;
  interviewer_name?: string | null;
  interviewer_email?: string | null;
}): string | null {
  if (item.recruiter_name) return item.recruiter_name;
  const manager = item.interviewer_name || item.interviewer_email;
  return manager ? `${manager} (gerente)` : null;
}

/** Why a time set in Google Calendar breaks the scheduling rules (SAZ-40). */
export const CALENDAR_ISSUE_LABEL: Record<CalendarIssue, string> = {
  past: "Movida al pasado",
  all_day: "Evento de día completo",
  outside_hours: "Fuera de tu horario",
  clash: "Choca con otra entrevista",
  busy: "Choca con tu calendario",
  daily_max: "Supera tu máximo diario",
};

export const LANGUAGE_LABEL: Record<Language, string> = { es: "Español", en: "Inglés" };

export const CHANNEL_LABEL: Record<Channel, string> = { web: "Chat web", voice: "Voz" };

/** The engine's own stages, before and after the questions. The agent's question steps are
 * named by `GET /api/agent-spec` (`useStepLabel`). */
export const ENGINE_STAGE_LABEL: Partial<Record<FunnelStage, string>> = {
  consent: "Consentimiento",
  confirm: "Confirmación",
  mode: "Modalidad",
  scheduling: "Entrevista",
  done: "Terminada",
  qualified: "Apto",
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  qualified: "Apto",
  disqualified: "No apto",
  abandoned: "Abandonó",
  declined_consent: "Rechazó el consentimiento",
  withdrawn: "Retirada",
};

export { formatExperience } from "@/lib/experience";

export const REASON_LABEL: Record<DisqualifyReason, string> = {
  no_license: "Sin carnet",
  license_in_progress: "Carnet en trámite o de aprendiz",
  license_not_valid: "Carnet no vigente",
  out_of_area: "Fuera de zona",
  policy_violation: "Intento de manipulación",
  unresolved_name: "Sin nombre completo",
  unresolved_license: "Carnet sin confirmar",
  unresolved_city: "Zona sin confirmar",
  no_contact: "Sin contacto",
};

const dateFormat = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const dateTimeFormat = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium", timeStyle: "short" });
const timeFormat = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit" });

/** A calendar date (`YYYY-MM-DD`), shown without shifting it to the viewer's time zone. */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return dateFormat.format(new Date(`${iso}T00:00:00Z`));
}

const localDayFormat = new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });

/** "Lunes, 26 de octubre": the local date of an ISO 8601 time with its offset, as written there. */
export function formatLocalDay(localIso: string): string {
  const day = localDayFormat.format(new Date(`${localIso.slice(0, 10)}T12:00:00Z`));
  return day.charAt(0).toUpperCase() + day.slice(1);
}

export function formatDateTime(iso: string): string {
  return dateTimeFormat.format(new Date(iso));
}

export function formatTime(iso: string): string {
  return timeFormat.format(new Date(iso));
}

const relative = new Intl.RelativeTimeFormat("es-ES", { numeric: "auto" });
const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

export function formatRelative(iso: string, now: number = Date.now()): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  for (const [unit, size] of STEPS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "ahora mismo";
}

const money = new Intl.NumberFormat("es-ES", { style: "currency", currency: "USD", maximumFractionDigits: 5 });

export function formatCost(usd: number): string {
  return money.format(usd);
}
