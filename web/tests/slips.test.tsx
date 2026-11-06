import fs from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { Slips } from "@/app/teach/slips/slips";
import styles from "@/app/teach/slips/slips.module.css";
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

function parseCssRules(css: string): CSSRule[] {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const sheet = Array.from(document.styleSheets).find((s) => s.ownerNode === style);
  const rules = sheet ? Array.from(sheet.cssRules) : [];
  style.remove();
  return rules;
}

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

test("print stylesheet configures 3-across A4 layout, typography, and page breaks", () => {
  const cssPath = path.resolve(__dirname, "../app/teach/slips/slips.module.css");
  const css = fs.readFileSync(cssPath, "utf-8");

  // Verify @page specifies A4 size and margins
  expect(css).toMatch(/@page\s*\{[^}]*size:\s*A4/);
  expect(css).toMatch(/@page\s*\{[^}]*margin:\s*10mm/);

  // Parse CSSOM rules
  const rules = parseCssRules(css);

  // Verify .grid specifies 3-across layout
  const gridRule = rules.find((r): r is CSSStyleRule => "selectorText" in r && r.selectorText === ".grid");
  expect(gridRule).toBeDefined();
  expect(gridRule!.style.display).toBe("grid");
  expect(gridRule!.style.gridTemplateColumns).toBe("repeat(3, 1fr)");

  // Verify .slip avoids page breaks and defines card dimensions
  const slipRule = rules.find((r): r is CSSStyleRule => "selectorText" in r && r.selectorText === ".slip");
  expect(slipRule).toBeDefined();
  expect(slipRule!.style.height).toBe("32mm");
  expect(slipRule!.style.padding).toBe("5mm");
  expect(slipRule!.style.getPropertyValue("page-break-inside")).toBe("avoid");
  expect(slipRule!.style.getPropertyValue("break-inside")).toBe("avoid");

  // Verify typography on .sheet, .code, and .small
  const sheetRule = rules.find((r): r is CSSStyleRule => "selectorText" in r && r.selectorText === ".sheet");
  expect(sheetRule).toBeDefined();
  expect(sheetRule!.style.fontFamily).toContain("system-ui");

  const codeRule = rules.find((r): r is CSSStyleRule => "selectorText" in r && r.selectorText === ".code");
  expect(codeRule).toBeDefined();
  expect(codeRule!.style.fontSize).toBe("22pt");
  expect(codeRule!.style.fontWeight).toBe("700");
  expect(codeRule!.style.letterSpacing).toBe("0.1em");
  expect(codeRule!.style.fontVariantNumeric).toBe("tabular-nums");

  const smallRule = rules.find((r): r is CSSStyleRule => "selectorText" in r && r.selectorText === ".small");
  expect(smallRule).toBeDefined();
  expect(smallRule!.style.fontSize).toBe("9pt");
});

test("@media print suppresses controls and expands sheet margins", () => {
  const cssPath = path.resolve(__dirname, "../app/teach/slips/slips.module.css");
  const css = fs.readFileSync(cssPath, "utf-8");

  // Parse CSSOM rules
  const rules = parseCssRules(css);

  const printMediaRule = rules.find((r): r is CSSMediaRule => "media" in r && (r as CSSMediaRule).media.mediaText === "print");
  expect(printMediaRule).toBeDefined();

  const mediaRules = Array.from(printMediaRule!.cssRules) as CSSStyleRule[];
  const controlsRule = mediaRules.find((r) => r.selectorText === ".controls");
  expect(controlsRule).toBeDefined();
  expect(controlsRule!.style.display).toBe("none");

  const sheetRule = mediaRules.find((r) => r.selectorText === ".sheet");
  expect(sheetRule).toBeDefined();
  expect(["0", "0px"]).toContain(sheetRule!.style.margin);
  expect(sheetRule!.style.maxWidth).toBe("none");

  // Verify DOM structure: navigation and action controls are enclosed in .controls
  const { container } = render(<Slips session={session} address="https://classroom.local/" />);
  const controls = container.querySelector<HTMLElement>(".controls, [class*='controls']");
  expect(controls).not.toBeNull();

  // Print button and instructions are inside the suppressed controls container
  const printButton = screen.getByRole("button", { name: "Print" });
  expect(controls).toContainElement(printButton);
  expect(controls).toHaveTextContent("Cut along the dashed lines.");

  // Slips are in the grid container outside the suppressed controls
  const grid = container.querySelector<HTMLElement>(".grid, [class*='grid']");
  expect(grid).not.toBeNull();
  expect(controls).not.toContainElement(grid);
  const slips = screen.getAllByTestId("slip");
  expect(slips.length).toBeGreaterThan(0);
  for (const slip of slips) {
    expect(grid).toContainElement(slip);
    expect(controls).not.toContainElement(slip);
  }
});

test("rendered component binds stylesheet classes and arranges 30 slips in grid", () => {
  // Lesson session with 30 unused codes (standard full-class batch from docs/manual-tests.md item 1)
  const session30: LessonSession = {
    id: 8,
    state: "open",
    class_id: 1,
    class_name: "Year 9 Science",
    opened_at: "2026-10-10T00:00:00Z",
    closed_at: null,
    roster: Array.from({ length: 30 }, (_, i) => ({
      id: i + 1,
      code: String(100000 + i),
      name: null,
      bound_at: null,
    })),
  };

  const { container } = render(<Slips session={session30} address="https://classroom.local/" />);

  // 1. Verify all 30 slips are rendered
  const slips = screen.getAllByTestId("slip");
  expect(slips).toHaveLength(30);

  // 2. Verify component markup directly applies imported CSS module classes
  const sheet = container.querySelector("main")!;
  expect(sheet.className).toContain(styles.sheet);

  const controls = container.querySelector(`.${styles.controls}`)!;
  expect(controls).not.toBeNull();

  const grid = container.querySelector(`.${styles.grid}`)!;
  expect(grid).not.toBeNull();

  for (const slip of slips) {
    expect(slip.className).toContain(styles.slip);
    const code = slip.querySelector(`.${styles.code}`)!;
    expect(code).not.toBeNull();
    const smalls = slip.querySelectorAll(`.${styles.small}`);
    expect(smalls.length).toBe(2);
  }
});
