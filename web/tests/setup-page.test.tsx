import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import SetupPage from "@/app/setup/page";
import { mockApi, stubLocation } from "./fetch";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

async function fill() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Setup code"), "ABCD-EFGH-JKLM");
  await user.type(screen.getByLabelText("Admin username"), "admin");
  await user.type(screen.getByLabelText("Password"), "a long password");
  await user.click(screen.getByRole("button", { name: "Create admin account" }));
}

test("submits the code and account, then goes to /admin", async () => {
  document.cookie = "csrf_token=tok";
  const fetch = mockApi({ "GET /setup": { body: { needed: true } }, "POST /setup": { body: { staff: {} } } });
  const assign = stubLocation();
  render(<SetupPage />);
  await fill();
  await waitFor(() => expect(assign).toHaveBeenCalledWith("/admin"));
  const post = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(post[1]!.body as string)).toEqual({
    code: "ABCD-EFGH-JKLM",
    username: "admin",
    password: "a long password",
  });
  expect((post[1]!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
});

test("shows the wrong-code message on 403", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({ "GET /setup": { body: { needed: true } }, "POST /setup": { status: 403, body: { error: "wrong_code" } } });
  const assign = stubLocation();
  render(<SetupPage />);
  await fill();
  expect(await screen.findByRole("alert")).toHaveTextContent("setup code is not right");
  expect(assign).not.toHaveBeenCalled();
});

test("says setup is done when it is not needed", async () => {
  mockApi({ "GET /setup": { body: { needed: false } } });
  render(<SetupPage />);
  expect(await screen.findByRole("heading", { name: "Setup is done" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
});

test("shows the server-down view when the first check fails", async () => {
  mockApi({ "GET /setup": { status: 503, body: { error: "unavailable" } } });
  render(<SetupPage />);
  expect(await screen.findByRole("heading", { name: "The server is not answering" })).toBeInTheDocument();
});
