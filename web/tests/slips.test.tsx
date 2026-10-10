import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { Slips } from "@/app/teach/slips/slips";
import type { LessonSession } from "@/lib/types";

const session: LessonSession = {
  id: 7,
  state: "open",
  class_id: 1,
  class_name: "Year 9 Science",
  opened_at: "2026-10-10T00:00:00Z",
  closed_at: null,
  roster: [
    { id: 1, code: "123456", name: null, bound_at: null },
    { id: 2, code: "654321", name: "Aroha", bound_at: "2026-10-10T00:01:00Z" },
    { id: 3, code: "000042", name: null, bound_at: null },
  ],
};

test("one slip per unused code with the class name and the chat address", () => {
  render(<Slips session={session} address="https://classroom.local/" />);
  const slips = screen.getAllByTestId("slip");
  expect(slips).toHaveLength(2);
  expect(slips[0]).toHaveTextContent("123456");
  expect(slips[1]).toHaveTextContent("000042");
  for (const slip of slips) {
    expect(slip).toHaveTextContent("Year 9 Science");
    expect(slip).toHaveTextContent("https://classroom.local/");
  }
  expect(screen.queryByText("654321")).not.toBeInTheDocument();
});

test("the print button prints", () => {
  const print = vi.fn();
  vi.stubGlobal("print", print);
  render(<Slips session={session} address="x" />);
  screen.getByRole("button", { name: "Print" }).click();
  expect(print).toHaveBeenCalled();
  vi.unstubAllGlobals();
});
