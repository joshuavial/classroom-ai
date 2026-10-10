import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Chat } from "@/app/chat";
import { sendMessage, takeEvents } from "@/lib/chat";
import type { Student } from "@/lib/types";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

const student: Student = { name: "Aroha", code: "123456", class_name: "Year 9", state: "open",
                           message_limit: 5, messages_used: 2 };

/** A streamed response whose chunks are released one at a time. */
function streamed(chunks: string[]) {
  const encoder = new TextEncoder();
  const releases: Array<() => void> = [];
  const stream = new ReadableStream({
    async start(controller) {
      for (const chunk of chunks) {
        await new Promise<void>((r) => releases.push(r));
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return {
    response: new Response(stream, { headers: { "content-type": "text/event-stream" } }),
    next: () => releases.shift()?.(),
  };
}

function ev(name: string, data: unknown) {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
}

test("events split across chunks are parsed whole", () => {
  const seen: string[] = [];
  let rest = takeEvents("event: delta\ndata: {\"te", (n, d) => seen.push(`${n}:${d.text}`));
  rest = takeEvents(`${rest}xt":"Hi"}\n\nevent: done\ndata: {}\n\n`, (n, d) => seen.push(`${n}:${d.text ?? ""}`));
  expect(seen).toEqual(["delta:Hi", "done:"]);
  expect(rest).toBe("");
});

test("the reply renders as each chunk arrives", async () => {
  document.cookie = "csrf_token=tok";
  const s = streamed([ev("delta", { text: "Hello" }), ev("delta", { text: " there" }), ev("done", { id: 1 })]);
  vi.stubGlobal("fetch", vi.fn(async () => s.response));
  const parts: string[] = [];
  const done = sendMessage(1, "hi", (t) => parts.push(t));
  await vi.waitFor(() => {
    s.next();
    expect(parts).toEqual(["Hello"]);
  });
  s.next();
  await vi.waitFor(() => expect(parts).toEqual(["Hello", " there"]));
  s.next();
  await expect(done).resolves.toEqual({ kind: "done" });
});

test("a stream that ends with no done event counts as cut off", async () => {
  document.cookie = "csrf_token=tok";
  vi.stubGlobal("fetch", vi.fn(async () =>
    new Response(ev("delta", { text: "Par" }), { headers: { "content-type": "text/event-stream" } })));
  const outcome = await sendMessage(1, "hi", () => {});
  expect(outcome.kind).toBe("error");
});

test("a blocked prompt comes back as JSON", async () => {
  document.cookie = "csrf_token=tok";
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ blocked: "Let's talk about something else." }),
    { headers: { "content-type": "application/json" } })));
  await expect(sendMessage(1, "bad", () => {})).resolves.toEqual({
    kind: "blocked", message: "Let's talk about something else.",
  });
});

function routes(extra: Record<string, () => Response> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url}`;
    if (extra[key]) return extra[key]();
    if (key === "GET /api/student/models") return Response.json({ models: ["gemma"] });
    if (key === "GET /api/student/conversations") return Response.json({ conversations: [] });
    if (key === "POST /api/student/conversations")
      return Response.json({ id: 9, model: "gemma", started_at: "", title: "" }, { status: 201 });
    throw new Error(`unexpected ${key}`);
  });
}

test("the page shows the monitoring notice, the enabled models and the messages left", async () => {
  vi.stubGlobal("fetch", routes());
  render(<Chat student={student} onChanged={() => {}} />);
  expect(screen.getByText("Your teacher can read everything you write here.")).toBeInTheDocument();
  expect(await screen.findByRole("option", { name: "gemma" })).toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(1);
  expect(screen.getByText("Messages left this lesson: 3")).toBeInTheDocument();
});

test("a conversation streams the reply and shows a blocked reply's message", async () => {
  document.cookie = "csrf_token=tok";
  vi.stubGlobal("fetch", routes({
    "POST /api/student/conversations/9/messages": () => new Response(
      ev("delta", { text: "Cells are" }) + ev("blocked", { message: "That reply was stopped." }),
      { headers: { "content-type": "text/event-stream" } }),
  }));
  render(<Chat student={student} onChanged={() => {}} />);
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "gemma" });
  await user.click(screen.getByRole("button", { name: "New conversation" }));
  await user.type(screen.getByLabelText("Your message"), "What is a cell?{Enter}");
  const log = await screen.findByRole("log", { name: "Conversation" });
  expect(await within(log).findByText(/That reply was stopped\./)).toBeInTheDocument();
  expect(log).toHaveTextContent("Cells are");
  expect(log).toHaveTextContent("What is a cell?");
});

test("a refusal before the reply is shown and the empty reply removed", async () => {
  document.cookie = "csrf_token=tok";
  vi.stubGlobal("fetch", routes({
    "POST /api/student/conversations/9/messages": () =>
      Response.json({ error: "limit_reached" }, { status: 409 }),
  }));
  render(<Chat student={student} onChanged={() => {}} />);
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "gemma" });
  await user.click(screen.getByRole("button", { name: "New conversation" }));
  await user.type(screen.getByLabelText("Your message"), "hi{Enter}");
  expect(await screen.findByText("You have used all your messages for this lesson.")).toBeInTheDocument();
  expect(screen.getByRole("log", { name: "Conversation" })).not.toHaveTextContent("AI:");
});

test("sending is off while the class is paused", async () => {
  document.cookie = "csrf_token=tok";
  vi.stubGlobal("fetch", routes());
  render(<Chat student={{ ...student, state: "paused" }} onChanged={() => {}} />);
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "gemma" });
  await user.click(screen.getByRole("button", { name: "New conversation" }));
  expect(await screen.findByLabelText("Your message")).toBeDisabled();
});
