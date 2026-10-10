import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import LoginPage from "@/app/login/page";
import { mockApi, stubLocation } from "./fetch";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

async function signIn() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Username"), "ms-smith");
  await user.type(screen.getByLabelText("Password"), "a long password");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

test.each([
  ["teacher", "/teach"],
  ["admin", "/admin"],
])("a %s goes to %s", async (role, path) => {
  document.cookie = "csrf_token=tok";
  mockApi({ "POST /login": { body: { staff: { username: "ms-smith", role } } } });
  const assign = stubLocation();
  render(<LoginPage />);
  await signIn();
  await waitFor(() => expect(assign).toHaveBeenCalledWith(path));
});

test("shows a message on 401 and stays on the page", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({ "POST /login": { status: 401, body: { error: "bad_login" } } });
  const assign = stubLocation();
  render(<LoginPage />);
  await signIn();
  expect(await screen.findByRole("alert")).toHaveTextContent("do not match");
  expect(assign).not.toHaveBeenCalled();
});

test("shows a wait message on 429", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({ "POST /login": { status: 429, body: { error: "too_many_attempts" } } });
  stubLocation();
  render(<LoginPage />);
  await signIn();
  expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
});
