import type { ChatReply, ChatState, MessageOut, Ui, UiAction } from "@/lib/types";

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

const numberOrUndefined = (value: unknown) => (typeof value === "number" ? value : undefined);

const isOptions = (value: unknown) =>
  Array.isArray(value) && value.every((o) => isObject(o) && typeof o.value === "string" && typeof o.label === "string");

const isLink = (value: unknown) => isObject(value) && typeof value.href === "string" && typeof value.label === "string";

/** A widget we know how to draw, or null so the message falls back to plain text. */
export function parseUi(value: unknown): Ui | null {
  if (!isObject(value)) return null;
  switch (value.kind) {
    case "quick_replies":
      // No options is fine when there is a link: the privacy notice after a policy close.
      if (!isOptions(value.options)) return null;
      return (value.options as unknown[]).length > 0 || isLink(value.link) ? (value as Ui) : null;
    case "multi_select":
      if (!isOptions(value.options) || (value.options as unknown[]).length === 0) return null;
      // The API sends unset bounds as null; the widget reads them as missing.
      return { ...(value as Ui), min: numberOrUndefined(value.min), max: numberOrUndefined(value.max) } as Ui;
    case "slots":
      return Array.isArray(value.slots) && value.slots.length > 0 ? (value as Ui) : null;
    case "confirm":
      return Array.isArray(value.fields) ? (value as Ui) : null;
    case "booking":
      return typeof value.slotId === "string" && typeof value.startsAt === "string" && typeof value.label === "string"
        ? (value as Ui)
        : null;
    case "phone":
      return Array.isArray(value.countries) && value.countries.length > 0 && typeof value.selected === "string"
        ? (value as Ui)
        : null;
    default:
      return null;
  }
}

export function parseReply(value: unknown): ChatReply {
  if (typeof value === "string") return { text: value, ui: null };
  if (isObject(value) && typeof value.text === "string") return { text: value.text, ui: parseUi(value.ui) };
  return { text: "", ui: null };
}

export function parseState(value: unknown): ChatState | null {
  if (!isObject(value) || typeof value.stage !== "string" || typeof value.language !== "string") return null;
  return value as ChatState;
}

export function parseDone(
  value: unknown,
): Pick<MessageOut, "conversation_id" | "resumed" | "candidate_text"> & { state: ChatState | null; reply: ChatReply | null } {
  if (!isObject(value)) return { conversation_id: "", resumed: false, candidate_text: null, state: null, reply: null };
  return {
    conversation_id: typeof value.conversation_id === "string" ? value.conversation_id : "",
    resumed: value.resumed === true,
    candidate_text: typeof value.candidate_text === "string" ? value.candidate_text : null,
    state: parseState(value.state),
    reply: isObject(value.reply) ? parseReply(value.reply) : null,
  };
}

/** What the candidate's bubble says after a widget tap, until the server returns the stored text. */
export function actionLabel(ui: Ui, action: UiAction, labels: { yes: string; no: string }): string {
  switch (action.kind) {
    case "quick_replies": {
      const options = ui.kind === "quick_replies" ? ui.options : [];
      return options.find((o) => o.value === action.value)?.label ?? action.value;
    }
    case "multi_select": {
      const options = ui.kind === "multi_select" ? ui.options : [];
      return action.value.map((v) => options.find((o) => o.value === v)?.label ?? v).join(", ");
    }
    case "slots": {
      const slot = ui.kind === "slots" ? ui.slots.find((s) => s.id === action.value) : undefined;
      return slot ? `${slot.label} · ${slot.location}` : action.value;
    }
    case "confirm":
      return action.value ? labels.yes : labels.no;
    case "phone": {
      const country = ui.kind === "phone" ? ui.countries.find((c) => c.code === action.value.country) : undefined;
      return country ? `${country.dial} ${action.value.number}` : action.value.number;
    }
  }
}
