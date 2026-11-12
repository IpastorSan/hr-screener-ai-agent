/**
 * The options picked after a toggle. An exclusive option (schedule "flexible") stands alone:
 * picking it clears the others, and picking another clears it.
 */
export function nextSelection(previous: readonly string[], next: readonly string[], exclusive: readonly string[] = []): string[] {
  const added = next.filter((value) => !previous.includes(value));
  if (added.some((value) => exclusive.includes(value))) {
    return added.filter((value) => exclusive.includes(value)).slice(-1);
  }
  if (added.length) return next.filter((value) => !exclusive.includes(value));
  return [...next];
}
