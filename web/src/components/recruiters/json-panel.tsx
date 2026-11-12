import { ChevronRightIcon } from "lucide-react";

/** A collapsed, plain-text JSON view. Candidate text inside is escaped by React, never parsed as HTML. */
export function JsonPanel({ label, value }: { label: string; value: unknown }) {
  return (
    <details className="group/json rounded-lg border bg-muted/40 text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-1 px-2 py-1 font-medium text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon className="size-3.5 transition-transform group-open/json:rotate-90" aria-hidden />
        {label}
      </summary>
      <pre className="max-h-80 overflow-auto border-t px-3 py-2 font-mono text-[0.7rem] leading-relaxed whitespace-pre-wrap break-all">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}
