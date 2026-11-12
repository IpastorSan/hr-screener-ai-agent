// Mirrors the recruiter response models in backend/src/sazon/api/schemas.py
// (OpenAPI: CandidatePage, CandidateItem, CandidateDetail, CandidatePatch, ScreeningExport,
// LocationOut) and the analytics models in backend/src/sazon/api/analytics.py (AnalyticsOut).

export type Country = "ES" | "MX";
export type Language = "es" | "en";
export type Channel = "web" | "voice";

/** Screening result, set by the engine. `withdrawn`: qualified, then cancelled the interview. */
export type ScreeningStatus = "in_progress" | "qualified" | "disqualified" | "abandoned" | "withdrawn";

/** The recruiter's own progress with the candidate, separate from the screening result. */
export type ReviewStatus = "new" | "contacted" | "hired" | "rejected";

export type Availability = "full_time" | "part_time" | "weekends";
export type Schedule = "morning" | "afternoon" | "evening" | "flexible";
export type Sentiment = "neutral" | "positive" | "confused" | "frustrated";
export type Platform =
  | "glovo"
  | "uber_eats"
  | "just_eat"
  | "rappi"
  | "didi_food"
  | "deliveroo"
  | "amazon_flex"
  | "other";

/** Internal chatbot QA signals. Never a candidate attribute. */
export type QualityFlag =
  | "unresolved_field"
  | "frustration"
  | "abuse"
  | "unknown_faq"
  | "injection_suspected"
  | "gate_flag";

export type Stage =
  | "consent"
  | "name"
  | "contact"
  | "license"
  | "city"
  | "availability"
  | "schedule"
  | "experience"
  | "start_date"
  | "confirm"
  | "mode"
  | "scheduling"
  | "done";

export type Outcome = "qualified" | "disqualified" | "abandoned" | "declined_consent" | "withdrawn";

export type Note = { text: string; created_at: string };

export type CandidateItem = {
  id: string;
  name: string | null;
  phone: string | null;
  email: string | null;
  status: ScreeningStatus;
  review_status: ReviewStatus;
  outcome_reason: string | null;
  location_id: string | null;
  location_name: string | null;
  city: string | null;
  country: Country | null;
  language: Language | null;
  sentiment: Sentiment | null;
  channel: Channel;
  /** In the talent pool ("Bolsa de candidatos"): agreed to keep their data for future vacancies. */
  talent_pool: boolean;
  availability: Availability[] | null;
  /** One or more blocks; `flexible` stands alone. */
  schedule: Schedule[] | null;
  experience_years: number | null;
  platforms: Platform[] | null;
  start_date: string | null;
  late_start_days: number | null;
  quality_flags: QualityFlag[];
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  /** The candidate's latest message or reply in any of their conversations. */
  last_activity_at: string;
  /** Start of the booked interview (UTC), if any. */
  interview_at?: string | null;
  /** The same start, ISO 8601 in the location's time zone. */
  interview_local_start?: string | null;
  /** The recruiter running the booked video interview, if any. */
  recruiter_id?: string | null;
  recruiter_name?: string | null;
  /** In person: the location's manager who runs the interview. */
  interviewer_name?: string | null;
  interviewer_email?: string | null;
  /** How the booked interview happens, if there is one. */
  interview_mode?: InterviewMode | null;
};

/** Candidates per screening status for the page's filters, ignoring `status` itself. */
export type StatusCounts = Record<ScreeningStatus | "all", number>;

export type CandidatePage = {
  items: CandidateItem[];
  total: number;
  page: number;
  page_size: number;
  counts: StatusCounts;
};

export type LocationOption = { location_id: string; name: string; city: string; country: Country };

export type CandidateProfile = {
  full_name: string | null;
  phone: string | null;
  email: string | null;
  has_license: boolean | null;
  location_id: string | null;
  availability: Availability[] | null;
  /** One or more blocks; `flexible` stands alone. */
  schedule: Schedule[] | null;
  experience_years: number | null;
  platforms: Platform[] | null;
  platform_other: string | null;
  start_date: string | null;
  late_start_days: number | null;
  /** The interview mode the candidate chose (or the only one there was). */
  interview_mode?: InterviewMode | null;
  /** Confirmed more years than a platform they named has existed in the country. */
  experience_unverified?: boolean | null;
};

export type CandidateSummary = {
  lines: string[];
  key_fields: Record<string, string>;
  flags: string[];
};

export type ContextIn = {
  country: Country | null;
  location_id: string | null;
  timezone: string | null;
  locale: string | null;
};

/** The engine's own models serialized as JSON; shown raw in the transcript. */
export type JsonObject = Record<string, unknown>;

/** What retrieval found for an answered question: `faq:<id>` or `doc:<id>#<section>`. */
export type SourceOut = { source: string; label: string; score: number | null };

export type TurnOut = {
  id: number;
  role: "agent" | "candidate" | "system";
  text: string | null;
  ui: JsonObject | null;
  extraction: JsonObject | null;
  action: JsonObject | null;
  model: string | null;
  latency_ms: number | null;
  cost_usd: number | null;
  fallback_used: boolean;
  /** Agent turns that answered a question; recruiters only. */
  sources?: SourceOut[];
  created_at: string;
};

