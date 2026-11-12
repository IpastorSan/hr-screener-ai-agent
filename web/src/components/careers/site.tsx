import Link from "next/link";
import { GlobeIcon } from "lucide-react";
import { PageShell } from "@/components/brand";
import { cn } from "@/lib/utils";
import { HtmlLang } from "./html-lang";
import { POSTING_PATHS, PRIVACY_PATHS, type PageLang } from "./postings";

const COPY = {
  es: {
    switchTo: "English",
    switchLang: "en",
    jobs: "Empleo",
    spain: "Repartidor/a en España",
    mexico: "Repartidor/a en México",
    privacy: "Aviso de privacidad",
    fictional: "Grupo Sazón, sus sociedades, direcciones y contactos son ficticios: este sitio es una demostración.",
  },
  en: {
    switchTo: "Español",
    switchLang: "es",
    jobs: "Jobs",
    spain: "Delivery rider in Spain",
    mexico: "Delivery driver in Mexico",
    privacy: "Privacy notice",
    fictional: "Grupo Sazón, its companies, addresses and contacts are fictional: this site is a demo.",
  },
} as const;

/** Public careers pages: brand header with a language switch, and a footer with the privacy notice. */
export function CareersShell({
  lang,
  altHref,
  wide = false,
  children,
}: {
  lang: PageLang;
  altHref: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  const copy = COPY[lang];
  return (
    <>
      <HtmlLang lang={lang} />
      <PageShell
        wide={wide}
        aside={
          <Link
            href={altHref}
            hrefLang={copy.switchLang}
            lang={copy.switchLang}
            className="inline-flex h-[34px] items-center gap-1.5 rounded-full border border-input px-3 text-sm font-bold transition-colors outline-none hover:border-primary focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <GlobeIcon aria-hidden className="size-4 text-muted-foreground" />
            {copy.switchTo}
          </Link>
        }
      >
        {children}
      </PageShell>
      <SiteFooter lang={lang} wide={wide} />
    </>
  );
}

export function SiteFooter({ lang, wide = false }: { lang: PageLang; wide?: boolean }) {
  const copy = COPY[lang];
  return (
    <footer className="border-t bg-secondary lg:group-data-chat-open/posting:-mr-[420px] lg:group-data-chat-open/posting:pr-[420px]">
      <div
        className={cn(
          "mx-auto flex w-full flex-wrap gap-x-8 gap-y-3.5 py-6 text-sm text-muted-foreground",
          wide ? "max-w-6xl px-5" : "max-w-5xl px-4",
        )}
      >
        <nav aria-label={copy.jobs} className="flex flex-col gap-1.5 text-foreground">
          <Link href={POSTING_PATHS[lang].ES} className="underline-offset-4 hover:underline">
            {copy.spain}
          </Link>
          <Link href={POSTING_PATHS[lang].MX} className="underline-offset-4 hover:underline">
            {copy.mexico}
          </Link>
          <Link href={PRIVACY_PATHS[lang]} className="underline-offset-4 hover:underline">
            {copy.privacy}
          </Link>
        </nav>
        <p className="max-w-[44ch]">{copy.fictional}</p>
      </div>
    </footer>
  );
}
