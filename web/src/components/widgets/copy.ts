import type { ChatLocale } from "@/lib/types";

const es = {
  intl: "es-ES",
  quickReplies: "Respuestas rápidas",
  newTab: "(se abre en una pestaña nueva)",
  options: "Opciones",
  continue: "Continuar",
  max: (n: number) => `Máximo ${n}`,
  atLeastOne: "Elige al menos una",
  atLeast: (n: number) => `Elige al menos ${n}`,
  chosen: (n: number) => (n === 1 ? "1 elegida" : `${n} elegidas`),
  confirmTitle: "¿Está todo bien?",
  confirmYes: "Sí, es correcto",
  confirmNo: "Corregir algo",
  slots: "Horarios de entrevista",
  book: "Reservar esta hora",
  booked: "Entrevista reservada",
  bookedHint: "¿No puedes ir? Escríbelo aquí y la cambiamos.",
  videoCall: "Videollamada",
  meetLink: "Unirse con Google Meet",
  meetByEmail: "El enlace de Google Meet te llegará al correo.",
  phonePrefix: "Prefijo",
  phoneNumber: "Número de móvil",
  phoneSend: "Enviar número",
};

export type WidgetCopy = typeof es;

const esMX: WidgetCopy = {
  ...es,
  intl: "es-MX",
  atLeastOne: "Elija al menos una",
  atLeast: (n: number) => `Elija al menos ${n}`,
  phoneNumber: "Número de celular",
  booked: "Entrevista agendada",
  bookedHint: "¿No puede asistir? Escríbalo aquí y la cambiamos.",
  meetByEmail: "El enlace de Google Meet le llegará al correo.",
};

const en: WidgetCopy = {
  intl: "en-GB",
  quickReplies: "Quick replies",
  newTab: "(opens in a new tab)",
  options: "Options",
  continue: "Continue",
  max: (n: number) => `${n} at most`,
  atLeastOne: "Pick at least one",
  atLeast: (n: number) => `Pick at least ${n}`,
  chosen: (n: number) => `${n} selected`,
  confirmTitle: "Is everything right?",
  confirmYes: "Yes, that's right",
  confirmNo: "Change something",
  slots: "Interview times",
  book: "Book this time",
  booked: "Interview booked",
  bookedHint: "Can't make it? Reply here and we'll move it.",
  videoCall: "Video call",
  meetLink: "Join with Google Meet",
  meetByEmail: "The Google Meet link will come by email.",
  phonePrefix: "Country code",
  phoneNumber: "Mobile number",
  phoneSend: "Send number",
};

export function widgetCopy(locale: ChatLocale = "es"): WidgetCopy {
  return locale === "en" ? en : locale === "es-MX" ? esMX : es;
}
