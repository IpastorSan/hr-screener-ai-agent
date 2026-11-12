import Link from "next/link";
import { ChevronLeftIcon, RotateCcwIcon, SparklesIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ChatCopy } from "./copy";

const BACK = "flex size-8 items-center justify-center rounded-full outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50";

/**
 * `onBack`: the chat sits inside a job posting, and the button returns to it (a close button in the desktop panel).
 * `headingId`: Ana's name is an `<h2>` with this id (the posting has its own `<h1>`).
 * `onRestart`: a "start again" button at the end, absent while a reply streams in.
 */
export function ChatHeader({
  copy,
  onBack,
  headingId,
  onRestart,
  restartPending = false,
}: {
  copy: ChatCopy;
  onBack?: () => void;
  headingId?: string;
  onRestart?: () => void;
  /** The confirmation below the header is open. */
  restartPending?: boolean;
}) {
  const Heading = headingId ? "h2" : "h1";
  return (
    <header className="@container flex shrink-0 items-center gap-2.5 border-b bg-card px-3 pt-[max(0.875rem,env(safe-area-inset-top))] pb-3 text-card-foreground">
      {onBack ? (
        <button type="button" onClick={onBack} aria-label={copy.backToPosting} className={BACK}>
          <ChevronLeftIcon aria-hidden className="size-5 lg:hidden" />
          <XIcon aria-hidden className="hidden size-5 lg:block" />
        </button>
      ) : (
        <Link href="/" aria-label="Grupo Sazón" className={BACK}>
          <ChevronLeftIcon className="size-5" />
        </Link>
      )}
      <div className="relative shrink-0">
        <span
          aria-hidden
          className="flex size-10 items-center justify-center rounded-full bg-primary font-heading text-xl font-medium text-primary-foreground"
        >
          A
        </span>
        <span aria-hidden className="absolute -right-px -bottom-px size-[11px] rounded-full bg-ok ring-2 ring-card" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2">
          <Heading id={headingId} className="font-heading text-lg leading-tight font-medium">
            Ana
          </Heading>
          <span
            data-testid="ai-badge"
            title={copy.aiBadgeTitle}
            className="inline-flex h-5 shrink-0 items-center gap-1 rounded-full bg-accent px-2 text-[0.6875rem] leading-none font-bold tracking-wider whitespace-nowrap text-accent-foreground uppercase"
          >
            <SparklesIcon aria-hidden className="size-3" />
            {copy.aiBadge}
          </span>
        </div>
        <p className="truncate text-xs text-muted-foreground">
          Grupo Sazón · <span className="font-bold text-ok">{copy.online}</span>
        </p>
      </div>
      {onRestart && <RestartButton label={copy.startAgain} onClick={onRestart} pending={restartPending} />}
    </header>
  );
}

/** "Start again" at the end of a header (the chat's and the call page's). */
export function RestartButton({ label, onClick, pending }: { label: string; onClick: () => void; pending: boolean }) {
  return (
    <button
      type="button"
      data-testid="restart"
      onClick={onClick}
      aria-expanded={pending}
      title={label}
      aria-label={label}
      className="flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-muted aria-expanded:text-foreground"
    >
      <RotateCcwIcon aria-hidden className="size-4" />
      <span aria-hidden className="@max-md:hidden">{label}</span>
    </button>
  );
}

type RestartCopy = Pick<ChatCopy, "startAgain" | "restartConfirm" | "restartYes" | "restartCancel">;

/** The confirmation under the header before starting again, in place of a browser dialog. */
export function RestartConfirm({ copy, onConfirm, onCancel }: { copy: RestartCopy; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div
      role="group"
      aria-label={copy.startAgain}
      data-testid="restart-confirm"
      className="flex shrink-0 flex-wrap items-center justify-end gap-x-3 gap-y-2 border-b bg-accent/60 px-3 py-2.5"
    >
      <p className="min-w-0 flex-1 basis-56 text-sm text-pretty">{copy.restartConfirm}</p>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={onCancel}>
          {copy.restartCancel}
        </Button>
        <Button size="sm" onClick={onConfirm}>
          {copy.restartYes}
        </Button>
      </div>
    </div>
  );
}
