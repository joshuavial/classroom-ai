import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import RootCertificate from "@/components/RootCertificate";
import { stubLocation } from "./fetch";

let saved: string[];

beforeEach(() => {
  saved = [];
  Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => {} });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("downloads Caddy's root certificate through the public API route", async () => {
  const fetch = vi.fn(async () => new Response("CERTIFICATE", {
    headers: { "Content-Disposition": 'attachment; filename="classroom-ai-root.crt"' },
  }));
  vi.stubGlobal("fetch", fetch);
  render(<RootCertificate />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Download root certificate" }));
  });
  expect(fetch).toHaveBeenCalledWith("/api/admin/root-certificate", {
    method: "GET", headers: {}, credentials: "same-origin",
  });
  expect(saved).toEqual(["classroom-ai-root.crt"]);
  expect(screen.getByText("Root certificate downloaded.")).toBeInTheDocument();
});

test("shows a failure message when the certificate is unavailable", async () => {
  const assign = stubLocation();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("not found", { status: 404 })));
  render(<RootCertificate />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Download root certificate" }));
  });
  expect(screen.getByText(/Could not download the root certificate/)).toBeInTheDocument();
  expect(assign).not.toHaveBeenCalled();
  expect(saved).toEqual([]);
});

test("redirects to /login when the session has expired with 401", async () => {
  const assign = stubLocation();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "not_signed_in" }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  })));
  render(<RootCertificate />);
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Download root certificate" }));
  });
  expect(assign).toHaveBeenCalledWith("/login");
  expect(saved).toEqual([]);
});
