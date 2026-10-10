import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Records from "@/components/Records";

type Call = { path: string; method: string; body: unknown };
let calls: Call[];
type Out = { status?: number; body: unknown; headers?: Record<string, string>; raw?: boolean };
let routes: Record<string, (call: Call) => Out>;
let saved: string[];

beforeEach(() => {
  calls = [];
  routes = {};
  saved = [];
  // jsdom has no object URLs.
  Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => {} });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
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
      const out: Out = handler ? handler(call) : { status: 404, body: { error: "not_found" } };
      const body = out.raw ? (out.body as string) : JSON.stringify(out.body);
      return new Response(body, { status: out.status ?? 200, headers: out.headers });
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
  expect(await screen.findByText("Messages are now kept for 7 days.")).toBeInTheDocument();
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

const students = [
  { id: 2, code: "222222", name: "Ben", removed: true, lesson_session_id: 1, opened: "2026-10-10T09:00:00Z",
    class: "Year 9", messages: 1 },
  { id: 1, code: "111111", name: "Aroha", removed: false, lesson_session_id: 1, opened: "2026-10-10T09:00:00Z",
    class: "Year 9", messages: 2 },
];

function base() {
  routes["GET /admin/retention"] = () => ({ body: { days: 30 } });
  routes["GET /admin/audit"] = () => ({ body: { audit: [] } });
  routes["GET /admin/students?q="] = () => ({ body: { students } });
}

test("backup is a POST download, and a failure says so", async () => {
  base();
  let ok = true;
  routes["POST /admin/backup"] = () =>
    ok
      ? { raw: true, body: "PGDMP...", headers: { "Content-Disposition": 'attachment; filename="classroom-ai-1.dump"' } }
      : { status: 500, body: { error: "backup_failed" } };
  render(<Records />);
  await screen.findByDisplayValue("30");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Download a backup" }));
  });
  expect(calls.find((c) => c.path === "/admin/backup")?.method).toBe("POST");
  expect(saved).toEqual(["classroom-ai-1.dump"]);
  ok = false;
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Download a backup" }));
  });
  expect(await screen.findByText(/The backup failed/)).toBeInTheDocument();
  expect(saved).toEqual(["classroom-ai-1.dump"]);
});

test("students are listed, removed ones marked, and searched", async () => {
  base();
  routes["GET /admin/students?q=ben"] = () => ({ body: { students: [students[0]] } });
  render(<Records />);
  expect(await screen.findByText("Aroha")).toBeInTheDocument();
  expect(screen.getByText(/Ben/).textContent).toContain("(removed)");
  fireEvent.change(screen.getByLabelText("Find a student by name or code"), { target: { value: "ben" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Find" }));
  });
  expect(screen.queryByText("Aroha")).not.toBeInTheDocument();
});

test("export downloads the file; delete asks first", async () => {
  base();
  routes["GET /admin/students/1/export"] = () => ({
    raw: true, body: "{}", headers: { "Content-Disposition": 'attachment; filename="student-1.json"' } });
  routes["DELETE /admin/students/1"] = () => ({ body: { deleted: 1 } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<Records />);
  await screen.findByText("Aroha");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Export Aroha" }));
  });
  expect(saved).toEqual(["student-1.json"]);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Delete Aroha" }));
  });
  expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  expect(confirm.mock.calls[0][0]).toContain("all their messages (2 when this list loaded)");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Delete Aroha" }));
  });
  expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/admin/students/1");
  expect(await screen.findByText("Deleted Aroha.")).toBeInTheDocument();
});
