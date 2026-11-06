import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import StudentPage from "@/app/page";
import TeachPage from "@/app/teach/page";
import type { ClassInfo, Conversation, LessonSession, Student, StudentCard, Transcript, Usage } from "@/lib/types";

class FakeEventSource {
  static CLOSED = 2;
  static last: FakeEventSource | null = null;
  readyState = 1;
  onerror: (() => void) | null = null;
  listeners = new Map<string, Array<() => void>>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(name: string, fn: () => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  emit(name: string) {
    for (const fn of this.listeners.get(name) ?? []) fn();
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

function ev(name: string, data: unknown) {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
}

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

test("student chat DOM: student header, monitoring banner, conversation flow, SSE streaming, and switching", async () => {
  document.cookie = "csrf_token=tok";

  const student: Student = {
    name: "Aroha",
    code: "123456",
    class_name: "Year 9 Science",
    state: "open",
    message_limit: 10,
    messages_used: 1,
  };

  let convoCount = 0;
  const conversations: Conversation[] = [];
  const transcriptMap: Record<number, Transcript["conversations"][0]["messages"]> = {};

  const json = (data: unknown) =>
    new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });

  const streamRef: { current: ReturnType<typeof streamed> | null } = { current: null };

  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/student/me") return json({ student });
    if (url === "/api/student/models") return json({ models: ["gemma-4-e2b-it"] });
    if (url === "/api/student/conversations" && (!init || init.method === "GET")) {
      return json({ conversations });
    }
    if (url === "/api/student/conversations" && init?.method === "POST") {
      const id = ++convoCount;
      const title = id === 1 ? "What is a cell?" : "What is DNA?";
      const convo: Conversation = { id, model: "gemma-4-e2b-it", title, started_at: new Date().toISOString() };
      conversations.unshift(convo);
      transcriptMap[id] = [];
      return json(convo);
    }
    if (url.includes("/messages") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      const match = url.match(/\/conversations\/(\d+)\/messages/);
      const id = match ? Number(match[1]) : 1;
      transcriptMap[id]?.push({
        id: 1,
        role: "user",
        text: body.text,
        status: "ok",
        at: "",
        flags: [],
      });
      streamRef.current = streamed([
        ev("delta", { text: "Cells are " }),
        ev("delta", { text: "units of life." }),
        ev("done", { id: 2 }),
      ]);
      transcriptMap[id]?.push({
        id: 2,
        role: "assistant",
        text: "Cells are units of life.",
        status: "ok",
        at: "",
        flags: [],
      });
      return streamRef.current.response;
    }
    const convoMatch = url.match(/\/api\/student\/conversations\/(\d+)$/);
    if (convoMatch) {
      const id = Number(convoMatch[1]);
      return json({ messages: transcriptMap[id] ?? [] });
    }
    return json({});
  }));

  render(<StudentPage />);

  // 1. Verify StudentHeader (role="banner") with name, code, class name
  const header = await screen.findByRole("banner");
  expect(header).toHaveTextContent("Aroha");
  expect(header).toHaveTextContent("123456");
  expect(header).toHaveTextContent("Year 9 Science");

  // 2. Verify monitoring banner
  expect(await screen.findByText("Your teacher can read everything you write here.")).toBeInTheDocument();

  // 3. Conversation creation
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "New conversation" }));
  const input = await screen.findByLabelText("Your message");
  expect(input).toBeEnabled();

  // 4. Send message & SSE streaming render
  await user.type(input, "What is a cell?");
  await user.click(screen.getByRole("button", { name: "Send" }));

  // Release streaming chunks
  await vi.waitFor(() => {
    streamRef.current?.next();
    expect(screen.getByRole("log", { name: "Conversation" })).toHaveTextContent("What is a cell?");
  });
  streamRef.current?.next();
  await vi.waitFor(() => {
    expect(screen.getByRole("log", { name: "Conversation" })).toHaveTextContent("Cells are units of life.");
  });
  streamRef.current?.next();

  // 5. Verify conversation appears in list
  expect(await screen.findByRole("button", { name: /What is a cell/ })).toBeInTheDocument();

  // 6. Start second conversation and verify conversation switching
  await user.click(screen.getByRole("button", { name: "New conversation" }));
  expect(screen.getByRole("log", { name: "Conversation" })).not.toHaveTextContent("What is a cell?");

  // Switch back to first conversation
  await user.click(screen.getByRole("button", { name: /What is a cell/ }));
  expect(await screen.findByRole("log", { name: "Conversation" })).toHaveTextContent("Cells are units of life.");
});