export type ConversationOut = {
  id: string;
  channel: Channel;
  stage: Stage;
  outcome: Outcome | null;
  disqualify_reason: string | null;
  drop_off_stage: Stage | null;
  quality_flags: QualityFlag[];
  context: ContextIn;
  started_at: string;
  ended_at: string | null;
  last_activity_at: string;
  /** A voice call's conversation in ElevenLabs (its recording and transcript there). */
  elevenlabs_conversation_id?: string | null;
  turns: TurnOut[];
  /** Questions neither the FAQ nor the documents answered, redacted. */
  faq_misses?: string[];
};

/** The booked interview. `local_start` is ISO 8601 in the location's time zone. */
export type BookingOut = {
  slot_id: string;
  starts_at: string;
  ends_at: string;
  local_start: string;
  location_id: string;
  location_name: string | null;
  address: string | null;
  booked_at: string;
  mode?: InterviewMode | null;
  meet_url?: string | null;
  recruiter_id?: string | null;
  recruiter_name?: string | null;
  /** In person: the location's manager who runs the interview. */
  interviewer_name?: string | null;
  interviewer_email?: string | null;
};

export type CandidateDetail = CandidateItem & {
  booking?: BookingOut | null;
  profile: CandidateProfile;
  summary: CandidateSummary | null;
  notes: Note[];
  conversations: ConversationOut[];
};

export type CandidatePatch = { status?: ReviewStatus; note?: string };

export type CandidateFilters = {
  status?: ScreeningStatus;
  review_status?: ReviewStatus;
  location_id?: string;
  country?: Country;
  /** "Bolsa de candidatos": agreed to keep their data for future vacancies. */
  talent_pool?: "true" | "false";
  sentiment?: Sentiment;
  quality_flag?: QualityFlag;
  /** Name, email or phone. */
  q?: string;
  /** The assigned interviewer: a recruiter id, or `me`. */
  recruiter_id?: string;
};

/** The list filtered by the agent's fields (`AgentSpec.filters`): an option by query parameter. */
export type FieldFilters = Record<string, string>;

export type CandidateQuery = CandidateFilters & {
  /** Descending with a leading `-`: the fixed keys, or a column the agent spec sorts by
   * (`SpecColumn.sort`). */
  sort?: string;
  page?: number;
  page_size?: number;
};

// --- analytics ---

export type DisqualifyReason =
  | "no_license"
  | "license_in_progress"
  | "license_not_valid"
  | "out_of_area"
  | "policy_violation"
  | "unresolved_name"
  | "unresolved_license"
  | "unresolved_city"
  | "no_contact";

/** A screening stage, or `qualified` for the funnel's last step. */
export type FunnelStage = Stage | "qualified";

export type Breakdown = {
  key: string;
  started: number;
  consented: number;
  completed: number;
  qualified: number;
  abandoned: number;
  completion_rate: number | null;
  qualified_rate: number | null;
};

export type Analytics = {
  totals: {
    started: number;
    consented: number;
    completed: number;
    qualified: number;
    disqualified: number;
    abandoned: number;
    /** Qualified, then cancelled the interview: counted in `completed`, not in `qualified`. */
    withdrawn: number;
    declined_consent: number;
    in_progress: number;
  };
  rates: {
    completion_rate: number | null;
    qualified_rate: number | null;
    abandonment_rate: number | null;
    withdrawal_rate: number | null;
    consent_decline_rate: number | null;
  };
  funnel: { stage: FunnelStage; count: number }[];
  drop_off: { stage: Stage; count: number }[];
  top_drop_off_stage: Stage | null;
  durations: {
    completed: number;
    avg_seconds: number | null;
    p50_seconds: number | null;
    avg_turns: number | null;
    p50_turns: number | null;
  };
  disqualification_reasons: { reason: DisqualifyReason; count: number }[];
  /** `recovery_rate` is null while no conversation has been nudged. */
  reengagement: { nudged: number; recovered: number; recovery_rate: number | null };
  by_channel: Breakdown[];
  by_country: Breakdown[];
  by_language: Breakdown[];
  sentiment: { sentiment: Sentiment; count: number }[];
  quality: {
    flagged: number;
    flagged_rate: number | null;
    flags: { flag: QualityFlag; count: number }[];
    usage: {
      model_calls: number;
      fallback_rate: number | null;
      total_cost_usd: number | null;
      cost_per_conversation_usd: number | null;
      cost_per_completed_usd: number | null;
      exchanges: number;
      avg_latency_ms: number | null;
      p50_latency_ms: number | null;
      p95_latency_ms: number | null;
    };
  };
};

/** A group of similar questions Ana could not answer; `id` is the CSV export's `group`. */
export type FaqMissGroup = {
  id: number;
  /** The most frequent wording in the group. */
  question: string;
  count: number;
  last_seen: string;
  countries: Country[];
  languages: Language[];
  /** The latest conversations that asked it, at most 3. */
  examples: { conversation_id: string; candidate_id: string | null }[];
};

