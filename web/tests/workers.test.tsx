import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Models from "@/components/Models";
import Workers, { POLL_MS, type Worker } from "@/components/Workers";

type Call = { path: string; method: string; body: unknown };

let calls: Call[];
let routes: Record<string, (call: Call) => { status?: number; body: unknown }>;

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

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
      if (!handler) return respond(404, { error: "not_found" });
      const out = handler(call);
      return respond(out.status ?? 200, out.body);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function click(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element);
  });
}

const up: Worker = {
  id: "6f1c3a9e-2b7d-4a52-9e4f-0d1b2c3e4f50",
  address: "http://10.0.0.5:8081",
  status: "up",
  models: ["gemma-4-e2b-it"],
  capacity: 4,
  in_flight: 1,
  last_heartbeat: "2026-10-10T09:00:00Z",
};
const join = { bash: "SERVER_URL=https://s JOIN_TOKEN=t docker compose", powershell: "$env:SERVER_URL='https://s'" };

test("shows the join command and each worker's status, models and load", async () => {
  routes["GET /workers"] = () => ({ body: { workers: [up] } });
  routes["GET /workers/join"] = () => ({ body: join });
  render(<Workers />);
  expect(await screen.findByDisplayValue(join.bash)).toBeInTheDocument();
  expect(screen.getByDisplayValue(join.powershell)).toBeInTheDocument();
  const row = (await screen.findByText(up.address)).closest("tr")!;
  expect(within(row).getByText("up")).toBeInTheDocument();
  expect(within(row).getByText("gemma-4-e2b-it")).toBeInTheDocument();
  expect(within(row).getByText("1 of 4")).toBeInTheDocument();
});

test("an open page shows a stopped worker as down on the next poll", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let status: Worker["status"] = "up";
  routes["GET /workers"] = () => ({ body: { workers: [{ ...up, status }] } });
  routes["GET /workers/join"] = () => ({ body: join });
  render(<Workers />);
  const row = (await screen.findByText(up.address)).closest("tr")!;
  expect(within(row).getByText("up")).toBeInTheDocument();
  status = "down";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS);
  });
  expect(within(screen.getByText(up.address).closest("tr")!).getByText("down")).toBeInTheDocument();
});

test("remove asks first, sends the rotate choice, and offers no remove for a removed worker", async () => {
  let workers = [up];
  routes["GET /workers"] = () => ({ body: { workers } });
  routes["GET /workers/join"] = () => ({ body: join });
  routes[`POST /workers/${up.id}/remove`] = () => {
    workers = [{ ...up, status: "removed" }];
    return { body: {} };
  };
  const confirm = vi.spyOn(window, "confirm");
  render(<Workers />);
  const button = await screen.findByRole("button", { name: `Remove ${up.address}` });

  confirm.mockReturnValueOnce(false);
  await click(button);
  expect(calls.some((c) => c.method === "POST")).toBe(false);

  confirm.mockReturnValueOnce(true).mockReturnValueOnce(false);
  await click(button);
  expect(calls.find((c) => c.method === "POST")).toEqual({
    path: `/workers/${up.id}/remove`,
    method: "POST",
    body: { rotate: false },
  });
  expect(await screen.findByText("removed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: `Remove ${up.address}` })).not.toBeInTheDocument();
});

test("rotate asks first and loads the new join command", async () => {
  let current = join;
  routes["GET /workers"] = () => ({ body: { workers: [] } });
  routes["GET /workers/join"] = () => ({ body: current });
  routes["POST /workers/rotate"] = () => {
    current = { bash: "NEW bash", powershell: "NEW ps" };
    return { body: {} };
  };
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<Workers />);
  expect(await screen.findByText("No workers have joined yet.")).toBeInTheDocument();
  const button = screen.getByRole("button", { name: "Rotate join token" });
  await click(button);
  expect(calls.some((c) => c.path === "/workers/rotate")).toBe(false);
  await click(button);
  expect(await screen.findByDisplayValue("NEW bash")).toBeInTheDocument();
  expect(confirm).toHaveBeenCalledTimes(2);
});

test("a failed load says so", async () => {
  routes["GET /workers/join"] = () => ({ body: join });
  routes["GET /workers"] = () => ({ status: 500, body: { error: "unavailable" } });
  render(<Workers />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the workers");
});

test("models toggle on and off", async () => {
  let models = [
    { name: "gemma-4-e2b-it", enabled: false, offered: true },
    { name: "old-model", enabled: true, offered: false },
  ];
  routes["GET /models"] = () => ({ body: { models } });
  routes["PUT /models/gemma-4-e2b-it"] = (call) => {
    models = [{ ...models[0], enabled: (call.body as { enabled: boolean }).enabled }, models[1]];
    return { body: {} };
  };
  render(<Models />);
  const box = await screen.findByRole("checkbox", { name: "gemma-4-e2b-it" });
  expect(box).not.toBeChecked();
  expect(screen.getByText(/no worker offers it right now/)).toBeInTheDocument();
  await click(box);
  expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ enabled: true });
  expect(await screen.findByRole("checkbox", { name: "gemma-4-e2b-it" })).toBeChecked();
});

test("a failed toggle says so", async () => {
  routes["GET /models"] = () => ({ body: { models: [{ name: "m", enabled: false, offered: true }] } });
  routes["PUT /models/m"] = () => ({ status: 403, body: { error: "forbidden" } });
  render(<Models />);
  await click(await screen.findByRole("checkbox", { name: "m" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not change m.");
});

test("a failed remove or rotate says so", async () => {
  routes["GET /workers"] = () => ({ body: { workers: [up] } });
  routes["GET /workers/join"] = () => ({ body: join });
  routes[`POST /workers/${up.id}/remove`] = () => ({ status: 500, body: { error: "unavailable" } });
  routes["POST /workers/rotate"] = () => ({ status: 403, body: { error: "forbidden" } });
  vi.spyOn(window, "confirm").mockReturnValueOnce(true).mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<Workers />);
  await click(await screen.findByRole("button", { name: `Remove ${up.address}` }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not remove the worker.");
  await click(screen.getByRole("button", { name: "Rotate join token" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not rotate the join token.");
});
