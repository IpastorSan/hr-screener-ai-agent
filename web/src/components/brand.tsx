import Link from "next/link";
import { cn } from "@/lib/utils";

export function BrandMark({ className }: { className?: string }) {
  return (
    <Link href="/" className={cn("inline-flex items-center gap-2.5 font-heading text-xl font-medium tracking-tight", className)}>
      <span aria-hidden className="relative inline-block size-[22px] rounded-full bg-primary">
        <span className="absolute inset-1.5 rounded-full bg-background" />
      </span>
      Grupo Sazón
    </Link>
  );
}

/** `wide`: the main area has no container or padding of its own (the job postings lay out
 * full-width bands). */
export function PageShell({ children, aside, wide = false }: { children: React.ReactNode; aside?: React.ReactNode; wide?: boolean }) {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="border-b bg-card lg:group-data-chat-open/posting:-mr-[420px] lg:group-data-chat-open/posting:pr-[420px]">
        <div className={cn("mx-auto flex h-14 w-full items-center justify-between gap-4", wide ? "max-w-6xl px-5" : "max-w-5xl px-4")}>
          <BrandMark />
          {aside}
        </div>
      </header>
      <main className={cn("w-full flex-1", wide ? "flex flex-col" : "mx-auto max-w-5xl px-4 py-8 sm:py-12")}>{children}</main>
    </div>
  );
}
