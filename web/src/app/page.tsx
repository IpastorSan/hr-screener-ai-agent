import Link from "next/link";
import { ArrowRightIcon, BikeIcon, ClipboardListIcon, MapPinIcon } from "lucide-react";
import { PageShell } from "@/components/brand";
import { POSTING_PATHS } from "@/components/careers/postings";
import { SiteFooter } from "@/components/careers/site";

const postings = [
  { href: POSTING_PATHS.es.ES, place: "España", detail: "Madrid, Barcelona, Valencia y más" },
  { href: POSTING_PATHS.es.MX, place: "México", detail: "CDMX, Guadalajara, Monterrey y más" },
] as const;

const entries = [
  {
    href: "/recruiters",
    icon: ClipboardListIcon,
    title: "Panel de selección",
    body: "Candidatos resumidos y puntuados, entrevistas y embudo por ciudad.",
  },
] as const;

export default function Home() {
  return (
    <>
      <PageShell>
        <div className="flex flex-col gap-10">
          <div className="flex max-w-xl flex-col gap-3">
            <p className="text-sm font-semibold text-muted-foreground">Repartidores · España y México</p>
            <h1 className="font-heading text-4xl font-normal tracking-tight text-balance sm:text-5xl">
              Buscamos gente que llegue{" "}
              <span className="underline decoration-primary decoration-[0.18em] underline-offset-[0.12em]">a tiempo</span>.
            </h1>
          </div>
          <section aria-labelledby="postings" className="flex flex-col gap-3 rounded-xl border bg-card p-5">
            <div className="flex items-center gap-3">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground">
                <BikeIcon className="size-5" />
              </span>
              <div>
                <h2 id="postings" className="font-heading text-xl font-normal">
                  Repartidor/a
                </h2>
                <p className="text-sm text-muted-foreground">
                  Mira la oferta de tu país, responde unas preguntas por chat y reserva tu entrevista.
                </p>
              </div>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2">
              {postings.map(({ href, place, detail }) => (
                <li key={href}>
                  <Link
                    href={href}
                    className="group flex flex-col items-start gap-3 rounded-lg border bg-background px-4 py-3 min-[480px]:flex-row min-[480px]:items-center min-[480px]:justify-between transition-colors outline-none hover:border-primary focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <span className="flex flex-col">
                      <span className="flex items-center gap-1.5 font-semibold">
                        <MapPinIcon className="size-4 text-muted-foreground" />
                        {place}
                      </span>
                      <span className="text-xs text-muted-foreground">{detail}</span>
                    </span>
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground">
                      Ver la oferta
                      <ArrowRightIcon className="size-4 transition-transform group-hover:translate-x-0.5" />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
          <ul className="grid gap-3 sm:grid-cols-2">
            {entries.map(({ href, icon: Icon, title, body }) => (
              <li key={href}>
                <Link
                  href={href}
                  className="group flex h-full flex-col gap-3 rounded-xl border bg-card p-5 transition-colors outline-none hover:border-primary focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span className="flex size-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
                    <Icon className="size-5" />
                  </span>
                  <span className="flex items-center gap-2 font-heading text-xl font-normal">
                    {title}
                    <ArrowRightIcon className="size-4 transition-transform group-hover:translate-x-0.5" />
                  </span>
                  <span className="text-sm text-muted-foreground">{body}</span>
                </Link>
              </li>
            ))}
          </ul>
          {process.env.NODE_ENV !== "production" && (
            <Link href="/dev/widgets" className="self-start text-sm text-muted-foreground underline underline-offset-4">
              Widgets de desarrollo
            </Link>
          )}
        </div>
      </PageShell>
      <SiteFooter lang="es" />
    </>
  );
}
