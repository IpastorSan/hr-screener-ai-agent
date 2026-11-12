import type { ChatLocale } from "@/lib/types";

const es = {
  title: "Entrevista por voz",
  aiBadge: "Asistente IA",
  aiBadgeTitle: "Ana es una asistente de IA (inteligencia artificial)",
  intro: "Habla con Ana, una asistente de IA. Son unas preguntas rápidas y respondes en voz alta.",
  privacy: "Tus respuestas se tratan según el aviso de privacidad. Ana te pedirá que lo aceptes de palabra.",
  privacyLink: "Aviso de privacidad",
  start: "Empezar llamada",
  end: "Colgar",
  switchToChat: "Prefiero escribir",
  startAgain: "Empezar de nuevo",
  restartConfirm: "¿Empezar una entrevista nueva? Tendrás que responder desde el principio.",
  restartYes: "Sí, empezar de nuevo",
  restartCancel: "Cancelar",
  status: {
    idle: "Lista para hablar",
    preparing: "Preparando la llamada",
    connecting: "Conectando",
    speaking: "Ana está hablando",
    listening: "Te escucho",
    ended: "Llamada terminada",
  },
  micDenied: "Necesito acceso al micrófono para la llamada. Puedes permitirlo en el navegador o seguir por escrito.",
  failed: "No hemos podido conectar la llamada. Inténtalo de nuevo o sigue por escrito.",
  busy: "Ahora mismo no podemos atenderte. Vuelve a intentarlo más tarde.",
  closed: "Esta solicitud ya está cerrada.",
  transcript: "Transcripción",
  transcriptEmpty: "Aquí verás lo que vais diciendo.",
  you: "Tú",
  notConfiguredTitle: "La entrevista por voz no está disponible",
  notConfigured: "Todavía no hemos activado las llamadas. Puedes hacer la misma entrevista por escrito.",
  goToChat: "Ir al chat",
  details: "Tus datos",
};

export type VoiceCopy = typeof es;

const esMX: VoiceCopy = {
  ...es,
  intro: "Hable con Ana, una asistente de IA. Son unas preguntas rápidas y responde en voz alta.",
  privacy: "Sus respuestas se tratan según el aviso de privacidad. Ana le pedirá que lo acepte de palabra.",
  restartConfirm: "¿Empezar una entrevista nueva? Tendrá que responder desde el principio.",
  status: { ...es.status, listening: "Le escucho" },
  micDenied: "Necesito acceso al micrófono para la llamada. Puede permitirlo en el navegador o seguir por escrito.",
  failed: "No pudimos conectar la llamada. Inténtelo de nuevo o siga por escrito.",
  busy: "Por ahora no podemos atenderle. Vuelva a intentarlo más tarde.",
  transcriptEmpty: "Aquí verá lo que van diciendo.",
  you: "Usted",
  notConfigured: "Todavía no activamos las llamadas. Puede hacer la misma entrevista por escrito.",
  details: "Sus datos",
};

const en: VoiceCopy = {
  title: "Voice interview",
  aiBadge: "AI assistant",
  aiBadgeTitle: "Ana is an AI assistant",
  intro: "Talk to Ana, an AI assistant. It's a few quick questions, answered out loud.",
  privacy: "Your answers are handled under the privacy notice. Ana will ask you to accept it out loud.",
  privacyLink: "Privacy notice",
  start: "Start call",
  end: "Hang up",
  switchToChat: "I'd rather type",
  startAgain: "Start again",
  restartConfirm: "Start a new interview? You'll answer from the beginning.",
  restartYes: "Yes, start again",
  restartCancel: "Cancel",
  status: {
    idle: "Ready to talk",
    preparing: "Getting the call ready",
    connecting: "Connecting",
    speaking: "Ana is speaking",
    listening: "Listening",
    ended: "Call ended",
  },
  micDenied: "The call needs your microphone. Allow it in the browser, or carry on by typing.",
  failed: "We couldn't connect the call. Try again, or carry on by typing.",
  busy: "We can't take calls right now. Please try again later.",
  closed: "This application is already closed.",
  transcript: "Transcript",
  transcriptEmpty: "What you both say shows up here.",
  you: "You",
  notConfiguredTitle: "Voice interviews aren't available",
  notConfigured: "Calls aren't switched on yet. You can do the same interview by typing.",
  goToChat: "Go to the chat",
  details: "Your details",
};

export function voiceCopy(locale: ChatLocale): VoiceCopy {
  return locale === "en" ? en : locale === "es-MX" ? esMX : es;
}
