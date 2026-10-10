import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { Console } from "@/app/teach/console";
import type { Transcript } from "@/lib/types";

afterEach(() => vi.unstubAllGlobals());

const card = (id: number, name: string) => ({
  id, name, code: String(100000 + id), messages: 0, latest: null, unreviewed_flags: 0, last_active: null,
});
const tr = (id: number, name: string) => ({
  student: { id, name, code: "x", removed: false }, conversations: [],
});

// Click A, then B before A's transcript arrives: the panel must end on B.
// Found by the cai-1ir.8 QA worker; fixed with a stale-response guard.
test("a slow transcript for a deselected card does not replace the selected one", async () => {
  let releaseA!: () => void;
  const json = (b: unknown) => new Response(JSON.stringify(b));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/students")) return json({ students: [card(1, "Aroha"), card(2, "Ben")] });
    if (url.endsWith("/usage"))
      return json({ active_students: 0, total_messages: 0, per_student: [], models: [], flags_by_category: [] });
    if (url === "/api/students/1/transcript") {
      await new Promise<void>((r) => (releaseA = r));
      return json(tr(1, "Aroha"));
    }
    return json(tr(2, "Ben"));
  }));
  render(<Console sessionId={7} version={0} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Aroha/ }));
  await user.click(screen.getByRole("button", { name: /Ben/ }));
  await screen.findByRole("region", { name: "Transcript for Ben" });
  releaseA();
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.queryByRole("region", { name: "Transcript for Aroha" })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Transcript for Ben" })).toBeInTheDocument();
});

test("a slow transcript refresh for the same card cannot replace newer messages", async () => {
  let releaseOld!: () => void;
  let transcriptCalls = 0;
  const json = (body: unknown) => new Response(JSON.stringify(body));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/students")) return json({ students: [card(1, "Aroha")] });
    if (url.endsWith("/usage"))
      return json({ active_students: 1, total_messages: 1, per_student: [], models: [], flags_by_category: [] });
    const call = ++transcriptCalls;
    if (call === 1) await new Promise<void>((resolve) => (releaseOld = resolve));
    const result: Transcript = tr(1, "Aroha");
    result.conversations = [{ id: 1, model: "gemma", started_at: "", messages: [{
      id: call, role: "assistant", text: call === 1 ? "old" : "current", status: "ok", at: "", flags: [],
    }] }];
    return json(result);
  }));
  const view = render(<Console sessionId={7} version={0} />);
  await userEvent.setup().click(await screen.findByRole("button", { name: /Aroha/ }));
  view.rerender(<Console sessionId={7} version={1} />);
  expect(await screen.findByText("current")).toBeInTheDocument();
  releaseOld();
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.queryByText("old")).not.toBeInTheDocument();
  expect(screen.getByText("current")).toBeInTheDocument();
});

test("a slow card and usage refresh cannot replace newer live state", async () => {
  let releaseCards!: () => void;
  let releaseUsage!: () => void;
  let cardCalls = 0;
  let usageCalls = 0;
  const json = (body: unknown) => new Response(JSON.stringify(body));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/students")) {
      const call = ++cardCalls;
      if (call === 1) await new Promise<void>((resolve) => (releaseCards = resolve));
      return json({ students: [card(1, call === 1 ? "Old" : "Current")] });
    }
    const call = ++usageCalls;
    if (call === 1) await new Promise<void>((resolve) => (releaseUsage = resolve));
    return json({ active_students: call === 1 ? 0 : 1, total_messages: call === 1 ? 0 : 2,
      per_student: [], models: [], flags_by_category: [] });
  }));
  const view = render(<Console sessionId={7} version={0} />);
  view.rerender(<Console sessionId={7} version={1} />);
  expect(await screen.findByRole("button", { name: /Current/ })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Usage" })).toHaveTextContent("Messages: 2");
  releaseCards();
  releaseUsage();
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.queryByRole("button", { name: /Old/ })).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Usage" })).toHaveTextContent("Messages: 2");
});
