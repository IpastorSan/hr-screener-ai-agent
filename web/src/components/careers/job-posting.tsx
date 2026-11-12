import Image from "next/image";
import { ArrowRightIcon, CheckIcon, MapPinIcon } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ApplyPanel } from "./apply-panel";
import { OpenChatLink, PostingChat } from "./posting-chat";
import { CareersShell } from "./site";
import type { Posting } from "./postings";

function Title({ title, accent }: { title: string; accent?: string }) {
  if (!accent || !title.endsWith(accent)) return title;
  return (
    <>
      {title.slice(0, -accent.length)}
      <em className="font-normal text-primary">{accent}</em>
    </>
  );
}

export function JobPosting({ posting }: { posting: Posting }) {
  const cities = [...new Set(posting.locations.map((loc) => loc.city))];
  const pay = posting.facts[0];

  return (
    <PostingChat posting={posting}>
    <CareersShell lang={posting.lang} altHref={posting.altPath} wide>
      <div className="mx-auto w-full max-w-6xl">
        <section className="grid @3xl:grid-cols-[1.1fr_1fr]">
          <div className="flex flex-col gap-4 px-5 pt-9 pb-7 @3xl:justify-center @3xl:px-10 @3xl:py-12">
            <p className="text-[0.8125rem] font-bold tracking-[0.06em] text-primary uppercase">{posting.eyebrow}</p>
            <h1 className="font-heading text-[clamp(2.6rem,6vw,4.5rem)] leading-[0.98] font-light tracking-[-0.02em] text-balance">
              <Title title={posting.title} accent={posting.titleAccent} />
            </h1>
            <p className="max-w-[36ch] text-lg text-pretty text-foreground/85">{posting.lead}</p>
          </div>
          <figure className="relative m-0 aspect-[4/3] @3xl:aspect-auto @3xl:min-h-80">
            <Image
              src="/img/rider.webp"
              alt={posting.photos.hero}
              fill
              preload
              sizes="(min-width: 768px) 50vw, 100vw"
              className="object-cover"
            />
          </figure>
        </section>

        <dl className="grid grid-cols-2 border-y @3xl:grid-cols-4">
          {posting.facts.map((fact, index) => (
            <div
              key={fact.label}
              className={cn(
                "flex flex-col px-5 py-4.5",
                index % 2 === 0 && "border-r",
                index < 2 && "border-b @3xl:border-b-0",
                index === 1 && "@3xl:border-r",
              )}
            >
              <dt className="text-xs font-bold tracking-[0.08em] text-muted-foreground uppercase">{fact.label}</dt>
              <dd className="mt-1 font-heading text-[1.3rem] leading-tight font-normal @3xl:text-[1.75rem]">{fact.value}</dd>
              <dd className="mt-0.5 text-[0.8125rem] text-muted-foreground">{fact.note}</dd>
            </div>
          ))}
        </dl>

        {/* With the chat open beside it, the apply card has done its job and the posting takes the room. */}
        <div className="grid gap-8 px-5 pt-8 pb-10 @3xl:grid-cols-[minmax(0,1fr)_340px] @3xl:p-10 lg:group-data-chat-open/posting:grid-cols-1">
          <div className="flex flex-col gap-8 @3xl:gap-10">
            <div className="grid gap-7 @3xl:grid-cols-2">
              {posting.sections.map((section) => (
                <section key={section.title}>
                  <h2 className="mb-3.5 font-heading text-[1.6rem] leading-tight font-normal">{section.title}</h2>
                  <ul className="border-b">
                    {section.items.map((item) => (
                      <li key={item} className="flex gap-2.5 border-t py-2 text-[0.9375rem]">
                        <CheckIcon aria-hidden className="mt-1 size-4 shrink-0 text-primary" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>

            <figure className="m-0 grid grid-cols-2 gap-2.5">
              <Image
                src="/img/kitchen.webp"
                alt={posting.photos.kitchen}
                width={960}
                height={720}
                sizes="(min-width: 768px) 25vw, 50vw"
                className="aspect-[4/3] w-full rounded-lg object-cover"
              />
              <Image
                src="/img/dish.webp"
                alt={posting.photos.dish}
                width={720}
                height={960}
                sizes="(min-width: 768px) 25vw, 50vw"
                className="aspect-[4/3] w-full rounded-lg object-cover"
              />
              <figcaption className="col-span-2 font-heading text-[1.05rem] text-pretty text-foreground/85 italic">
                {posting.photos.caption}
              </figcaption>
            </figure>

            <section>
              <h2 className="mb-3.5 font-heading text-[1.6rem] leading-tight font-normal">{posting.stepsTitle}</h2>
              <ol className="grid gap-3.5 @3xl:grid-cols-3">
                {posting.steps.map((step, index) => (
                  <li key={step.title} className="relative rounded-lg border bg-card py-4.5 pr-4.5 pl-14">
                    <span
                      aria-hidden
                      className="absolute top-4 left-4.5 grid size-[26px] place-items-center rounded-full bg-accent font-heading font-semibold text-primary"
                    >
                      {index + 1}
                    </span>
                    <h3 className="mb-1 font-bold">{step.title}</h3>
                    <p className="text-[0.9rem] text-muted-foreground">{step.body}</p>
                  </li>
                ))}
              </ol>
            </section>

            <section>
              <h2 className="mb-3.5 font-heading text-[1.6rem] leading-tight font-normal">{posting.citiesTitle}</h2>
              <ul className="flex flex-wrap gap-2">
                {cities.map((city) => (
                  <li
                    key={city}
                    className="inline-flex h-8 items-center gap-1.5 rounded-full border bg-card px-3 text-[0.9rem]"
                  >
                    <MapPinIcon aria-hidden className="size-3.5 text-muted-foreground" />
                    {city}
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <aside className="@3xl:sticky @3xl:top-4 @3xl:self-start lg:group-data-chat-open/posting:hidden">
            <ApplyPanel posting={posting} />
          </aside>
        </div>
      </div>

      {/* Phones: the apply card sits at the end of the page, so a bar keeps the way there in reach. */}
      <div className="sticky bottom-0 z-10 mt-auto flex items-center justify-between gap-2.5 border-t bg-card/95 px-4 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] backdrop-blur-sm md:hidden">
        <span className="min-w-0 text-[0.8125rem] leading-tight font-bold">
          {posting.title} · <span className="whitespace-nowrap">{pay.value}</span>
        </span>
        <OpenChatLink href="#apply" className={cn(buttonVariants(), "h-10 shrink-0 gap-2 px-5 text-[0.9375rem]")}>
          {posting.apply.button}
          <ArrowRightIcon aria-hidden className="size-4" />
        </OpenChatLink>
      </div>
    </CareersShell>
    </PostingChat>
  );
}
