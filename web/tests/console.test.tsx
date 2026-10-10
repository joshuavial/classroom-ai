import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { CardGrid, Console, TranscriptPanel, UsageStrip } from "@/app/teach/console";
import type { StudentCard, Transcript } from "@/lib/types";
import { mockApi } from "./fetch";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

const card = (over: Partial<StudentCard>): StudentCard => ({
  id: 1, name: "Aroha", code: "123456", messages: 0, latest: null, unreviewed_flags: 0, last_active: null, ...over,
});

test("cards show name, code, count and latest message, and mark flags with text as well as colour", () => {
  render(
    <CardGrid
      cards={[
        card({ messages: 3, latest: { role: "user", text: "what is a cell", status: "ok", at: "" } }),
        card({ id: 2, name: "Ben", code: "654321", unreviewed_flags: 2,
               latest: { role: "user", text: "bad", status: "blocked", at: "" } }),
      ]}
      selected={null}
      onSelect={() => {}}
    />,
  );
  const [aroha, ben] = screen.getAllByRole("button");
  expect(aroha).toHaveTextContent("Aroha");
  expect(aroha).toHaveTextContent("123456");
  expect(aroha).toHaveTextContent("3 messages");
  expect(aroha).toHaveTextContent("what is a cell");
  expect(aroha).not.toHaveTextContent("Flagged");
  expect(ben).toHaveTextContent("Flagged: 2");
  expect(ben).toHaveTextContent("[blocked]");
});

test("an empty class says nobody has joined", () => {
  render(<CardGrid cards={[]} selected={null} onSelect={() => {}} />);
  expect(screen.getByText("No students have joined yet.")).toBeInTheDocument();
});

test("the usage strip shows the summary", () => {
  render(
    <UsageStrip
      usage={{
        active_students: 4, total_messages: 17, per_student: [],
        models: [{ model: "gemma", conversations: 5 }], flags_by_category: [{ category: "Violent", flags: 1 }],
      }}
    />,
  );
  const strip = screen.getByRole("region", { name: "Usage" });
  expect(strip).toHaveTextContent("Active in the last 5 minutes: 4");
  expect(strip).toHaveTextContent("Messages: 17");
  expect(strip).toHaveTextContent("gemma 5");
  expect(strip).toHaveTextContent("Violent 1");
});

const transcript: Transcript = {
  student: { id: 1, name: "Aroha", code: "123456", removed: false },
  conversations: [{
    id: 9, model: "gemma", started_at: "",
    messages: [
      { id: 1, role: "user", text: "hi", status: "ok", at: "", flags: [] },
      { id: 2, role: "user", text: "bad thing", status: "blocked", at: "",
        flags: [{ id: 5, category: "Violent", action: "block_flag", at: "", reviewed_by: null, reviewed_at: null }] },
    ],
  }],
};

test("the transcript shows blocked messages and flag reasons inline with Mark reviewed", async () => {
  const onReview = vi.fn();
  render(<TranscriptPanel transcript={transcript} onReview={onReview} onClose={() => {}} />);
  const panel = screen.getByRole("region", { name: "Transcript for Aroha" });
  expect(panel).toHaveTextContent("bad thing (blocked)");
  expect(panel).toHaveTextContent("Flag: Violent (blocked and flagged)");
  await userEvent.setup().click(within(panel).getByRole("button", { name: "Mark reviewed" }));
  expect(onReview).toHaveBeenCalledWith(5);
});

test("a reviewed flag says who reviewed it", () => {
  const reviewed = structuredClone(transcript);
  reviewed.conversations[0].messages[1].flags[0] = { ...reviewed.conversations[0].messages[1].flags[0],
    reviewed_by: "ms-smith", reviewed_at: "2026-10-10T00:00:00Z" };
  render(<TranscriptPanel transcript={reviewed} onReview={() => {}} onClose={() => {}} />);
  expect(screen.getByText("Reviewed by ms-smith.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Mark reviewed" })).not.toBeInTheDocument();
});

test("clicking a card opens its transcript and Mark reviewed posts the review", async () => {
  document.cookie = "csrf_token=tok";
  const fetch = mockApi({
    "GET /sessions/7/students": { body: { students: [card({ unreviewed_flags: 1 })] } },
    "GET /sessions/7/usage": { body: { active_students: 1, total_messages: 2, per_student: [], models: [], flags_by_category: [] } },
    "GET /students/1/transcript": { body: transcript },
    "POST /flags/5/review": { body: { id: 5 } },
  });
  render(<Console sessionId={7} version={0} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Aroha/ }));
  const panel = await screen.findByRole("region", { name: "Transcript for Aroha" });
  await user.click(within(panel).getByRole("button", { name: "Mark reviewed" }));
  await vi.waitFor(() =>
    expect(fetch.mock.calls.some(([url, init]) => url === "/api/flags/5/review" && init?.method === "POST")).toBe(true),
  );
});
