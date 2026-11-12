/** A Google Meet link we are willing to put in an `href`: https on meet.google.com only, so a
 * tampered booking can never turn the card into a `javascript:` or phishing link. */
export function safeMeetUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.hostname !== "meet.google.com" || url.username || url.password) return null;
  return url.toString();
}

/** "meet.google.com/abc-defg-hij": the link as shown, without the scheme. */
export function meetLabel(url: string): string {
  return url.replace(/^https:\/\//, "").replace(/\/$/, "");
}
