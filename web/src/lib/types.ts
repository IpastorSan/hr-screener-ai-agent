export type Option = { value: string; label: string };

/** `startsAt` is ISO 8601 in the location's local time with its offset; `time` is "10:30". */
export type Slot = { id: string; startsAt: string; label: string; location: string; time?: string | null };

export type InterviewMode = "onsite" | "video";

/** The booked interview shown under Ana's confirmation. `meetUrl`: the Google Meet link of a
 * video interview, once its calendar event exists. */
export type Booking = {
  slotId: string;
  startsAt: string;
  label: string;
  location: string;
  address: string;
  mode?: InterviewMode | null;
  meetUrl?: string | null;
};

export type ConfirmField = { label: string; value: string };

/** A page a widget links to, opened in a new tab (the privacy notice on the consent step). */
export type WidgetLink = { href: string; label: string };

export type CountryCode = "ES" | "MX";

export type DialCountry = { code: CountryCode; dial: string; label: string };

export type Ui =
  | { kind: "quick_replies"; options: Option[]; link?: WidgetLink | null }
  | { kind: "multi_select"; options: Option[]; min?: number; max?: number; exclusive?: string[] }
  | { kind: "slots"; slots: Slot[] }
  | { kind: "confirm"; fields: ConfirmField[] }
  | { kind: "phone"; countries: DialCountry[]; selected: CountryCode }
  | ({ kind: "booking" } & Booking);

export type UiKind = Ui["kind"];

export type UiAction =
  | { kind: "quick_replies"; value: string }
  | { kind: "multi_select"; value: string[] }
  | { kind: "slots"; value: string }
  | { kind: "confirm"; value: boolean }
  | { kind: "phone"; value: { country: CountryCode; number: string } };

/** UI chrome copy: Spain Spanish (tú), Mexican Spanish (usted) or English. */
export type ChatLocale = "es" | "es-MX" | "en";

export type WidgetProps<K extends UiKind> = {
  ui: Extract<Ui, { kind: K }>;
  onAction: (action: Extract<UiAction, { kind: K }>) => void;
  disabled?: boolean;
  locale?: ChatLocale;
};

// --- candidate chat API (backend/src/sazon/api/schemas.py) ---

import type { Language, Outcome, ScreeningStatus, Stage } from "./recruiter-types";

export type { Language, Outcome, Stage };

export type ChatState = {
  stage: Stage;
  outcome: Outcome | null;
  language: Language;
  country: CountryCode;
  ended: boolean;
  /** Closed for required data that could not be resolved: a new application can start at once. */
  restartable?: boolean;
  /** Just closed at a gate (license): the next message may still reopen it, so the composer stays open for it. */
  reopenable?: boolean;
  /** The candidate took the offer to pause and has not written since: no nudges. */
  paused: boolean;
};

export type ChatReply = { text: string; ui: Ui | null };

export type StartContext = {
  country?: CountryCode;
  location_id?: string;
  timezone?: string;
  locale?: string;
};

export type StartOut = { conversation_id: string; reply: ChatReply; state: ChatState };

export type MessageIn = { text: string } | { action: UiAction };

/** `candidate_text`: the candidate's message as the transcript stores it, or null when not stored. */
export type MessageOut = {
  conversation_id: string;
  resumed: boolean;
  candidate_text: string | null;
  reply: ChatReply;
  state: ChatState;
};

/** `nudge`: Ana's re-engagement message after the candidate went quiet. */
export type TranscriptTurn = {
  role: "agent" | "candidate";
  text: string;
  ui: Ui | null;
  nudge?: boolean;
  created_at: string;
};

/** `POST /api/conversations/{id}/nudge`. */
export type NudgeOut = { reply: ChatReply; state: ChatState };

/** `POST /api/conversations/{id}/voice`: what `startSession` needs for this conversation. */
export type VoiceSessionOut = {
  conversation_id: string;
  agent_id: string | null;
  conversation_token: string | null;
  language: Language;
  voice_id: string | null;
  opening_line: string;
  dynamic_variables: Record<string, string>;
  custom_llm_extra_body: Record<string, string>;
};

export type TranscriptOut = {
  conversation_id: string;
  channel: string;
  state: ChatState;
  turns: TranscriptTurn[];
  /** A returning candidate's application was resumed: the chat or call carries on in this conversation. */
  continued_in?: string | null;
};

/** A candidate row in an insight table (`CandidateRow` in backend/src/sazon/recruiter_assistant.py). */
export type CandidateRow = {
  id: string;
  name: string;
  city?: string | null;
  status: ScreeningStatus;
  experience_years?: number | null;
  start_date?: string | null;
};

export type Insight =
  | { kind: "stat"; label: string; value: string | number; delta?: number; hint?: string }
  | { kind: "candidates"; title?: string; candidates: CandidateRow[] }
  | { kind: "bar"; title: string; data: { label: string; value: number }[] }
  | { kind: "funnel"; title: string; steps: { stage: string; count: number }[] };

export type InsightKind = Insight["kind"];

export type InsightAction = { kind: "open_candidate"; candidateId: string };

export type InsightProps<K extends InsightKind> = {
  insight: Extract<Insight, { kind: K }>;
  onAction?: (action: InsightAction) => void;
};

/** `POST /api/assistant`: the recruiter assistant's answer. */
export type AskAnswer = { text: string; insights: Insight[] };

export type AskTurn = { role: "user" | "assistant"; text: string };
