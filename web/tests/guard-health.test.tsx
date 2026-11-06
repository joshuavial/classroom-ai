import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import GuardHealth from "@/components/GuardHealth";
import TeachPage from "@/app/teach/page";
import { mockApi } from "./fetch";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

test("warns when the guard is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ status: "down", db: "ok", guard: "down" }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  )));
  render(<GuardHealth />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Student chat is blocked");
});

test("stays quiet when the guard is healthy", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ status: "ok", db: "ok", guard: "ok" }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )));
  render(<GuardHealth />);
  await vi.waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
    "/healthz",
    { credentials: "same-origin", headers: { Accept: "application/json" } },
  ));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("clears an outage warning after the guard recovers", async () => {
  vi.useFakeTimers();
  let calls = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    calls += 1;
    const guard = calls === 1 ? "down" : "ok";
    return new Response(JSON.stringify({ status: guard, db: "ok", guard }), {
      status: guard === "ok" ? 200 : 503,
      headers: { "Content-Type": "application/json" },
    });
  }));
  render(<GuardHealth />);
  await act(async () => Promise.resolve());
  expect(screen.getByRole("alert")).toBeInTheDocument();

  await act(async () => {
    vi.advanceTimersByTime(15_000);
    await Promise.resolve();
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("teacher console mounts GuardHealth alert banner when guard is down", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({
    "GET /models": { body: { models: [] } },
    "GET /me": { body: { staff: { username: "teacher1", role: "teacher" } } },
    "GET /classes": { body: { classes: [] } },
    "GET /healthz": { status: 503, body: { status: "down", db: "ok", guard: "down" } },
  });
  render(<TeachPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Student chat is blocked");
});
