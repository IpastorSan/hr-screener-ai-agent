"use client";

import { CalendarCheckIcon, CalendarPlusIcon, CalendarX2Icon } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { disconnectCalendar, getAuthProviders, googleConnectUrl } from "@/lib/recruiter-api";
import type { AuthProviders } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { calendarNotice } from "./google-messages";
import { formatDateTime } from "./labels";
import { useSession } from "./session";

/**
 * The signed-in recruiter's Google Calendar, in the header: "Conectar Google Calendar" until it
 * is connected, then a status that opens the details and "Desconectar". Shows the outcome of a
 * connection (`?calendar=` on the way back from Google) as a toast. Hidden when Google is not
 * configured.
 */
export function CalendarConnection() {
  const { me, refresh } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [providers, setProviders] = useState<AuthProviders | null>(null);
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const notice = params.get("calendar");

  useEffect(() => {
    const controller = new AbortController();
    getAuthProviders({ signal: controller.signal }).then(setProviders, () => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const message = calendarNotice(notice);
    if (!message) return;
    if (message.ok) toast.success(message.text);
    else toast.error(message.text);
    void refresh().catch(() => undefined);
    router.replace(pathname);
  }, [notice, pathname, refresh, router]);

  if (!providers?.google) return null;
  const { status, google_email, connected_at } = me.calendar;

  if (status !== "connected") {
    const revoked = status === "revoked";
    return (
      <a
        href={googleConnectUrl(pathname)}
        data-testid="calendar-connect"
        title={revoked ? "Google ya no acepta el permiso: vuelve a conectar tu calendario" : "Conecta tu Google Calendar para recibir entrevistas"}
        className={cn(
          buttonVariants({ variant: "outline", size: "sm" }),
          "max-sm:size-7 max-sm:px-0",
          revoked && "border-warn/50 text-warn",
        )}
      >
        {revoked ? <CalendarX2Icon data-icon="inline-start" /> : <CalendarPlusIcon data-icon="inline-start" />}
        <span className="max-sm:sr-only">{revoked ? "Reconectar Google Calendar" : "Conectar Google Calendar"}</span>
      </a>
    );
  }

  async function disconnect() {
    setLeaving(true);
    try {
      await disconnectCalendar();
      await refresh();
      setOpen(false);
      toast.success("Google Calendar desconectado.");
    } catch {
      toast.error("No se pudo desconectar. Inténtalo de nuevo.");
    } finally {
      setLeaving(false);
    }
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        data-testid="calendar-connected"
        className="text-ok max-sm:size-7 max-sm:px-0"
      >
        <CalendarCheckIcon data-icon="inline-start" />
        <span className="max-sm:sr-only">Calendar conectado</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Google Calendar conectado</DialogTitle>
            <DialogDescription>
              Las entrevistas que te asignen se crean en tu calendario ({google_email ?? me.email}) con el candidato invitado, y tus huecos
              ocupados dejan de ofrecerse.
              {connected_at && <> Conectado el {formatDateTime(connected_at)}.</>}
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Si lo desconectas, retiramos el permiso en Google y dejas de recibir entrevistas nuevas hasta que lo vuelvas a conectar. Las
            ya reservadas siguen en tu calendario.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cerrar
            </Button>
            <Button variant="destructive" onClick={() => void disconnect()} disabled={leaving}>
              {leaving ? "Desconectando…" : "Desconectar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
