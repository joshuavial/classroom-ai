import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Lesson } from "@/app/teach/lesson";
import { mockApi } from "./fetch";

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

beforeEach(() => vi.stubGlobal("EventSource", FakeEventSource));
afterEach(() => vi.unstubAllGlobals());

const session = (names: (string | null)[]) => ({
  body: {
    id: 7,
    state: "open",
    class_id: 1,
    class_name: "Year 9",
    opened_at: new Date().toISOString(),
    closed_at: null,
    roster: names.map((name, i) => ({ id: i + 1, code: `00000${i}`, name, bound_at: name ? "x" : null })),
  },
});

test("the roster reloads when the live stream reports a join", async () => {
  mockApi({ "GET /sessions/7": [session([null]), session(["Aroha"])] });
  const { unmount } = render(<Lesson sessionId={7} onEnded={() => {}} />);
  expect(await screen.findByText("Not used")).toBeInTheDocument();
  expect(FakeEventSource.last?.url).toBe("/api/sessions/7/events");
  act(() => FakeEventSource.last?.emit("join"));
  expect(await screen.findByText("Aroha", {}, { timeout: 2000 })).toBeInTheDocument();
  unmount();
  expect(FakeEventSource.last?.closed).toBe(true);
});

test("a slow roster response cannot replace a newer live refresh", async () => {
  let releaseOld!: () => void;
  let calls = 0;
  const json = (body: unknown) => new Response(JSON.stringify(body));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/sessions/7") {
      const call = ++calls;
      if (call === 1) await new Promise<void>((resolve) => (releaseOld = resolve));
      return json(session(call === 1 ? [null] : ["Aroha"]).body);
    }
    if (url.endsWith("/students")) return json({ students: [] });
    return json({ active_students: 0, total_messages: 0, per_student: [], models: [], flags_by_category: [] });
  }));
  render(<Lesson sessionId={7} onEnded={() => {}} />);
  act(() => FakeEventSource.last?.emit("join"));
  expect(await screen.findByText("Aroha", {}, { timeout: 2000 })).toBeInTheDocument();
  releaseOld();
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.getByText("Aroha")).toBeInTheDocument();
  expect(screen.queryByText("Not used")).not.toBeInTheDocument();
});

test("a stream the server refused is opened again after 15 seconds", async () => {
  vi.useFakeTimers();
  try {
    mockApi({ "GET /sessions/7": session([null]) });
    render(<Lesson sessionId={7} onEnded={() => {}} />);
    const first = FakeEventSource.last!;
    first.readyState = FakeEventSource.CLOSED;
    act(() => first.onerror?.());
    act(() => vi.advanceTimersByTime(15000));
    expect(FakeEventSource.last).not.toBe(first);
  } finally {
    vi.useRealTimers();
  }
});
