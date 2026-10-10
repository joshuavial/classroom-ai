import { vi } from "vitest";

type Reply = { status?: number; body?: unknown };

/** Mock fetch with replies keyed by "METHOD /path". Returns the mock. */
export function mockApi(replies: Record<string, Reply | Reply[]>) {
  const queues = Object.fromEntries(
    Object.entries(replies).map(([k, v]) => [k, Array.isArray(v) ? [...v] : [v]]),
  );
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${(init?.method ?? "GET").toUpperCase()} ${url.replace(/^\/api/, "")}`;
    const queue = queues[key];
    if (!queue) throw new Error(`unexpected request ${key}`);
    const reply = queue.length > 1 ? queue.shift()! : queue[0];
    return new Response(reply.body === undefined ? "" : JSON.stringify(reply.body), {
      status: reply.status ?? 200,
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export function stubLocation() {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  return assign;
}
