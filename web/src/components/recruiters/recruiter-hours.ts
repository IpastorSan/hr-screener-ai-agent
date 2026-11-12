// Working hours as a recruiter types them: "09:00-14:00, 16:00-19:00". Pure, so `node --test` runs it.

export type Block = [string, string];

const HOUR = /^([01]\d|2[0-3]):[0-5]\d$/;

/** "09:00–14:00, 16:00–19:00"; "—" without blocks. */
export function formatHours(blocks: Block[] | undefined): string {
  if (!blocks || blocks.length === 0) return "—";
  return blocks.map(([start, end]) => `${start}–${end}`).join(", ");
}

function pad(time: string): string {
  const [hours, minutes = "00"] = time.split(":");
  return `${hours.padStart(2, "0")}:${minutes.padStart(2, "0")}`;
}

/** Parse "9-14, 16:00-19:00" into blocks, or null when it is not valid. Empty is no blocks. */
export function parseHours(text: string): Block[] | null {
  const parts = text
    .split(/[,;]/)
    .map((part) => part.trim())
    .filter(Boolean);
  const blocks: Block[] = [];
  for (const part of parts) {
    const match = part.split(/\s*[-–]\s*/);
    if (match.length !== 2) return null;
    const [start, end] = match.map(pad);
    if (!HOUR.test(start) || !HOUR.test(end) || start >= end) return null;
    blocks.push([start, end]);
  }
  return blocks;
}
