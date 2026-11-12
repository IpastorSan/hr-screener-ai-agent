"use client";

import { LogOutIcon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useSession } from "./session";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

/** The signed-in recruiter and the way out. */
export function UserMenu() {
  const { me, signOut } = useSession();
  const [leaving, setLeaving] = useState(false);
  return (
    <div className="flex items-center gap-2 border-l pl-2 sm:pl-3">
      <span
        aria-hidden
        className="grid size-7 shrink-0 place-content-center rounded-full bg-accent text-xs font-bold text-accent-foreground"
      >
        {initials(me.name)}
      </span>
      <span className="hidden max-w-40 flex-col leading-tight lg:flex" data-testid="current-recruiter">
        <span className="truncate text-sm font-medium">{me.name}</span>
        <span className="truncate text-xs text-muted-foreground">{me.is_admin ? "Administración" : me.email}</span>
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Cerrar sesión (${me.name})`}
        title="Cerrar sesión"
        disabled={leaving}
        onClick={() => {
          setLeaving(true);
          void signOut();
        }}
      >
        <LogOutIcon />
      </Button>
    </div>
  );
}
