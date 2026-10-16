import { act, render, screen, within } from "@testing-library/react";
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


test.each(["done", "blocked", "error"])("navigation preserves the owning reply through %s", async (ending) => {
  document.cookie = "csrf_token=tok";
  const s = streamed([ev("delta", { text: "First" }), ev("delta", { text: " second" }),
    ev(ending, ending === "done" ? { id: 12 } : { message: "Stopped." })]);
  const convos = [
    { id: 9, model: "gemma", started_at: "", title: "Chat A" },
    { id: 10, model: "gemma", started_at: "", title: "Chat B" },
  ];
  vi.stubGlobal("fetch", routes({
    "GET /api/student/conversations": () => Response.json({ conversations: convos }),
    "GET /api/student/conversations/9": () => Response.json({ messages: [] }),
    "GET /api/student/conversations/10": () => Response.json({ messages: [
      { id: 11, role: "assistant", text: "Other conversation", status: "ok" },
    ] }),
    "POST /api/student/conversations/9/messages": () => s.response,
  }));
  render(<Chat student={student} onChanged={() => {}} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Chat A/ }));
  await user.type(await screen.findByLabelText("Your message"), "Question{Enter}");
  await act(async () => s.next());
  const log = screen.getByRole("log");
  await vi.waitFor(() => expect(log).toHaveTextContent("First"));
  await user.click(screen.getByRole("button", { name: /Chat B/ }));
  await vi.waitFor(() => expect(log).toHaveTextContent("Other conversation"));
  await act(async () => s.next());
  expect(log).not.toHaveTextContent("First");
  expect(log).not.toHaveTextContent("second");
  await user.click(screen.getByRole("button", { name: /Chat A/ }));
  await vi.waitFor(() => expect(log).toHaveTextContent("First second"));
  expect(log).toHaveTextContent("Question");
  expect(log).not.toHaveTextContent("Other conversation");
  await act(async () => s.next());
  await vi.waitFor(() => expect(screen.getByLabelText("Your message")).toBeEnabled());
  if (ending !== "done") expect(log).toHaveTextContent("Stopped.");
  expect(log).toHaveTextContent("First second");
});

test("a late transcript response cannot select an older navigation", async () => {
  let resolveA!: (response: Response) => void;
  const pendingA = new Promise<Response>((resolve) => { resolveA = resolve; });
  const base = routes({
    "GET /api/student/conversations": () => Response.json({ conversations: [
      { id: 9, model: "gemma", title: "Chat A" }, { id: 10, model: "gemma", title: "Chat B" },
    ] }),
    "GET /api/student/conversations/10": () => Response.json({ messages: [
      { id: 11, role: "assistant", text: "Conversation B", status: "ok" },
    ] }),
  });
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) =>
    url === "/api/student/conversations/9" ? pendingA : base(url, init)));
  render(<Chat student={student} onChanged={() => {}} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Chat A/ }));
  await user.click(screen.getByRole("button", { name: /Chat B/ }));
  await vi.waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("Conversation B"));
  await act(async () => resolveA(Response.json({ messages: [] })));
  expect(screen.getByRole("log")).toHaveTextContent("Conversation B");
  expect(screen.getByRole("button", { name: /Chat B/ })).toHaveAttribute("aria-current", "true");
});

test("a transcript loaded before a turn cannot erase the completed reply", async () => {
  document.cookie = "csrf_token=tok";
  let resolveHistory!: (response: Response) => void;
  const history = new Promise<Response>((resolve) => { resolveHistory = resolve; });
  const base = routes({
    "GET /api/student/conversations": () => Response.json({ conversations: [
      { id: 9, model: "gemma", title: "Chat A" },
    ] }),
    "POST /api/student/conversations/9/messages": () => new Response(
      ev("delta", { text: "A complete reply" }) + ev("done", { id: 12 }),
      { headers: { "content-type": "text/event-stream" } }),
  });
  let loads = 0;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) =>
    url === "/api/student/conversations/9"
      ? (++loads === 1 ? history : Promise.resolve(Response.json({ messages: [] })))
      : base(url, init)));
  render(<Chat student={student} onChanged={() => {}} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Chat A/ }));
  expect(screen.getByLabelText("Your message")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Chat A/ }));
  await vi.waitFor(() => expect(screen.getByLabelText("Your message")).toBeEnabled());
  await user.type(screen.getByLabelText("Your message"), "Question{Enter}");
  await vi.waitFor(() => expect(screen.getByLabelText("Your message")).toBeEnabled());
  expect(screen.getByRole("log")).toHaveTextContent("A complete reply");
  await act(async () => resolveHistory(Response.json({ messages: [] })));
  expect(screen.getByRole("log")).toHaveTextContent("A complete reply");
});

