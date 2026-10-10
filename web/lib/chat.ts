// Sending a chat message and reading the reply as it is written. The reply
// arrives as a server-sent event stream on a POST, so EventSource cannot be
// used; this reads the response body directly.

import { ApiError, CSRF_COOKIE, CSRF_HEADER } from "@/lib/api";

export type ChatOutcome =
  | { kind: "done" }
  | { kind: "blocked"; message: string }
  | { kind: "error"; message: string };

const CUT_OFF = "The reply was cut off. Try sending your message again.";

function csrfToken(): string | undefined {
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === CSRF_COOKIE) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

/** Parse complete events out of `buffer`; return the unparsed remainder. */
export function takeEvents(buffer: string, onEvent: (name: string, data: Record<string, unknown>) => void): string {
  let rest = buffer;
  for (let end = rest.indexOf("\n\n"); end !== -1; end = rest.indexOf("\n\n")) {
    const block = rest.slice(0, end);
    rest = rest.slice(end + 2);
    let name = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7);
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (data) onEvent(name, JSON.parse(data));
  }
  return rest;
}

/**
 * Send `text` in a conversation. `onDelta` gets reply text as it arrives.
 * Throws ApiError for a refusal before the reply starts (paused, limit, model
 * off, chat unavailable).
 */
export async function sendMessage(
  conversationId: number,
  text: string,
  onDelta: (text: string) => void,
): Promise<ChatOutcome> {
  if (!csrfToken()) await fetch("/api/me", { credentials: "same-origin" });
  const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "text/event-stream" };
  const token = csrfToken();
  if (token) headers[CSRF_HEADER] = token;
  const response = await fetch(`/api/student/conversations/${conversationId}/messages`, {
    method: "POST",
    headers,
    credentials: "same-origin",
    body: JSON.stringify({ text }),
  });
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    const body = await response.text();
    let parsed: unknown = body;
    try {
      parsed = JSON.parse(body);
    } catch {}
    throw new ApiError(response.status, parsed);
  }
  if (!type.startsWith("text/event-stream")) {
    const body = (await response.json()) as { blocked?: string };
    return { kind: "blocked", message: body.blocked ?? "" };
  }
  let outcome: ChatOutcome | null = null;
  const handle = (name: string, data: Record<string, unknown>) => {
    if (name === "delta") onDelta(String(data.text ?? ""));
    else if (name === "done") outcome = { kind: "done" };
    else if (name === "blocked") outcome = { kind: "blocked", message: String(data.message ?? "") };
    else if (name === "error") outcome = { kind: "error", message: String(data.message ?? CUT_OFF) };
  };
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer = takeEvents(buffer + decoder.decode(value, { stream: true }), handle);
    }
  } catch {
    // The connection dropped; whatever arrived is shown and kept.
  }
  return outcome ?? { kind: "error", message: CUT_OFF };
}
