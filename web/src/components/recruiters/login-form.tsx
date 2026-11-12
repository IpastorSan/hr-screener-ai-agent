"use client";

import { LockKeyholeIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { getAuthProviders, getMe, googleSignInUrl, login } from "@/lib/recruiter-api";
import type { AuthProviders } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { signInError } from "./google-messages";
import { safeNext } from "./next-path";


function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="size-4">
      <path fill="#4285F4" d="M22.6 12.2c0-.8-.1-1.5-.2-2.2H12v4.2h6c-.3 1.4-1 2.5-2.2 3.3v2.7h3.5c2.1-1.9 3.3-4.7 3.3-8z" />
      <path fill="#34A853" d="M12 23c3 0 5.5-1 7.3-2.7l-3.5-2.7c-1 .7-2.3 1.1-3.8 1.1-2.9 0-5.4-2-6.3-4.6H2.1v2.8C3.9 20.5 7.7 23 12 23z" />
      <path fill="#FBBC05" d="M5.7 14.1c-.2-.7-.4-1.4-.4-2.1s.1-1.4.4-2.1V7.1H2.1C1.4 8.6 1 10.2 1 12s.4 3.4 1.1 4.9l3.6-2.8z" />
      <path fill="#EA4335" d="M12 5.4c1.6 0 3.1.6 4.2 1.7l3.1-3.1C17.5 2.1 15 1 12 1 7.7 1 3.9 3.5 2.1 7.1l3.6 2.8C6.6 7.3 9.1 5.4 12 5.4z" />
    </svg>
  );
}

/** Sign-in for recruiters: the company Google account, or email and password. Accounts are
 * created by an admin; Google only signs in someone who already has one. */
export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(() => signInError(params.get("error")));
  const [pending, setPending] = useState(false);
  const [providers, setProviders] = useState<AuthProviders | null>(null);

  // Already signed in: straight to the dashboard.
  useEffect(() => {
    const controller = new AbortController();
    getMe({ signal: controller.signal }).then(
      () => router.replace(next),
      () => undefined,
    );
    getAuthProviders({ signal: controller.signal }).then(setProviders, () => undefined);
    return () => controller.abort();
  }, [router, next]);

  const google = providers?.google ?? false;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await login(email, password);
      router.replace(next);
    } catch (failure) {
      setPassword("");
      if (failure instanceof ApiError && failure.status === 429) setError("Demasiados intentos. Espera unos minutos e inténtalo de nuevo.");
      else if (failure instanceof ApiError && failure.status === 401) setError("Email o contraseña incorrectos.");
      else setError("No se pudo iniciar sesión. Inténtalo de nuevo.");
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 rounded-2xl bg-card p-6 ring-1 ring-foreground/10 sm:p-8">
      <div className="flex flex-col gap-2">
        <span aria-hidden className="grid size-10 place-content-center rounded-full bg-accent text-primary">
          <LockKeyholeIcon className="size-5" />
        </span>
        <h1 className="font-heading text-2xl font-normal tracking-tight">Panel de selección</h1>
        <p className="text-sm text-muted-foreground">Entra con tu cuenta de reclutador de Grupo Sazón.</p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Email
          <Input
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="nombre@grupo-sazon.com"
            className="h-10"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Contraseña
          <Input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={error ? true : undefined}
            className="h-10"
          />
        </label>
        <p role="alert" aria-live="polite" className="min-h-5 text-sm text-destructive">
          {error}
        </p>
        <Button type="submit" size="lg" className="h-10" disabled={pending || !email || !password}>
          {pending ? "Entrando…" : "Entrar"}
        </Button>
      </form>

      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" />o<span className="h-px flex-1 bg-border" />
      </div>

      <div className="flex flex-col gap-1.5">
        {google ? (
          <a
            href={googleSignInUrl(next)}
            className={cn(buttonVariants({ variant: "outline", size: "lg" }), "h-auto min-h-10 flex-wrap gap-x-1.5 py-2 whitespace-normal")}
            data-testid="google-sign-in"
          >
            <GoogleMark />
            Continuar con Google
          </a>
        ) : (
          <>
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="h-auto min-h-10 flex-wrap gap-x-1.5 py-2 whitespace-normal"
              disabled
              aria-describedby="google-off"
            >
              <GoogleMark />
              Continuar con Google
            </Button>
            <p id="google-off" className="text-center text-xs text-muted-foreground">
              {providers ? "El acceso con Google no está configurado en este entorno." : "Comprobando el acceso con Google…"}
            </p>
          </>
        )}
      </div>

      <p className="text-xs text-muted-foreground">¿No tienes cuenta o has olvidado la contraseña? Pide a un administrador del equipo que te la cree o la restablezca.</p>
    </div>
  );
}
