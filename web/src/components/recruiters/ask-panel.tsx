"use client";

import { ArrowUpIcon, RotateCcwIcon, SparklesIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { InsightList } from "@/components/insights";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { recruiterApi } from "@/lib/recruiter-api";
import type { AskAnswer, AskTurn, InsightAction } from "@/lib/types";

const MAX_QUESTION = 500;
const HISTORY_EXCHANGES = 3;

export const SUGGESTED_QUESTIONS = [
  "Aptos en CDMX que pueden empezar esta semana",
  "¿Dónde abandonan más los candidatos?",
  "Candidatos con experiencia en Glovo en Barcelona",
  "¿Cuántos aptos esta semana por país?",
] as const;

type Exchange = { id: number; question: string; answer?: AskAnswer; error?: string };

export function askAssistant(question: string, history: AskTurn[], signal?: AbortSignal): Promise<AskAnswer> {
  return recruiterApi<AskAnswer>("/api/assistant", { method: "POST", json: { question, history }, signal });
}

function historyOf(exchanges: Exchange[]): AskTurn[] {
  return exchanges
    .filter((exchange) => exchange.answer)
    .slice(-HISTORY_EXCHANGES)
    .flatMap((exchange) => [
      { role: "user" as const, text: exchange.question },
      { role: "assistant" as const, text: exchange.answer!.text },
    ]);
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) return "La pregunta no es válida. Prueba a escribirla de otra forma.";
  return "No he podido responder ahora. Inténtalo de nuevo.";
}

/** "Preguntar a Ana": the recruiter assistant in a side sheet, opened from the header. */
export function AskPanel() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const nextId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [exchanges, pending]);

  useEffect(() => () => controller.current?.abort(), []);

  async function ask(text: string) {
    const question = text.trim().slice(0, MAX_QUESTION);
    if (!question || pending) return;
    const id = nextId.current++;
    const history = historyOf(exchanges);
    setExchanges((current) => [...current, { id, question }]);
    setDraft("");
    setPending(true);
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    try {
      const answer = await askAssistant(question, history, abort.signal);
      setExchanges((current) => current.map((e) => (e.id === id ? { ...e, answer } : e)));
    } catch (error) {
      if (abort.signal.aborted) return;
      setExchanges((current) => current.map((e) => (e.id === id ? { ...e, error: errorMessage(error) } : e)));
    } finally {
      if (controller.current === abort) {
        controller.current = null;
        setPending(false);
      }
    }
  }

  function retry(exchange: Exchange) {
    setExchanges((current) => current.filter((e) => e.id !== exchange.id));
    void ask(exchange.question);
  }

  function onAction(action: InsightAction) {
    if (action.kind !== "open_candidate") return;
    setOpen(false);
    router.push(`/recruiters/${encodeURIComponent(action.candidateId)}`);
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={<Button variant="outline" size="sm" aria-label="Preguntar a Ana" className="max-sm:size-7 max-sm:px-0" />}
      >
        <SparklesIcon className="text-chart-1" />
        <span className="hidden sm:inline">Preguntar a Ana</span>
      </SheetTrigger>
      <SheetContent className="w-full gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
        <SheetHeader className="border-b pr-12">
          <SheetTitle className="flex items-center gap-2">
            <SparklesIcon className="size-4 text-chart-1" />
            Preguntar a Ana
          </SheetTitle>
          <SheetDescription>Consulta candidatos y métricas del proceso. Solo lectura: Ana no cambia nada.</SheetDescription>
        </SheetHeader>

        <div data-slot="ask-thread" className="min-h-0 flex-1 overflow-y-auto px-4 py-4" aria-live="polite">
          {exchanges.length === 0 ? (
            <Suggestions onPick={ask} />
          ) : (
            <ol className="flex flex-col gap-6">
              {exchanges.map((exchange) => (
                <li key={exchange.id} className="flex flex-col gap-3">
                  <p className="max-w-[85%] self-end rounded-2xl rounded-br-md bg-primary px-3 py-2 whitespace-pre-wrap text-primary-foreground">
                    {exchange.question}
                  </p>
                  {exchange.answer ? (
                    <Answer answer={exchange.answer} onAction={onAction} />
                  ) : exchange.error ? (
                    <div className="flex items-center justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-destructive">
                      <span>{exchange.error}</span>
                      <Button variant="ghost" size="sm" onClick={() => retry(exchange)} disabled={pending}>
                        <RotateCcwIcon data-icon="inline-start" />
                        Reintentar
                      </Button>
                    </div>
                  ) : (
                    <Thinking />
                  )}
                </li>
              ))}
            </ol>
          )}
          <div ref={bottom} />
        </div>

        <form
          className="border-t p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void ask(draft);
          }}
        >
          <div className="flex items-end gap-2">
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void ask(draft);
                }
              }}
              maxLength={MAX_QUESTION}
              rows={1}
              placeholder="Pregunta sobre candidatos o el embudo…"
              aria-label="Tu pregunta"
              className="max-h-32 min-h-10 resize-none"
            />
            <Button type="submit" size="icon-lg" disabled={pending || !draft.trim()} aria-label="Enviar pregunta">
              <ArrowUpIcon />
            </Button>
          </div>
          {exchanges.length > 0 && (
            <div className="-mx-1 mt-2 flex gap-1.5 overflow-x-auto px-1 pb-0.5">
              {SUGGESTED_QUESTIONS.map((question) => (
                <button
                  key={question}
                  type="button"
                  disabled={pending}
                  onClick={() => void ask(question)}
                  className="shrink-0 rounded-full border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  {question}
                </button>
              ))}
            </div>
          )}
        </form>
      </SheetContent>
    </Sheet>
  );
}

function Suggestions({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-muted-foreground">Prueba con una de estas preguntas:</p>
      <ul className="grid gap-2 sm:grid-cols-2">
        {SUGGESTED_QUESTIONS.map((question) => (
          <li key={question}>
            <button
              type="button"
              onClick={() => onPick(question)}
              className="h-full w-full rounded-xl border bg-card px-3 py-2.5 text-left text-sm transition-colors outline-none hover:border-chart-1/60 hover:bg-accent/40 focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Answer({ answer, onAction }: { answer: AskAnswer; onAction: (action: InsightAction) => void }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="whitespace-pre-wrap text-foreground">{answer.text}</p>
      {answer.insights.length > 0 && <InsightList insights={answer.insights} onAction={onAction} />}
    </div>
  );
}

function Thinking() {
  return (
    <div className="flex flex-col gap-2" role="status">
      <span className="flex items-center gap-2 text-muted-foreground">
        <SparklesIcon className="size-3.5 animate-pulse text-chart-1" />
        Ana está consultando los datos…
      </span>
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}
