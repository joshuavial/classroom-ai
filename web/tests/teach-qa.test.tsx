import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import TeachPage from "@/app/teach/page";
import { Lesson } from "@/app/teach/lesson";
import type { LessonSession } from "@/lib/types";
import { mockApi, stubLocation } from "./fetch";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

const cls = { id: 1, name: "Year 9", instructions: "", message_limit: null, live_session: null };

test("Cancel on the class edit form does not save", async () => {
  document.cookie = "csrf_token=tok";
  stubLocation();
  const fetch = mockApi({
    "GET /models": { body: { models: [] } },
    "GET /me": { body: { staff: { username: "t", role: "teacher" } } },
    "GET /classes": { body: { classes: [cls] } },
    "PATCH /classes/1": { body: { ...cls, name: "Changed" } },
  });
  render(<TeachPage />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Edit class" }));
  const item = within(screen.getByRole("article", { name: "Year 9" }));
  const name = item.getByLabelText("Class name");
  await user.clear(name);
  await user.type(name, "Changed");
  await user.click(item.getByRole("button", { name: "Cancel" }));
  const patched = fetch.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "PATCH");
  expect(patched).toBe(false);
});

const liveSession: LessonSession = {
  id: 1,
  state: "open",
  class_id: 1,
  class_name: "Year 9",
  opened_at: "2026-10-10T00:00:00Z",
  closed_at: null,
  roster: [
    { id: 10, code: "123456", name: "Aroha", bound_at: "2026-10-10T00:01:00Z" },
    { id: 11, code: "654321", name: null, bound_at: null },
  ],
};

test("all controls in the lesson session bar and roster are reachable by keyboard with focus", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({
    "GET /sessions/1": { body: liveSession },
    "GET /sessions/1/students": { body: { students: [] } },
    "GET /sessions/1/usage": {
      body: { active_students: 1, total_messages: 0, per_student: [], models: [], flags_by_category: [] },
    },
  });

  class MockEventSource {
    close = vi.fn();
    addEventListener = vi.fn();
    removeEventListener = vi.fn();
  }
  vi.stubGlobal("EventSource", MockEventSource);

  render(<Lesson sessionId={1} onEnded={() => {}} />);
  const user = userEvent.setup();

  const pauseBtn = await screen.findByRole("button", { name: "Pause" });
  const closeBtn = screen.getByRole("button", { name: "Close lesson" });
  const printLink = screen.getByRole("link", { name: "Print slips" });
  const countInput = screen.getByRole("spinbutton", { name: /More codes/ });
  const generateBtn = screen.getByRole("button", { name: "Generate" });
  const refreshBtn = screen.getByRole("button", { name: "Refresh" });
  const renameBtn = screen.getByRole("button", { name: "Rename" });
  const unbindBtn = screen.getByRole("button", { name: "Unbind code 123456" });
  const removeBoundBtn = screen.getByRole("button", { name: "Remove code 123456" });
  const removeUnboundBtn = screen.getByRole("button", { name: "Remove code 654321" });

  // Tab through session bar controls
  await user.tab();
  expect(pauseBtn).toHaveFocus();

  await user.tab();
  expect(closeBtn).toHaveFocus();

  await user.tab();
  expect(printLink).toHaveFocus();

  await user.tab();
  expect(countInput).toHaveFocus();

  await user.tab();
  expect(generateBtn).toHaveFocus();

  await user.tab();
  expect(refreshBtn).toHaveFocus();

  // Tab through roster actions
  await user.tab();
  expect(renameBtn).toHaveFocus();

  await user.tab();
  expect(unbindBtn).toHaveFocus();

  await user.tab();
  expect(removeBoundBtn).toHaveFocus();

  await user.tab();
  expect(removeUnboundBtn).toHaveFocus();
});
