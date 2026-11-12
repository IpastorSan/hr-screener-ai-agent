import {
  BotIcon,
  CalendarCheckIcon,
  CalendarClockIcon,
  CircleHelpIcon,
  FrownIcon,
  GlobeIcon,
  MapPinIcon,
  MehIcon,
  MessageSquareTextIcon,
  MicIcon,
  SmileIcon,
  VideoIcon,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Channel, InterviewMode, Language, QualityFlag, ReviewStatus, ScreeningStatus, Sentiment, SpecField } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { valueOf, type FieldValues } from "./field-values";
import {
  CHANNEL_LABEL,
  INTERVIEW_LABEL,
  LANGUAGE_LABEL,
  MODE_LABEL,
  QUALITY_FLAG_LABEL,
  REVIEW_LABEL,
  SCREENING_LABEL,
  SENTIMENT_LABEL,
} from "./labels";

const SCREENING_CLASS: Record<ScreeningStatus, string> = {
  qualified: "bg-ok-soft text-ok",
  disqualified: "bg-destructive-soft text-destructive",
  in_progress: "bg-warn-soft text-warn",
  abandoned: "bg-muted text-muted-foreground",
  withdrawn: "bg-muted text-muted-foreground",
};

/** `interview`: a booked interview, shown instead of plain "Apto". */
export function ScreeningBadge({
  status,
  interview,
  className,
}: {
  status: ScreeningStatus;
  interview?: boolean;
  className?: string;
}) {
  if (interview && status === "qualified") {
    return (
      <Badge className={cn("bg-accent text-accent-foreground", className)}>
        <CalendarCheckIcon data-icon="inline-start" />
        {INTERVIEW_LABEL}
      </Badge>
    );
  }
  return <Badge className={cn(SCREENING_CLASS[status], className)}>{SCREENING_LABEL[status]}</Badge>;
}

const REVIEW_DOT: Record<ReviewStatus, string> = {
  new: "bg-amber",
  contacted: "bg-qa",
  hired: "bg-ok",
  rejected: "bg-muted-foreground/50",
};

export function ReviewLabel({ status }: { status: ReviewStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span aria-hidden className={cn("size-2 rounded-full", REVIEW_DOT[status])} />
      {REVIEW_LABEL[status]}
    </span>
  );
}

/** How the booked interview happens: in person or a Google Meet call. */
export function ModeBadge({ mode }: { mode: InterviewMode }) {
  const Icon = mode === "video" ? VideoIcon : MapPinIcon;
  return (
    <Badge variant="outline" className="border-input font-normal text-muted-foreground" data-testid="mode-badge">
      <Icon data-icon="inline-start" />
      {MODE_LABEL[mode]}
    </Badge>
  );
}

/** Confirmed by the candidate, but longer than a platform they named has been in the country. */
export function UnverifiedExperienceBadge() {
  return (
    <Badge
      variant="outline"
      className="border-warn/40 font-normal text-warn"
      title="Más años de los que la plataforma lleva en el país; el candidato lo confirmó"
    >
      <CircleHelpIcon data-icon="inline-start" />
      Experiencia por confirmar
    </Badge>
  );
}

export function LateStartBadge({ days }: { days: number }) {
  return (
    <Badge variant="outline" className="border-warn/40 font-normal text-warn" title={`Empieza dentro de ${days} días`}>
      <CalendarClockIcon data-icon="inline-start" />
      {days} d
    </Badge>
  );
}

/** What a field's companion attribute says of its value, by type: a far start date, more
 * years than a platform has existed. Nothing for other fields. */
export function CompanionBadge({ field, values }: { field: SpecField; values: FieldValues }) {
  const companion = valueOf(field, values, field.companion);
  if (field.type === "date" && typeof companion === "number") return <LateStartBadge days={companion} />;
  if (field.type === "years" && companion) return <UnverifiedExperienceBadge />;
  return null;
}

export const SENTIMENT_ICON: Record<Sentiment, LucideIcon> = {
  positive: SmileIcon,
  neutral: MehIcon,
  confused: MehIcon,
  frustrated: FrownIcon,
};

export const SENTIMENT_CLASS: Record<Sentiment, string> = {
  positive: "text-ok",
  neutral: "text-muted-foreground",
  confused: "text-chart-4",
  frustrated: "text-destructive",
};

export function SentimentIcon({ sentiment, withLabel = false }: { sentiment: Sentiment | null; withLabel?: boolean }) {
  if (!sentiment) return <span className="text-muted-foreground">—</span>;
  const Icon = SENTIMENT_ICON[sentiment];
  return (
    <span className={cn("inline-flex items-center gap-1.5", SENTIMENT_CLASS[sentiment])} title={SENTIMENT_LABEL[sentiment]}>
      <Icon className="size-4" aria-hidden />
      <span className={withLabel ? "text-sm" : "sr-only"}>{SENTIMENT_LABEL[sentiment]}</span>
    </span>
  );
}

export function LanguageChannel({ language, channel }: { language: Language | null; channel: Channel }) {
  const ChannelIcon = channel === "voice" ? MicIcon : MessageSquareTextIcon;
  return (
    <span className="inline-flex items-center gap-2 text-muted-foreground">
      <span className="inline-flex items-center gap-1" title={language ? LANGUAGE_LABEL[language] : "Idioma desconocido"}>
        <GlobeIcon className="size-3.5" aria-hidden />
        <span className="text-[0.7rem] font-bold tracking-[0.04em] uppercase">{language ?? "—"}</span>
      </span>
      <span title={CHANNEL_LABEL[channel]}>
        <ChannelIcon className="size-3.5" aria-hidden />
        <span className="sr-only">{CHANNEL_LABEL[channel]}</span>
      </span>
    </span>
  );
}

/** Internal chatbot QA marker: dashed and robot-tagged so it never reads as a candidate trait. */
export function QualityFlagChip({ flag }: { flag: QualityFlag }) {
  return (
    <span
      className="inline-flex h-5 items-center gap-1 rounded-md border border-dashed border-qa/50 bg-qa-soft px-1.5 text-[0.7rem] font-bold whitespace-nowrap text-qa"
      title="Control de calidad interno del bot, no es un dato del candidato"
    >
      <BotIcon className="size-3" aria-hidden />
      {QUALITY_FLAG_LABEL[flag]}
    </span>
  );
}
