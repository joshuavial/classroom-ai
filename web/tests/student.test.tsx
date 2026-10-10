import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import StudentPage from "@/app/page";
import { StudentHeader } from "@/app/student-header";
import { mockApi } from "./fetch";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

const aroha = { name: "Aroha", code: "123456", class_name: "Year 9", state: "open", message_limit: null };

test("the header shows the student's name and code", () => {
  render(<StudentHeader student={{ ...aroha, state: "open" }} />);
  const header = screen.getByRole("banner");
  expect(header).toHaveTextContent("Aroha");
  expect(header).toHaveTextContent("123456");
});

test("a signed-in student sees the header and the monitoring notice", async () => {
  mockApi({ "GET /student/me": { body: { student: aroha } } });
  render(<StudentPage />);
  expect(await screen.findByRole("banner")).toHaveTextContent("Aroha");
  expect(screen.getByText("Your teacher can read everything you write here.")).toBeInTheDocument();
  expect(screen.queryByText(/paused/)).not.toBeInTheDocument();
});

test("a paused class says so", async () => {
  mockApi({ "GET /student/me": { body: { student: { ...aroha, state: "paused" } } } });
  render(<StudentPage />);
  expect(await screen.findByRole("status")).toHaveTextContent("paused the class");
});

test("joining with a code shows the header", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({ "GET /student/me": { body: { student: null } }, "POST /join": { body: { student: aroha } } });
  render(<StudentPage />);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Code from your slip"), "123456");
  await user.type(screen.getByLabelText("Your name"), "Aroha");
  await user.click(screen.getByRole("button", { name: "Join" }));
  expect(await screen.findByRole("banner")).toHaveTextContent("123456");
});

test("a wrong code says to check the slip", async () => {
  document.cookie = "csrf_token=tok";
  mockApi({
    "GET /student/me": { body: { student: null } },
    "POST /join": { status: 404, body: { error: "bad_code" } },
  });
  render(<StudentPage />);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Code from your slip"), "999999");
  await user.type(screen.getByLabelText("Your name"), "Aroha");
  await user.click(screen.getByRole("button", { name: "Join" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Check your slip");
});
