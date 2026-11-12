"use client";

import { useId } from "react";
import Link from "next/link";
import { ArrowRightIcon, MicIcon, SparklesIcon } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { ANY_LOCATION as ANY, openOnPlainClick, usePostingChat } from "./posting-chat";
import { applyHref, type Posting, voiceHref } from "./postings";

/** Location picker and the Apply button, which opens the chat in the posting with its context. */
export function ApplyPanel({ posting }: { posting: Posting }) {
  const { apply, locations } = posting;
  const { location, setLocation, openChat } = usePostingChat();
  const labelId = useId();
  const items: Record<string, string> = { [ANY]: apply.anyLocation };
  for (const loc of locations) items[loc.id] = loc.name;
  const cities = [...new Set(locations.map((loc) => loc.city))];

  return (
    <section aria-labelledby="apply" className="flex flex-col gap-3.5 rounded-[14px] border bg-card p-5.5">
      <div className="flex flex-col gap-1">
        <h2 id="apply" className="scroll-mt-6 font-heading text-[1.6rem] leading-tight font-normal">
          {apply.title}
        </h2>
        <p className="text-[0.9rem] text-muted-foreground">{apply.body}</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <span id={labelId} className="text-sm font-bold">
          {apply.locationLabel}
        </span>
        <Select items={items} value={location} onValueChange={(next) => setLocation(next ? String(next) : ANY)}>
          <SelectTrigger
            aria-labelledby={labelId}
            data-testid="location-select"
            className={cn("h-10! w-full rounded-md bg-card", location === ANY && "text-muted-foreground")}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false} align="start" className="max-h-80">
            <SelectItem value={ANY}>{apply.anyLocation}</SelectItem>
            {cities.map((city) => (
              <SelectGroup key={city}>
                <SelectLabel>{city}</SelectLabel>
                {locations
                  .filter((loc) => loc.city === city)
                  .map((loc) => (
                    <SelectItem key={loc.id} value={loc.id}>
                      {loc.name}
                    </SelectItem>
                  ))}
              </SelectGroup>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Link
        href={applyHref(posting, location === ANY ? null : location)}
        onClick={(event) => openOnPlainClick(event, openChat)}
        className={cn(buttonVariants(), "h-12 w-full gap-2 text-[1.0625rem]")}
      >
        {apply.button}
        <ArrowRightIcon aria-hidden className="size-4" />
      </Link>
      <Link
        href={voiceHref(posting, location === ANY ? null : location)}
        data-testid="voice-link"
        className="-mt-1.5 inline-flex items-center justify-center gap-1.5 self-center text-[0.9rem] font-bold text-primary underline underline-offset-3"
      >
        <MicIcon aria-hidden className="size-4" />
        {apply.voiceLink}
      </Link>
      <p className="flex items-start gap-2 text-[0.8125rem] text-muted-foreground">
        <SparklesIcon aria-hidden className="mt-[3px] size-3.5 shrink-0 text-primary" />
        <span>
          {apply.aiNote}{" "}
          <Link href={posting.privacyPath} className="font-bold text-foreground underline underline-offset-2">
            {apply.privacyLink}
          </Link>
        </span>
      </p>
    </section>
  );
}
