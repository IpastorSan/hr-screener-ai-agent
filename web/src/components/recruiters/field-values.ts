import type { Country, SpecField } from "@/lib/recruiter-types";

/** The option of a `multi_enum` whose words are kept in its companion attribute. */
const OTHER = "other";

/** A candidate list item or a profile: each field's value under its `attr`. */
export type FieldValues = object;

export function valueOf(field: SpecField, values: FieldValues, attr: string | null = field.attr): unknown {
  return attr === null ? undefined : (values as Record<string, unknown>)[attr];
}

/** The field's name, in the country's words when it has its own ("Licencia" in Mexico). */
export function fieldLabel(field: SpecField, country: Country | null): string {
  return (country && field.country_labels[country]) || field.label;
}

export function optionLabel(field: SpecField, value: string): string {
  return field.options.find((option) => option.value === value)?.label ?? value;
}

/** "Tiempo completo, Fines de semana"; an "other" by its words; an empty choice by the field's
 * word for none ("Ninguna"), else "—" like no answer. */
export function formatChoices(field: SpecField, values: FieldValues): string {
  const picked = valueOf(field, values) as string[] | null | undefined;
  if (!picked) return "—";
  const named = field.companion !== null;
  const names = picked.filter((value) => !(named && value === OTHER)).map((value) => optionLabel(field, value));
  if (named && picked.includes(OTHER)) {
    names.push((valueOf(field, values, field.companion) as string | null) ?? optionLabel(field, OTHER));
  }
  return names.length ? names.join(", ") : (field.none_label ?? "—");
}

