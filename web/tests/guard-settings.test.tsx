import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import GuardSettings, { type GuardSettingsDocument } from "@/components/GuardSettings";

let settings: GuardSettingsDocument;
let saved: unknown;

function response(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  saved = null;
  settings = {
    version: 1,
    categories: {
      Violent: { enabled: true, action: "block_flag", message: "Please ask a teacher." },
      "Self-harm": { enabled: false, action: "block", message: "Please ask for help." },
    },
    uncategorized: { action: "block_flag", message: "This needs a teacher to check it." },
  };
  document.cookie = "csrf_token=test";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "PUT") {
        saved = JSON.parse(init.body as string);
        settings = saved as GuardSettingsDocument;
      }
      return response(200, settings);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

test("edits every category and the always-active fallback", async () => {
  render(<GuardSettings />);
  const violent = (await screen.findByRole("group", { name: "Violent" })) as HTMLElement;
  const selfHarm = screen.getByRole("group", { name: "Self-harm" });
  expect(within(violent).getByRole("checkbox")).toBeChecked();
  expect(within(selfHarm).getByRole("checkbox")).not.toBeChecked();

  fireEvent.click(within(violent).getByRole("checkbox"));
  fireEvent.change(within(violent).getByRole("combobox"), { target: { value: "allow_flag" } });
  fireEvent.change(within(violent).getByRole("textbox"), { target: { value: "A new message." } });
  const fallback = screen.getByRole("group", { name: "Uncategorized" });
  expect(within(fallback).queryByRole("checkbox")).not.toBeInTheDocument();
  fireEvent.change(within(fallback).getByRole("combobox"), { target: { value: "block" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save guard settings" })));

  expect((saved as GuardSettingsDocument).categories.Violent).toEqual({
    enabled: false,
    action: "allow_flag",
    message: "A new message.",
  });
  expect((saved as GuardSettingsDocument).uncategorized.action).toBe("block");
  expect(screen.getByRole("status")).toHaveTextContent("Guard settings saved");
});

test("shows fail-closed load and save errors", async () => {
  const fetch = vi.mocked(global.fetch);
  fetch.mockResolvedValueOnce(response(503, { error: "guard_configuration_invalid" }));
  render(<GuardSettings />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Chat remains blocked");

  fetch.mockResolvedValueOnce(response(200, settings));
  render(<GuardSettings />);
  await screen.findByRole("group", { name: "Violent" });
  fetch.mockResolvedValueOnce(response(500, { error: "unavailable" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save guard settings" })));
  expect(screen.getByRole("status")).toHaveTextContent("Chat remains blocked");
});

test("explains the winner-neutral empty category list", async () => {
  settings.categories = {};
  render(<GuardSettings />);
  expect(await screen.findByText(/Categories will appear/)).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Uncategorized" })).toBeInTheDocument();
});
