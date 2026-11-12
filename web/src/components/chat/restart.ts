/** What the header's "start again" control does for the chat as it is (SAZ-72). */
export type RestartControl = "hidden" | "direct" | "confirm";

/**
 * Hidden while the chat opens or a reply streams in, and before Ana's first message.
 * A chat the candidate has written in asks first; one they have not starts again at once.
 */
export function restartControl(status: string, messages: readonly { role: "agent" | "candidate" }[]): RestartControl {
  if (status !== "ready" || messages.length === 0) return "hidden";
  return messages.some((message) => message.role === "candidate") ? "confirm" : "direct";
}
