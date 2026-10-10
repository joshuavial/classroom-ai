// JSON calls to the Python app under /api. Same origin, so the session cookie
// goes with every request. State-changing requests also send the CSRF token
// from the csrf_token cookie in the X-CSRF-Token header. A browser with no
// token yet (first visit, or after sign out) gets one from GET /api/me first.

export const CSRF_COOKIE = "csrf_token";
export const CSRF_HEADER = "X-CSRF-Token";

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
  ) {
    super(`API request failed with status ${status}`);
  }
}

function readCookie(name: string): string | undefined {
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

async function csrfHeader(): Promise<Record<string, string>> {
  if (!readCookie(CSRF_COOKIE)) {
    await fetch("/api/me", { credentials: "same-origin", headers: { Accept: "application/json" } });
  }
  const token = readCookie(CSRF_COOKIE);
  return token ? { [CSRF_HEADER]: token } : {};
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && method !== "HEAD") Object.assign(headers, await csrfHeader());
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      if (response.ok) throw new ApiError(response.status, text);
      data = text;
    }
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return data as T;
}

// A download from the API: the body as a Blob, plus the filename the server
// suggested. Sends the CSRF header like api() for anything but GET.
export async function apiBlob(path: string, method = "GET"): Promise<{ blob: Blob; filename: string }> {
  const headers = method !== "GET" && method !== "HEAD" ? await csrfHeader() : {};
  const response = await fetch(`/api${path}`, { method, headers, credentials: "same-origin" });
  if (!response.ok) {
    const text = await response.text();
    let body: unknown = text;
    try {
      body = JSON.parse(text);
    } catch {
      // keep the text
    }
    throw new ApiError(response.status, body);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "download";
  return { blob: await response.blob(), filename };
}

// Hands a Blob to the browser as a file to save.
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
