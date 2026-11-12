"use client";

import type { ChatLocale, Ui, UiAction } from "@/lib/types";
import { BookingCard } from "./booking-card";
import { ConfirmCard } from "./confirm-card";
import { MultiSelect } from "./multi-select";
import { PhoneInput } from "./phone-input";
import { QuickReplies } from "./quick-replies";
import { SlotPicker } from "./slot-picker";

export { BookingCard, ConfirmCard, MultiSelect, PhoneInput, QuickReplies, SlotPicker };

type Props = { ui: Ui; onAction: (action: UiAction) => void; disabled?: boolean; locale?: ChatLocale };

export function Widget({ ui, onAction, disabled, locale }: Props) {
  switch (ui.kind) {
    case "quick_replies":
      return <QuickReplies ui={ui} onAction={onAction} disabled={disabled} locale={locale} />;
    case "multi_select":
      return <MultiSelect ui={ui} onAction={onAction} disabled={disabled} locale={locale} />;
    case "slots":
      return <SlotPicker ui={ui} onAction={onAction} disabled={disabled} locale={locale} />;
    case "confirm":
      return <ConfirmCard ui={ui} onAction={onAction} disabled={disabled} locale={locale} />;
    case "booking":
      return <BookingCard ui={ui} locale={locale} />;
    case "phone":
      return <PhoneInput ui={ui} onAction={onAction} disabled={disabled} locale={locale} />;
  }
}
