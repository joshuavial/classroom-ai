import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import TeachPage from "@/app/teach/page";
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
