import { BotIcon } from "lucide-react";
import { CareersShell } from "./site";
import { PRIVACY_PATHS, type PageLang } from "./postings";

export type PrivacySection = { id: string; title: string; body: React.ReactNode };

export type PrivacyContent = {
  lang: PageLang;
  title: string;
  updated: string;
  intro: React.ReactNode;
  summaryTitle: string;
  summary: React.ReactNode[];
  tocTitle: string;
  sections: PrivacySection[];
};

export const CONTACT_EMAIL = "privacidad@gruposazon.example";

export function PrivacyNotice({ content }: { content: PrivacyContent }) {
  const altHref = PRIVACY_PATHS[content.lang === "es" ? "en" : "es"];
  return (
    <CareersShell lang={content.lang} altHref={altHref}>
      <div className="grid gap-10 lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-12">
        <nav aria-label={content.tocTitle} className="hidden lg:block">
          <div className="sticky top-6 flex flex-col gap-2 text-sm">
            <p className="font-semibold">{content.tocTitle}</p>
            <ol className="flex flex-col gap-1.5 text-muted-foreground">
              {content.sections.map((section) => (
                <li key={section.id}>
                  <a href={`#${section.id}`} className="underline-offset-4 hover:text-foreground hover:underline">
                    {section.title}
                  </a>
                </li>
              ))}
            </ol>
          </div>
        </nav>
        <article className="flex max-w-[44rem] min-w-0 flex-col gap-8">
          <header className="flex flex-col gap-3">
            <h1 className="font-heading text-4xl font-normal tracking-tight text-balance">{content.title}</h1>
            <p className="text-sm text-muted-foreground">{content.updated}</p>
            <div className="text-pretty text-muted-foreground">{content.intro}</div>
          </header>
          <aside aria-labelledby="ai-summary" className="flex flex-col gap-3 rounded-xl border border-primary/30 bg-accent/60 p-5">
            <h2 id="ai-summary" className="flex items-center gap-2 font-heading text-lg font-medium">
              <BotIcon aria-hidden className="size-5" />
              {content.summaryTitle}
            </h2>
            <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm">
              {content.summary.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </aside>
          {content.sections.map((section, index) => (
            <section key={section.id} id={section.id} aria-labelledby={`${section.id}-title`} className="flex scroll-mt-6 flex-col gap-3">
              <h2 id={`${section.id}-title`} className="font-heading text-2xl font-normal">
                <span className="mr-2 text-muted-foreground tabular-nums">{index + 1}.</span>
                {section.title}
              </h2>
              <div className="flex flex-col gap-3 text-[0.95rem] leading-relaxed [&_strong]:font-semibold [&_ul]:flex [&_ul]:list-disc [&_ul]:flex-col [&_ul]:gap-1.5 [&_ul]:pl-5">{section.body}</div>
            </section>
          ))}
        </article>
      </div>
    </CareersShell>
  );
}

/** A two-column table that turns into stacked rows on narrow screens. */
export function NoticeTable({ head, rows }: { head: [string, string]; rows: [React.ReactNode, React.ReactNode][] }) {
  return (
    <div className="overflow-hidden rounded-lg border">
      <table className="w-full text-left text-sm">
        <thead className="hidden bg-muted sm:table-header-group">
          <tr>
            <th scope="col" className="w-1/3 px-4 py-2 font-semibold">
              {head[0]}
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              {head[1]}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map(([a, b], index) => (
            <tr key={index} className="flex flex-col sm:table-row">
              <th scope="row" className="px-4 pt-3 font-semibold align-top sm:py-3 sm:font-medium">
                {a}
              </th>
              <td className="px-4 pt-1 pb-3 align-top text-muted-foreground sm:py-3">{b}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Mail() {
  return (
    <a href={`mailto:${CONTACT_EMAIL}`} className="font-medium text-primary underline underline-offset-2 dark:text-foreground">
      {CONTACT_EMAIL}
    </a>
  );
}

export function External({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium text-primary underline underline-offset-2 dark:text-foreground">
      {children}
    </a>
  );
}