test("teacher console DOM: session controls, outage banner, card grid, and transcript slide-out", async () => {
  document.cookie = "csrf_token=tok";

  const teacherStaff = { staff: { username: "teacher1", role: "teacher" } };
  const session: LessonSession = {
    id: 7,
    class_id: 1,
    class_name: "Year 9 Science",
    state: "open",
    opened_at: new Date(Date.now() - 300000).toISOString(),
    closed_at: null,
    roster: [
      { id: 1, code: "100001", name: "Aroha", bound_at: null },
      { id: 2, code: "100002", name: "Ben", bound_at: null },
    ],
  };

  const classes: ClassInfo[] = [
    {
      id: 1,
      name: "Year 9 Science",
      instructions: "Guide inquiry",
      message_limit: 10,
      teacher: "teacher1",
      live_session: session,
    },
  ];

  const cards: StudentCard[] = [
    {
      id: 1,
      name: "Aroha",
      code: "100001",
      messages: 2,
      latest: { role: "assistant", text: "Cells are units of life.", status: "ok", at: new Date().toISOString() },
      unreviewed_flags: 1,
      last_active: new Date().toISOString(),
    },
    {
      id: 2,
      name: "Ben",
      code: "100002",
      messages: 1,
      latest: { role: "user", text: "What is an atom?", status: "ok", at: new Date().toISOString() },
      unreviewed_flags: 0,
      last_active: new Date().toISOString(),
    },
  ];

  const usage: Usage = {
    active_students: 2,
    total_messages: 3,
    per_student: [
      { id: 1, name: "Aroha", messages: 2 },
      { id: 2, name: "Ben", messages: 1 },
    ],
    models: [{ model: "gemma-4-e2b-it", conversations: 2 }],
    flags_by_category: [{ category: "Violent", flags: 1 }],
  };

  const arohaTranscript: Transcript = {
    student: { id: 1, name: "Aroha", code: "100001", removed: false },
    conversations: [
      {
        id: 1,
        model: "gemma-4-e2b-it",
        started_at: "",
        messages: [
          { id: 1, role: "user", text: "Tell me about cells", status: "ok", at: "", flags: [] },
          { id: 2, role: "assistant", text: "Cells are units of life.", status: "ok", at: "", flags: [] },
          {
            id: 3,
            role: "user",
            text: "Unsafe search term",
            status: "blocked",
            at: "",
            flags: [{ id: 10, category: "Violent", action: "block_flag", at: "", reviewed_by: null, reviewed_at: null }],
          },
        ],
      },
    ],
  };

  const benTranscript: Transcript = {
    student: { id: 2, name: "Ben", code: "100002", removed: false },
    conversations: [
      {
        id: 2,
        model: "gemma-4-e2b-it",
        started_at: "",
        messages: [
          { id: 4, role: "user", text: "What is an atom?", status: "ok", at: "", flags: [] },
          { id: 5, role: "assistant", text: "An atom is the smallest unit of an element.", status: "ok", at: "", flags: [] },
        ],
      },
    ],
  };

  const json = (data: unknown, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/me") return json(teacherStaff);
    if (url === "/api/classes") return json({ classes });
    if (url === "/api/sessions/7") return json(session);
    if (url === "/api/sessions/7/students") return json({ students: cards });
    if (url === "/api/sessions/7/usage") return json(usage);
    if (url === "/api/students/1/transcript") return json(arohaTranscript);
    if (url === "/api/students/2/transcript") return json(benTranscript);
    if (url === "/api/models") return json({ models: [{ name: "gemma-4-e2b-it", enabled: true }] });
    if (url === "/healthz" || url === "/api/healthz") return json({ status: "down", db: "ok", guard: "down" }, 503);
    return json({});
  }));

  render(<TeachPage />);

  // 1. GuardHealth outage banner renders when guard is down
  const banner = await screen.findByRole("alert");
  expect(banner).toHaveTextContent("The guard is unavailable. Student chat is blocked until the guard is healthy.");

  // 2. Session bar controls are rendered
  const headings = await screen.findAllByRole("heading", { level: 2, name: "Year 9 Science" });
  expect(headings.length).toBeGreaterThan(0);
  expect(await screen.findByRole("button", { name: "Pause" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Close lesson" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Print slips" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Generate" })).toBeInTheDocument();

  // 3. Student card grid renders cards
  const arohaCard = await screen.findByRole("button", { name: /Aroha/ });
  const benCard = screen.getByRole("button", { name: /Ben/ });
  expect(arohaCard).toBeInTheDocument();
  expect(benCard).toBeInTheDocument();
  expect(arohaCard).toHaveTextContent("Flagged: 1");

  // 4. Click Aroha's card -> opens transcript panel
  const user = userEvent.setup();
  await user.click(arohaCard);

  const arohaPanel = await screen.findByRole("region", { name: "Transcript for Aroha" });
  expect(arohaPanel).toBeInTheDocument();
  expect(arohaPanel).toHaveTextContent("Aroha (100001)");
  expect(arohaPanel).toHaveTextContent("Unsafe search term");
  expect(arohaPanel).toHaveTextContent("Flag: Violent");

  // 5. Click Ben's card -> transcript updates to Ben
  await user.click(benCard);
  const benPanel = await screen.findByRole("region", { name: "Transcript for Ben" });
  expect(benPanel).toBeInTheDocument();
  expect(benPanel).toHaveTextContent("What is an atom?");
  expect(benPanel).toHaveTextContent("An atom is the smallest unit of an element.");
  expect(screen.queryByRole("region", { name: "Transcript for Aroha" })).not.toBeInTheDocument();

  // 6. Close button hides the transcript
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("region", { name: "Transcript for Ben" })).not.toBeInTheDocument();
});
