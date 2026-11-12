import { BotIcon, CogIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ConversationOut, TurnOut } from "@/lib/recruiter-types";
import { cn } from "@/lib/utils";
import { QualityFlagChip } from "./badges";
import { JsonPanel } from "./json-panel";
import { useStepLabel } from "./agent-spec";
import { CHANNEL_LABEL, formatCost, formatDateTime, formatTime, OUTCOME_LABEL } from "./labels";

/** A widget without options, by kind. "Reserva" alone would read as the fallback reply chip. */
const WIDGET_LABEL: Record<string, string> = {
  phone: "Campo de teléfono",
  confirm: "Botones de confirmación",
  booking: "Tarjeta de la entrevista",
};

function widgetLabels(ui: TurnOut["ui"]): string[] {
  if (!ui) return [];
  const options = (ui as { options?: unknown }).options;
  if (Array.isArray(options)) {
    return options.map((option) => String((option as { label?: unknown }).label ?? (option as { value?: unknown }).value ?? ""));
  }
  const slots = (ui as { slots?: unknown }).slots;
  if (Array.isArray(slots)) return slots.map((slot) => String((slot as { label?: unknown }).label ?? ""));
  const kind = String((ui as { kind?: unknown }).kind ?? "");
  return [WIDGET_LABEL[kind] ?? kind];
}

function TurnMeta({ turn }: { turn: TurnOut }) {
  const parts = [formatTime(turn.created_at)];
  if (turn.model) parts.push(turn.model);
  if (turn.latency_ms !== null) parts.push(`${turn.latency_ms} ms`);
  if (turn.cost_usd !== null) parts.push(formatCost(turn.cost_usd));
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 text-[0.68rem] text-muted-foreground tabular-nums">
      {parts.join(" · ")}
      {turn.fallback_used && (
        <span className="rounded bg-chart-4/10 px-1 font-medium text-chart-4" title="El modelo falló y se usó la respuesta de reserva">
          reserva
        </span>
      )}
    </div>
  );
}

/** "Fuente: Manual del repartidor › Seguridad" for a document, "FAQ: <question>" for the Q&A. */
function Sources({ sources }: { sources: NonNullable<TurnOut["sources"]> }) {
  return (
    <ul className="flex max-w-[85%] flex-col gap-0.5 text-[0.7rem] text-muted-foreground sm:max-w-[75%]" aria-label="Fuentes de la respuesta">
      {sources.map((source) => (
        <li key={source.source} title={source.source}>
          {source.source.startsWith("faq:") ? source.label : `Fuente: ${source.label}`}
        </li>
      ))}
    </ul>
  );
}

function Turn({ turn }: { turn: TurnOut }) {
  if (turn.role === "system") {
    return (
      <li className="flex flex-col items-center gap-1 py-1">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
          <CogIcon className="size-3" aria-hidden />
          {turn.text ?? "Evento del sistema"}
        </span>
        <TurnMeta turn={turn} />
        {turn.action && (
          <div className="w-full max-w-md">
            <JsonPanel label="Acción del motor" value={turn.action} />
          </div>
        )}
      </li>
    );
  }

  const candidate = turn.role === "candidate";
  const labels = widgetLabels(turn.ui);
  return (
    <li className={cn("flex flex-col gap-1", candidate ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap break-words sm:max-w-[75%]",
          candidate ? "rounded-br-md bg-primary text-primary-foreground" : "rounded-bl-md bg-muted",
        )}
      >
        {turn.text ?? <span className="italic opacity-70">(sin texto)</span>}
      </div>
      {labels.length > 0 && (
        <div className="flex max-w-[85%] flex-wrap gap-1">
          {labels.map((label, index) => (
            <span key={index} className="rounded-full border px-2 py-0.5 text-[0.7rem] text-muted-foreground">
              {label}
            </span>
          ))}
        </div>
      )}
      {!candidate && turn.sources && turn.sources.length > 0 && <Sources sources={turn.sources} />}
      <TurnMeta turn={turn} />
      {(turn.extraction || turn.action) && (
        <div className={cn("flex w-full max-w-[85%] flex-col gap-1 sm:max-w-[75%]", candidate && "items-stretch")}>
          {turn.extraction && <JsonPanel label="Extracción" value={turn.extraction} />}
          {turn.action && <JsonPanel label="Acción del motor" value={turn.action} />}
        </div>
      )}
    </li>
  );
}

export function Conversation({ conversation, index, total }: { conversation: ConversationOut; index: number; total: number }) {
  const stageLabel = useStepLabel();
  return (
    <section className="flex flex-col gap-4" aria-label={`Conversación ${index + 1}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b pb-3 text-xs text-muted-foreground">
        {total > 1 && <span className="font-semibold text-foreground">Conversación {index + 1}</span>}
        <span className="inline-flex items-center gap-1">
          <BotIcon className="size-3.5" aria-hidden />
          {CHANNEL_LABEL[conversation.channel]}
        </span>
        <span>Inicio {formatDateTime(conversation.started_at)}</span>
        <span>Etapa: {stageLabel(conversation.stage)}</span>
        {conversation.outcome && <Badge variant="secondary">{OUTCOME_LABEL[conversation.outcome]}</Badge>}
        {conversation.drop_off_stage && <span>Abandonó en: {stageLabel(conversation.drop_off_stage)}</span>}
        {conversation.disqualify_reason && <span>Motivo: {conversation.disqualify_reason}</span>}
        {conversation.quality_flags.map((flag) => (
          <QualityFlagChip key={flag} flag={flag} />
        ))}
      </div>
      {conversation.faq_misses && conversation.faq_misses.length > 0 && (
        <div className="rounded-md border border-dashed px-3 py-2 text-xs">
          <p className="font-medium text-foreground">Preguntas sin respuesta</p>
          <ul className="mt-1 list-disc pl-4 text-muted-foreground">
            {conversation.faq_misses.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
        </div>
      )}
      {conversation.turns.length === 0 ? (
        <p className="text-sm text-muted-foreground">Sin mensajes.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {conversation.turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
        </ol>
      )}
    </section>
  );
}
