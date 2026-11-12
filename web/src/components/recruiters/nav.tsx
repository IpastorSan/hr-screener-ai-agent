"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useSession } from "./session";

const NAV = [
  { href: "/recruiters", label: "Candidatos" },
  { href: "/recruiters/interviews", label: "Mis entrevistas" },
  { href: "/recruiters/analytics", label: "Analítica" },
] as const;
const ADMIN_NAV = [{ href: "/recruiters/team", label: "Reclutadores" }] as const;
const SECTIONS = ["/recruiters/interviews", "/recruiters/analytics", "/recruiters/team"];

function NavLinks({ current }: { current: string | null }) {
  const { me } = useSession();
  const links = me.is_admin ? [...NAV, ...ADMIN_NAV] : NAV;
  return (
    <nav aria-label="Panel de selección" className="flex items-center gap-1">
      {links.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          aria-current={current === href ? "page" : undefined}
          className={cn(
            "rounded-lg px-3 py-1.5 text-sm font-bold whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground",
            current === href && "bg-accent text-accent-foreground hover:text-accent-foreground",
          )}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}

function ActiveNav() {
  const pathname = usePathname();
  return <NavLinks current={SECTIONS.find((section) => pathname.startsWith(section)) ?? "/recruiters"} />;
}

/** Pass `current` to render without reading the URL (the Suspense fallback). */
export function RecruiterNav({ current }: { current?: string | null }) {
  return current === undefined ? <ActiveNav /> : <NavLinks current={current} />;
}