test.each(["done", "blocked", "error"])("terminal events while away stay with their conversation: %s", async (ending) => {
  document.cookie = "csrf_token=tok";
  const s = streamed([ev("delta", { text: "First" }), ev("delta", { text: " second" }),
    ev(ending, ending === "done" ? { id: 12 } : { message: "Stopped." })]);
  let loads = 0;
  const savedReply = ending === "blocked" ? "First second\nStopped." : "First second";
  vi.stubGlobal("fetch", routes({
    "GET /api/student/conversations": () => Response.json({ conversations: [
      { id: 9, model: "gemma", title: "Chat A" }, { id: 10, model: "gemma", title: "Chat B" },
    ] }),
    "GET /api/student/conversations/9": () => {
      loads++;
      if (loads === 1) return Response.json({ messages: [] });
      if (loads === 2) return Response.json({ error: "unavailable" }, { status: 503 });
      return Response.json({ messages: [
        { id: 11, role: "user", text: "Question", status: "ok" },
        { id: 12, role: "assistant", text: savedReply, status: ending === "done" ? "ok" : ending },
      ] });
    },
    "GET /api/student/conversations/10": () => Response.json({ messages: [
      { id: 21, role: "assistant", text: "Other conversation", status: "ok" },
    ] }),
    "POST /api/student/conversations/9/messages": () => s.response,
  }));
  render(<Chat student={student} onChanged={() => {}} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Chat A/ }));
  await user.type(await screen.findByLabelText("Your message"), "Question{Enter}");
  await act(async () => s.next());
  const log = screen.getByRole("log");
  await vi.waitFor(() => expect(log).toHaveTextContent("First"));
  await user.click(screen.getByRole("button", { name: /Chat B/ }));
  await vi.waitFor(() => expect(log).toHaveTextContent("Other conversation"));
  await act(async () => s.next());
  expect(log).toHaveTextContent("Other conversation");
  expect(log).not.toHaveTextContent("First");
  await act(async () => s.next());
  await vi.waitFor(() => expect(screen.getByLabelText("Your message")).toBeEnabled());
  expect(log).toHaveTextContent("Other conversation");
  expect(log).not.toHaveTextContent("First");
  expect(log).not.toHaveTextContent("Stopped.");
  // The first return cannot load history. It must retain the owning local turn,
  // including terminal messages which arrived while B was still selected.
  await user.click(screen.getByRole("button", { name: /Chat A/ }));
  await vi.waitFor(() => expect(loads).toBe(2));
  expect(log).toHaveTextContent("Question");
  expect(log).toHaveTextContent("First second");
  if (ending !== "done") expect(log).toHaveTextContent("Stopped.");
  expect(log).not.toHaveTextContent("Other conversation");
  // Retrying history reconciles the complete stored transcript, without duplicates.
  await user.click(screen.getByRole("button", { name: /Chat A/ }));
  await vi.waitFor(() => expect(screen.getByLabelText("Your message")).toBeEnabled());
  expect(loads).toBe(3);
  expect(within(log).getAllByText("You:", { exact: true })).toHaveLength(1);
  const replies = within(log).getAllByText("AI:", { exact: true });
  expect(replies).toHaveLength(1);
  expect(replies[0].parentElement).toHaveTextContent(savedReply.replace("\n", " "));
  if (ending === "error") expect(log).not.toHaveTextContent("Stopped.");
});
