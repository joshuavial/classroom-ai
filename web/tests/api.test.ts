import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError } from "@/lib/api";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

test("POST sends the CSRF header from the cookie and a JSON body", async () => {
  document.cookie = "csrf_token=abc123";
  const fetch = mockFetch(200, { ok: true });
  await expect(api("/thing", { method: "POST", body: { a: 1 } })).resolves.toEqual({ ok: true });
  const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/thing");
  const headers = init.headers as Record<string, string>;
  expect(headers["X-CSRF-Token"]).toBe("abc123");
  expect(init.body).toBe('{"a":1}');
  expect(init.credentials).toBe("same-origin");
});

test("GET sends no CSRF header", async () => {
  document.cookie = "csrf_token=abc123";
  const fetch = mockFetch(200, []);
  await api("/thing");
  const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
});

test("a non-2xx reply throws ApiError with status and body", async () => {
  mockFetch(403, { error: "forbidden" });
  const error = (await api("/thing").catch((e: unknown) => e)) as ApiError;
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(403);
  expect(error.body).toEqual({ error: "forbidden" });
});

test("a non-JSON error body still throws ApiError with the status", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad Gateway", { status: 502 })));
  const error = (await api("/thing").catch((e: unknown) => e)) as ApiError;
  expect(error).toBeInstanceOf(ApiError);
  expect(error.status).toBe(502);
  expect(error.body).toBe("Bad Gateway");
});
