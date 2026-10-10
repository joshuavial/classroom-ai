import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Records from "@/components/Records";

type Call = { path: string; method: string; body: unknown };
let calls: Call[];
let routes: Record<string, (call: Call) => { status?: number; body: unknown }>;

beforeEach(() => {
  calls = [];
  routes = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const call = {
        path: url.replace(/^\/api/, ""),
        method: init.method ?? "GET",
        body: init.body ? JSON.parse(init.body as string) : undefined,
      };
      calls.push(call);
      const handler = routes[`${call.method} ${call.path}`];
      const out = handler ? handler(call) : { status: 404, body: { error: "not_found" } };
      return new Response(JSON.stringify(out.body), { status: out.status ?? 200 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function row(n: number) {
  return { id: n, username: "admin", action: "model.enable", detail: { model: `m${n}` }, time: "2026-10-10T09:00:00Z" };
}

async function submit(form: HTMLElement) {
  await act(async () => {
    fireEvent.submit(form);
  });
}

test("shows retention, the backup link and the audit log with paging", async () => {
  routes["GET /admin/retention"] = () => ({ body: { days: 30 } });
  routes["GET /admin/audit"] = () => ({ body: { audit: Array.from({ length: 100 }, (_, i) => row(200 - i)) } });
  routes["GET /admin/audit?before=101"] = () => ({ body: { audit: [row(100), row(99)] } });
  render(<Records />);
  expect(await screen.findByDisplayValue("30")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Download a backup" })).toHaveAttribute("href", "/api/admin/backup");
  expect(await screen.findByText(JSON.stringify({ model: "m200" }))).toBeInTheDocument();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Older entries" }));
  });
  expect(await screen.findByText(JSON.stringify({ model: "m99" }))).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Older entries" })).not.toBeInTheDocument();
});

test("lowering retention asks first; raising it does not", async () => {
  routes["GET /admin/retention"] = () => ({ body: { days: 30 } });
  routes["GET /admin/audit"] = () => ({ body: { audit: [] } });
  routes["PUT /admin/retention"] = (call) => ({ body: call.body });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<Records />);
  const input = await screen.findByDisplayValue("30");
  fireEvent.change(input, { target: { value: "7" } });
  await submit(input.closest("form")!);
  expect(calls.some((c) => c.method === "PUT")).toBe(false);
  await submit(input.closest("form")!);
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ days: 7 });
  expect(await screen.findByText("Conversations are now kept for 7 days.")).toBeInTheDocument();
  fireEvent.change(await screen.findByDisplayValue("7"), { target: { value: "60" } });
  await submit(screen.getByDisplayValue("60").closest("form")!);
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ days: 7 }, { days: 60 }]);
});

test("a refused retention value says so", async () => {
  routes["GET /admin/retention"] = () => ({ body: { days: 30 } });
  routes["GET /admin/audit"] = () => ({ body: { audit: [] } });
  routes["PUT /admin/retention"] = () => ({ status: 400, body: { error: "bad_days" } });
  render(<Records />);
  const input = await screen.findByDisplayValue("30");
  fireEvent.change(input, { target: { value: "40" } });
  await submit(input.closest("form")!);
  expect(await screen.findByText("Use a whole number of days from 1 to 3650.")).toBeInTheDocument();
});
