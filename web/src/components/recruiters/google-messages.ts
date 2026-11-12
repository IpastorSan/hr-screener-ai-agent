// What the API's Google redirects mean (`/login?error=…`, `?calendar=…`), kept free of React
// so `node --test` runs them. Codes: backend/src/sazon/api/google_auth.py.

const RETRY = "No se pudo entrar con Google. Inténtalo de nuevo.";

const SIGN_IN: Record<string, string> = {
  domain: "Esa cuenta de Google no es de la empresa. Entra con tu cuenta de trabajo.",
  unverified: "Tu cuenta de Google no tiene el email verificado.",
  unknown: "No hay ningún reclutador activo con ese email. Pide a un administrador que te dé de alta.",
  denied: "Has cancelado el acceso con Google.",
  state: "El acceso con Google caducó. Inténtalo de nuevo.",
};

const CALENDAR: Record<string, { ok: boolean; text: string }> = {
  connected: { ok: true, text: "Google Calendar conectado: tus entrevistas se crearán en tu calendario." },
  scopes: { ok: false, text: "Google Calendar no se conectó: acepta los permisos de calendario." },
  mismatch: { ok: false, text: "Conecta el calendario de la misma cuenta con la que entras al panel." },
  denied: { ok: false, text: "Has cancelado la conexión con Google Calendar." },
  domain: { ok: false, text: "Esa cuenta de Google no es de la empresa." },
};

/** The sign-in page's message for `?error=`, or null without one. */
export function signInError(code: string | null): string | null {
  if (!code) return null;
  return SIGN_IN[code] ?? RETRY;
}

/** The toast for `?calendar=` after connecting, or null. */
export function calendarNotice(code: string | null): { ok: boolean; text: string } | null {
  if (!code) return null;
  return CALENDAR[code] ?? { ok: false, text: "No se pudo conectar Google Calendar. Inténtalo de nuevo." };
}