export type FaqMisses = {
  total: number;
  /** More questions in the range than were read (the latest 2,000). */
  truncated: boolean;
  /** Grouped by meaning; false: by exact text, when the retrieval model is not available. */
  by_similarity: boolean;
  groups: FaqMissGroup[];
};

export type AnalyticsFilters = {
  /** First day, `YYYY-MM-DD`. */
  from?: string;
  /** Last day, `YYYY-MM-DD`. */
  to?: string;
  channel?: Channel;
  country?: Country;
  language?: Language;
};

// --- recruiters (backend/src/sazon/api/schemas.py: MeOut, RecruiterOut, InterviewItem) ---

export type InterviewMode = "onsite" | "video";
export type Weekday = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
/** Weekday to `[start, end]` blocks in HH:MM, the recruiter's local time. */
export type WorkingHours = Partial<Record<Weekday, [string, string][]>>;

/** A recruiter's Google Calendar connection. `revoked`: Google no longer accepts the grant. */
export type CalendarConnection = {
  status: "connected" | "revoked" | "disconnected";
  google_email: string | null;
  connected_at: string | null;
};

/** `GET /api/auth/providers`: what sign-in and the calendar button can offer. */
export type AuthProviders = {
  google: boolean;
  domain: string;
  scheduling_source: "fixed" | "google" | "fake";
};

/** `GET /api/me`: the signed-in recruiter. */
export type Me = {
  id: string;
  name: string;
  email: string;
  is_admin: boolean;
  country: Country;
  timezone: string;
  calendar: CalendarConnection;
};

export type Recruiter = Me & {
  active: boolean;
  location_ids: string[];
  interview_modes: InterviewMode[];
  working_hours: WorkingHours;
  max_interviews_per_day: number;
  /** The candidates they interview beyond the locations: field id to options (a teacher's
   * subjects); a field not named, any answer. */
  scope: Record<string, string[]>;
  has_password: boolean;
  created_at: string;
  updated_at: string;
};

/** What an admin sets on a recruiter; `password` only to set a new one. */
export type RecruiterFields = {
  name?: string;
  password?: string;
  is_admin?: boolean;
  timezone?: string;
  location_ids?: string[];
  interview_modes?: InterviewMode[];
  working_hours?: WorkingHours;
  max_interviews_per_day?: number;
  scope?: Record<string, string[]>;
};

export type RecruiterCreate = RecruiterFields & { name: string; email: string; country: Country };
export type RecruiterPatch = RecruiterFields & { active?: boolean };

/** `GET /api/interviews`: an interview on a recruiter's agenda. */
export type InterviewItem = {
  booking_id: string;
  candidate_id: string;
  candidate_name: string | null;
  phone: string | null;
  email: string | null;
  status: ScreeningStatus;
  starts_at: string;
  ends_at: string;
  local_start: string;
  location_id: string;
  location_name: string | null;
  address: string | null;
  mode: InterviewMode | null;
  meet_url: string | null;
  /** Video: the recruiter who runs it. */
  recruiter_id: string | null;
  recruiter_name: string | null;
  /** In person: the location's manager who runs it; the recruiter only sees it. */
  interviewer_name?: string | null;
  interviewer_email?: string | null;
  /** The recruiter moved the event in Google Calendar to a time the rules would not offer. */
  calendar_issue?: CalendarIssue | null;
};

export type CalendarIssue = "past" | "all_day" | "outside_hours" | "clash" | "busy" | "daily_max";

// --- agent spec (GET /api/agent-spec) ---

/** How a field is read, asked and shown (`sazon.engine.spec.FieldType`). */
export type FieldType =
  | "person_name"
  | "contact"
  | "credential"
  | "yes_no"
  | "enum"
  | "multi_enum"
  | "years"
  | "date"
  | "location"
  | "free_text";

export type SpecOption = { value: string; label: string };

export type SpecField = {
  id: string;
  type: FieldType;
  /** `contact`: which one. */
  channel: "phone" | "email" | null;
  /** The profile attribute (and candidate column) with the value. */
  attr: string;
  /** The attribute kept with it: an `other`'s words, `years` that failed a check, a late start. */
  companion: string | null;
  label: string;
  /** `label` where a country names it otherwise. */
  country_labels: Partial<Record<Country, string>>;
  /** `enum`, `multi_enum` and yes/no answers. */
  options: SpecOption[];
  /** `multi_enum`: an empty choice. */
  none_label: string | null;
  funnel_step: Stage;
};

export type SpecColumn = { field: string; label: string; sort: string | null; wrap: boolean };

export type SpecFilter = { field: string; param: string; label: string; options: SpecOption[] };

export type AgentSpec = {
  agent: string;
  /** The language of every label. */
  language: Language;
  /** In the order they are asked. */
  fields: SpecField[];
  columns: SpecColumn[];
  filters: SpecFilter[];
  funnel: { step: FunnelStage; label: string }[];
  /** The fields a recruiter may be scoped on beyond the locations (`Recruiter.scope`). */
  interview_scope: string[];
};
