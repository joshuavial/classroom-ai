import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import AdminPage from "@/app/admin/page";
import TeachPage from "@/app/teach/page";
import { mockApi, stubLocation } from "./fetch";

afterEach(() => vi.unstubAllGlobals());

test("anonymous visitor to /admin goes to /setup while setup is needed", async () => {
  mockApi({ "GET /me": { body: { staff: null } }, "GET /setup": { body: { needed: true } } });
  const assign = stubLocation();
  render(<AdminPage />);
  await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/setup"));
});

test("anonymous visitor to /teach goes to /login once setup is done", async () => {
  mockApi({ "GET /me": { body: { staff: null } }, "GET /setup": { body: { needed: false } } });
  const assign = stubLocation();
  render(<TeachPage />);
  await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
});

test("a teacher on /admin is told they need an admin account", async () => {
  mockApi({ "GET /me": { body: { staff: { username: "t", role: "teacher" } } } });
  stubLocation();
  render(<AdminPage />);
  expect(await screen.findByText("You need an admin account to see this page.")).toBeInTheDocument();
});

test("an admin sees the staff list on /admin", async () => {
  mockApi({
    "GET /me": { body: { staff: { username: "boss", role: "admin" } } },
    "GET /staff": { body: { staff: [{ username: "boss", role: "admin", created_at: "" }] } },
  });
  render(<AdminPage />);
  expect(await screen.findByRole("cell", { name: "boss" })).toBeInTheDocument();
});

test("an admin can open /teach", async () => {
  mockApi({ "GET /me": { body: { staff: { username: "boss", role: "admin" } } } });
  render(<TeachPage />);
  expect(await screen.findByRole("heading", { name: "Teacher" })).toBeInTheDocument();
});

test("a failing /api/me shows that the server is not answering", async () => {
  mockApi({ "GET /me": { status: 503, body: { error: "unavailable" } } });
  render(<TeachPage />);
  expect(await screen.findByRole("heading", { name: "The server is not answering" })).toBeInTheDocument();
});

test("a 401 while loading the staff list sends the admin back to /login", async () => {
  mockApi({
    "GET /me": { body: { staff: { username: "boss", role: "admin" } } },
    "GET /staff": { status: 401, body: { error: "not_signed_in" } },
  });
  const assign = stubLocation();
  render(<AdminPage />);
  await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
});
