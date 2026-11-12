import { API_URL, api, ApiError } from "@/lib/api";
import type {
  AgentSpec,
  Analytics,
  AuthProviders,
  AnalyticsFilters,
  CandidateDetail,
  CandidatePage,
  CandidatePatch,
  CandidateQuery,
  FaqMisses,
  FieldFilters,
  InterviewItem,
  LocationOption,
  Me,
  Recruiter,
  RecruiterCreate,
  RecruiterPatch,
  ScreeningStatus,
} from "@/lib/recruiter-types";

/** Dispatched on `window` when the API answers 401: the session ended or never existed. */
export const SIGNED_OUT_EVENT = "sazon:signed-out";

/**
 * A dashboard API call: sends the session cookie (the API may be on another origin in
 * development) and announces a 401 so the session gate can send the recruiter to sign in.
 */
export async function recruiterApi<T>(path: string, init: Parameters<typeof api>[1] = {}): Promise<T> {
  try {
    return await api<T>(path, { credentials: "include", ...init });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    }
    throw error;
  }
}

export const SCREENING_STATUSES = ["qualified", "disqualified", "in_progress", "abandoned", "withdrawn"] as const satisfies readonly ScreeningStatus[];

function search(query: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function listCandidates(query: CandidateQuery, fields: FieldFilters, init?: RequestInit): Promise<CandidatePage> {
  return recruiterApi<CandidatePage>(`/api/candidates${search({ ...query, ...fields })}`, init);
}

export function getAgentSpec(init?: RequestInit): Promise<AgentSpec> {
  return recruiterApi<AgentSpec>("/api/agent-spec", init);
}

export function listLocations(init?: RequestInit): Promise<LocationOption[]> {
  return recruiterApi<LocationOption[]>("/api/locations", init);
}

export function getAnalytics(filters: AnalyticsFilters, init?: RequestInit): Promise<Analytics> {
  return recruiterApi<Analytics>(`/api/analytics${search(filters)}`, init);
}

export function getCandidate(id: string, init?: RequestInit): Promise<CandidateDetail> {
  return recruiterApi<CandidateDetail>(`/api/candidates/${encodeURIComponent(id)}`, init);
}

export function updateCandidate(id: string, patch: CandidatePatch): Promise<CandidateDetail> {
  return recruiterApi<CandidateDetail>(`/api/candidates/${encodeURIComponent(id)}`, { method: "PATCH", json: patch });
}

/** Fetch the ATS export and hand it to the browser as a .json download. */
export async function downloadExport(id: string): Promise<void> {
  const body = await recruiterApi<unknown>(`/api/candidates/${encodeURIComponent(id)}/export`);
  saveFile(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }), `candidato-${id}.json`);
}

export function getFaqMisses(filters: AnalyticsFilters, init?: RequestInit): Promise<FaqMisses> {
  return recruiterApi<FaqMisses>(`/api/analytics/faq-misses${search(filters)}`, init);
}

/** Fetch the unanswered questions as CSV, one row per question with its group, as a download. */
export async function downloadFaqMisses(filters: AnalyticsFilters): Promise<void> {
  const body = await recruiterApi<string>(`/api/analytics/faq-misses.csv${search(filters)}`, { headers: { accept: "text/csv" } });
  // `fetch` drops the byte order mark; spreadsheet apps need it to read the accents as UTF-8.
  saveFile(new Blob(["﻿", body], { type: "text/csv;charset=utf-8" }), "preguntas-sin-respuesta.csv");
}

function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// --- session ---

/** Sign in with email and password. 401 for any wrong pair, 429 after too many attempts. */
export function login(email: string, password: string): Promise<Me> {
  return api<Me>("/api/auth/login", { method: "POST", json: { email, password }, credentials: "include" });
}

export function logout(): Promise<void> {
  return api<void>("/api/auth/logout", { method: "POST", json: {}, credentials: "include" });
}

/** Whether Google sign-in and Calendar are configured; public. */
export function getAuthProviders(init?: RequestInit): Promise<AuthProviders> {
  return api<AuthProviders>("/api/auth/providers", init);
}

/** Top-level navigation targets (Google's consent page comes back to the API, then here). */
export function googleSignInUrl(next: string): string {
  return `${API_URL}/api/auth/google/start?next=${encodeURIComponent(next)}`;
}

export function googleConnectUrl(next: string): string {
  return `${API_URL}/api/auth/google/connect?next=${encodeURIComponent(next)}`;
}

/** Revoke the signed-in recruiter's calendar grant at Google and forget it. */
export function disconnectCalendar(): Promise<void> {
  return recruiterApi<void>("/api/auth/google/calendar", { method: "DELETE" });
}

/** The signed-in recruiter; rejects with a 401 `ApiError` without a session. */
export function getMe(init?: RequestInit): Promise<Me> {
  return api<Me>("/api/me", { credentials: "include", ...init });
}

// --- recruiters and interviews ---

export function listInterviews(init?: RequestInit): Promise<InterviewItem[]> {
  return recruiterApi<InterviewItem[]>("/api/interviews", init);
}

export function listRecruiters(init?: RequestInit): Promise<Recruiter[]> {
  return recruiterApi<Recruiter[]>("/api/recruiters", init);
}

export function createRecruiter(body: RecruiterCreate): Promise<Recruiter> {
  return recruiterApi<Recruiter>("/api/recruiters", { method: "POST", json: body });
}

export function updateRecruiter(id: string, patch: RecruiterPatch): Promise<Recruiter> {
  return recruiterApi<Recruiter>(`/api/recruiters/${encodeURIComponent(id)}`, { method: "PATCH", json: patch });
}
