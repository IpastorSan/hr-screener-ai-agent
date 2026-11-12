"use client";

import { useId, useMemo, useState } from "react";
import { SendHorizontalIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CountryCode, WidgetProps } from "@/lib/types";
import { widgetCopy } from "./copy";
import { formatNational, nationalDigits, PLACEHOLDER } from "./phone-format";

const MIN_DIGITS = 8;

export function PhoneInput({ ui, onAction, disabled, locale }: WidgetProps<"phone">) {
  const copy = widgetCopy(locale);
  const id = useId();
  const [country, setCountry] = useState<CountryCode>(ui.selected);
  const [number, setNumber] = useState("");
  const [sent, setSent] = useState(false);
  const locked = disabled || sent;
  const valid = nationalDigits(number) >= MIN_DIGITS;
  const items = useMemo(() => ui.countries.map((c) => ({ value: c.code, label: c.label })), [ui.countries]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (locked || !valid) return;
    const formatted = formatNational(country, number);
    setNumber(formatted);
    setSent(true);
    onAction({ kind: "phone", value: { country, number: formatted } });
  };

  return (
    <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-2.5 rounded-[14px] border bg-card p-3">
      <label htmlFor={`${id}-number`} className="text-xs font-bold tracking-wider text-muted-foreground uppercase">
        {copy.phoneNumber}
      </label>
      <div className="flex gap-2">
        <Select
          items={items}
          value={country}
          onValueChange={(next) => next && setCountry(next as CountryCode)}
          disabled={locked}
        >
          <SelectTrigger aria-label={copy.phonePrefix} className="h-10 shrink-0 rounded-md bg-card font-bold tabular-nums">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value} className="tabular-nums">
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          id={`${id}-number`}
          type="tel"
          inputMode="tel"
          autoComplete="tel-national"
          placeholder={PLACEHOLDER[country]}
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          disabled={locked}
          maxLength={20}
          className="h-10 min-w-0 flex-1 rounded-md bg-card text-[1.0625rem] tabular-nums md:text-[1.0625rem] disabled:opacity-100"
        />
      </div>
      <Button type="submit" disabled={locked || !valid} className="h-[34px] self-start px-3.5 text-sm">
        <SendHorizontalIcon data-icon="inline-start" />
        {copy.phoneSend}
      </Button>
    </form>
  );
}
