import type { ChatLocale, ChatState } from "@/lib/types";

const es = {
  intl: "es-ES",
  aiBadge: "Asistente IA",
  aiBadgeTitle: "Ana es una asistente de IA (inteligencia artificial)",
  online: "En línea",
  typing: "Ana está escribiendo",
  placeholder: "Escribe un mensaje",
  placeholderClosed: "Conversación finalizada",
  send: "Enviar",
  notSent: "No se ha enviado.",
  retry: "Reintentar",
  startFailed: "No hemos podido abrir el chat.",
  startFailedHint: "Revisa tu conexión e inténtalo de nuevo.",
  loading: "Abriendo el chat",
  closedQualifiedTitle: "Solicitud completada",
  closedQualified: "Tu solicitud está completa. Gracias por tu tiempo.",
  closedDisqualifiedTitle: "Gracias por tu interés",
  closedDisqualified: "Esta vez no podemos seguir con tu solicitud.",
  closedDeclinedTitle: "No hemos guardado tus datos",
  closedDeclined: "Has preferido no continuar, así que no guardamos nada de esta conversación.",
  closedOtherTitle: "Conversación cerrada",
  closedOther: "Gracias por tu tiempo.",
  startAgain: "Empezar de nuevo",
  restartConfirm: "¿Empezar una conversación nueva? Tendrás que responder desde el principio.",
  restartYes: "Sí, empezar de nuevo",
  restartCancel: "Cancelar",
  notifyTitle: "Ana",
  notifyPrompt: "¿Te aviso si te escribo mientras estás en otra pestaña?",
  notifyAllow: "Avísame",
  notifyDismiss: "Ahora no",
  backToPosting: "Volver a la oferta",
};

export type ChatCopy = typeof es;

const esMX: ChatCopy = {
  ...es,
  intl: "es-MX",
  placeholder: "Escriba un mensaje",
  startFailedHint: "Revise su conexión e inténtelo de nuevo.",
  closedQualified: "Su solicitud está completa. Gracias por su tiempo.",
  closedDisqualifiedTitle: "Gracias por su interés",
  closedDisqualified: "Esta vez no podemos seguir con su solicitud.",
  closedDeclinedTitle: "No guardamos sus datos",
  closedDeclined: "Prefirió no continuar, así que no guardamos nada de esta conversación.",
  closedOther: "Gracias por su tiempo.",
  startAgain: "Empezar de nuevo",
  restartConfirm: "¿Empezar una conversación nueva? Tendrá que responder desde el principio.",
  notifyPrompt: "¿Le aviso si le escribo mientras está en otra pestaña?",
  notifyAllow: "Avíseme",
};

const en: ChatCopy = {
  intl: "en-GB",
  aiBadge: "AI assistant",
  aiBadgeTitle: "Ana is an AI assistant",
  online: "Online",
  typing: "Ana is typing",
  placeholder: "Type a message",
  placeholderClosed: "Conversation ended",
  send: "Send",
  notSent: "Not sent.",
  retry: "Retry",
  startFailed: "We couldn't open the chat.",
  startFailedHint: "Check your connection and try again.",
  loading: "Opening the chat",
  closedQualifiedTitle: "Application complete",
  closedQualified: "Your application is complete. Thanks for your time.",
  closedDisqualifiedTitle: "Thanks for your interest",
  closedDisqualified: "We can't take your application further this time.",
  closedDeclinedTitle: "Nothing was saved",
  closedDeclined: "You chose not to continue, so we haven't kept anything from this conversation.",
  closedOtherTitle: "Conversation closed",
  closedOther: "Thanks for your time.",
  startAgain: "Start again",
  restartConfirm: "Start a new conversation? You'll answer from the beginning.",
  restartYes: "Yes, start again",
  restartCancel: "Cancel",
  notifyTitle: "Ana",
  notifyPrompt: "Shall I alert you if I write while you're in another tab?",
  notifyAllow: "Notify me",
  notifyDismiss: "Not now",
  backToPosting: "Back to the job",
};

export function chatLocale(state: Pick<ChatState, "language" | "country"> | null, fallback: ChatLocale = "es"): ChatLocale {
  if (!state) return fallback;
  if (state.language === "en") return "en";
  return state.country === "MX" ? "es-MX" : "es";
}

export function chatCopy(locale: ChatLocale): ChatCopy {
  return locale === "en" ? en : locale === "es-MX" ? esMX : es;
}
