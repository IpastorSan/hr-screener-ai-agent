"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { getMe, logout, SIGNED_OUT_EVENT } from "@/lib/recruiter-api";
import type { Me } from "@/lib/recruiter-types";
import { loginUrl } from "./next-path";

type Session = { me: Me; signOut: () => Promise<void>; refresh: () => Promise<void> };

const SessionContext = createContext<Session | null>(null);

/** The signed-in recruiter. Only inside `SessionGate`. */
export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionGate");
  return session;
}

/**
 * Renders the dashboard only for a signed-in recruiter: asks `GET /api/me` once, and sends
 * the browser to the sign-in page when there is no session or any later call answers 401.
 * The API enforces access; this only keeps a signed-out visitor from seeing an empty shell.
 */
export function SessionGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getMe({ signal: controller.signal }).then(setMe, (error: unknown) => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) router.replace(loginUrl(window.location.pathname));
      else setFailed(true);
    });
    return () => controller.abort();
  }, [router]);

  useEffect(() => {
    const signedOut = () => router.replace(loginUrl(window.location.pathname));
    window.addEventListener(SIGNED_OUT_EVENT, signedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, signedOut);
  }, [router]);

  const signOut = useCallback(async () => {
    await logout().catch(() => undefined);
    router.replace("/login");
  }, [router]);

  const refresh = useCallback(async () => {
    setMe(await getMe());
  }, []);

  if (failed) {
    return (
      <div role="alert" className="mx-auto mt-24 max-w-sm px-4 text-center text-sm text-muted-foreground">
        No se pudo conectar con el panel. Recarga la página en unos segundos.
      </div>
    );
  }
  if (!me) {
    return (
      <div aria-busy role="status" aria-label="Comprobando la sesión" className="flex flex-col">
        <div className="h-14 border-b" />
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 px-4 py-8">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-4 w-80 max-w-full" />
          <Skeleton className="mt-4 h-64 w-full rounded-xl" />
        </div>
      </div>
    );
  }
  return <SessionContext.Provider value={{ me, signOut, refresh }}>{children}</SessionContext.Provider>;
}
