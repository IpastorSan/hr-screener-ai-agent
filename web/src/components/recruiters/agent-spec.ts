"use client";

import { getAgentSpec } from "@/lib/recruiter-api";
import type { AgentSpec, FunnelStage, SpecField } from "@/lib/recruiter-types";
import { cached } from "./cached-list";
import { formatChoices, optionLabel, valueOf, type FieldValues } from "./field-values";
import { ENGINE_STAGE_LABEL, formatDate, formatExperience } from "./labels";

const spec = cached<AgentSpec | null>(() => getAgentSpec(), null);

/** The agent spec from `GET /api/agent-spec`, fetched once per page load; null until loaded. */
export const useAgentSpec = spec.use;

/** A stage's name: the spec's for a funnel step, the engine's for its own stages (the interview,
 * done); the stage's id while the spec loads. */
export function useStepLabel(): (step: FunnelStage) => string {
  const loaded = useAgentSpec();
  return (step) =>
    loaded?.funnel.find((item) => item.step === step)?.label ?? ENGINE_STAGE_LABEL[step] ?? step;
}

/** The value as text, by the field's type; "—" when there is none. */
export function formatValue(field: SpecField, values: FieldValues): string {
  const value = valueOf(field, values);
  if (value === null || value === undefined) return "—";
  switch (field.type) {
    case "multi_enum":
      return formatChoices(field, values);
    case "years":
      return formatExperience(value as number);
    case "date":
      return formatDate(value as string);
    case "credential":
    case "yes_no":
      // A credential's reading its gate flags is kept as said ("in_progress").
      return optionLabel(field, typeof value === "string" ? value : value ? "yes" : "no");
    case "enum":
      return optionLabel(field, String(value));
    default:
      return String(value);
  }
}
