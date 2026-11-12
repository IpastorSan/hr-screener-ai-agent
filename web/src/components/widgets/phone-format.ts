import type { CountryCode } from "@/lib/types";

const MX_TWO_DIGIT_AREAS = ["55", "33", "81"];

/** Groups a national number the way people write it ("612 345 678", "33 1234 5678"), or
 * returns the trimmed input unchanged when it is not a plain national number. */
export function formatNational(country: CountryCode, raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (/[^\d\s().-]/.test(raw.trim())) return raw.trim();
  if (country === "ES" && digits.length === 9) {
    return `${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6)}`;
  }
  if (country === "MX" && digits.length === 10) {
    if (MX_TWO_DIGIT_AREAS.includes(digits.slice(0, 2))) {
      return `${digits.slice(0, 2)} ${digits.slice(2, 6)} ${digits.slice(6)}`;
    }
    return `${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6)}`;
  }
  return raw.trim();
}

export function nationalDigits(raw: string) {
  return raw.replace(/\D/g, "").length;
}

export const PLACEHOLDER: Record<CountryCode, string> = { ES: "612 345 678", MX: "33 1234 5678" };
