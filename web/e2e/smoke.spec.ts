import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";

const MODEL = "gemma-4-e2b-it";

// Set up, join a worker with the admin page's own join command, turn its
// model on, then a student joins and chats while the teacher watches.
test("a student joins and chats, and the teacher sees it live", async ({ browser }) => {
  const code = process.env.SETUP_CODE;
  test.skip(!code, "run through e2e/run.sh, which passes SETUP_CODE");

  const teacher = await (await browser.newContext()).newPage();
  await teacher.goto("/admin");
  await expect(teacher).toHaveURL(/\/setup$/);
  await teacher.getByLabel("Setup code").fill(code!);
  await teacher.getByLabel("Admin username").fill("admin");
  await teacher.getByLabel("Password").fill("a long admin password");
  await teacher.getByRole("button", { name: "Create admin account" }).click();
  await expect(teacher).toHaveURL(/\/admin$/);

  // The join command the admin page shows, run as a tech teacher would.
  const join = await (await teacher.request.get("/api/workers/join")).json();
  execSync(join.bash, {
    cwd: process.env.REPO_ROOT,
    shell: "/bin/sh",
    env: { ...process.env, COMPOSE_PROJECT_NAME: "cai-e2e-worker", BACKEND_URL: process.env.BACKEND_URL },
    stdio: "inherit",
  });
  await expect
    .poll(async () => (await (await teacher.request.get("/api/models")).json()).models?.map((m: { name: string }) => m.name),
      { timeout: 60_000 })
    .toContain(MODEL);
  const csrf = (await teacher.context().cookies()).find((c) => c.name === "csrf_token")!.value;
  const on = await teacher.request.put(`/api/models/${MODEL}`, {
    data: { enabled: true },
    headers: { "X-CSRF-Token": csrf },
  });
  expect(on.ok()).toBeTruthy();

  await teacher.goto("/teach");
  const newClass = teacher.getByRole("region", { name: "New class" });
  await newClass.getByLabel("Class name").fill("Year 9 Science");
  await newClass.getByRole("button", { name: "Create class" }).click();
  await teacher.getByRole("button", { name: "Start lesson" }).click();
  await expect(teacher.getByText("No students have joined yet.")).toBeVisible();
  const slips = await teacher.locator("tbody tr td:first-child").allInnerTexts();
  const [slip, secondSlip] = slips.map((value) => value.trim());

  const student = await (await browser.newContext()).newPage();
  await student.goto("/");
  await student.getByLabel("Code from your slip").fill(slip);
  await student.getByLabel("Your name").fill("Aroha");
  await student.getByRole("button", { name: "Join" }).click();
  await expect(student.getByRole("banner")).toContainText(slip);
  await expect(student.getByText("Your teacher can read everything you write here.")).toBeVisible();

  const secondStudent = await (await browser.newContext()).newPage();
  await secondStudent.goto("/");
  await secondStudent.getByLabel("Code from your slip").fill(secondSlip);
  await secondStudent.getByLabel("Your name").fill("Ben");
  await secondStudent.getByRole("button", { name: "Join" }).click();
  await expect(secondStudent.getByRole("banner")).toContainText(secondSlip);

  const cards = teacher.getByRole("list", { name: "Students" });
  await expect(cards.getByRole("button", { name: /Aroha/ })).toBeVisible({ timeout: 5_000 });
  await expect(cards.getByRole("button", { name: /Ben/ })).toBeVisible({ timeout: 5_000 });

  await expect(student.getByRole("option", { name: MODEL })).toBeAttached({ timeout: 20_000 });
  await student.getByRole("button", { name: "New conversation" }).click();
  await student.getByLabel("Your message").fill("What is a cell?");
  await student.getByLabel("Your message").press("Enter");
  await expect(student.getByRole("log", { name: "Conversation" })).toContainText("Cells are the units of life.", {
    timeout: 20_000,
  });

  // Acceptance 3: the teacher sees it within five seconds, without a reload.
  await expect(cards.getByRole("button", { name: /Aroha/ })).toContainText("Cells are the units of life.", {
    timeout: 5_000,
  });
  // Deliver a real, stored server reply to the browser one SSE event at a time.
  // This makes navigation during streaming deterministic without a slow model.
  await student.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await realFetch(...args);
      if (!String(args[0]).endsWith("/messages")) return response;
      const chunks = (await response.text()).split("\n\n").filter(Boolean);
      const encoder = new TextEncoder();
      const stream = new ReadableStream({
        start(controller) {
          (window as unknown as { nextChatEvent: () => void }).nextChatEvent = () => {
            const chunk = chunks.shift();
            if (chunk) controller.enqueue(encoder.encode(chunk + "\n\n"));
            if (!chunks.length) controller.close();
          };
        },
      });
      return new Response(stream, { headers: response.headers, status: response.status });
    };
  });
  await student.getByLabel("Your message").fill("Tell me more about cells");
  await student.getByLabel("Your message").press("Enter");
  await student.waitForFunction(() => typeof (window as unknown as { nextChatEvent?: unknown }).nextChatEvent === "function");
  const nextEvent = () => student.evaluate(() => (window as unknown as { nextChatEvent: () => void }).nextChatEvent());
  await nextEvent();
  const transcript = student.getByRole("log", { name: "Conversation" });
  await expect(transcript).toContainText("Tell me more about cells");
  await student.getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(transcript).not.toContainText("Cells");
  await nextEvent();
  await expect(transcript).not.toContainText("Cells");
  await student.getByRole("button", { name: /What is a cell/ }).click();
  await expect(transcript).toContainText("Tell me more about cells");
  await expect(transcript.locator("p").last()).toContainText("Cells are");
  // Four more deltas and the done event finish the six-piece fake reply.
  for (let i = 0; i < 5; i++) await nextEvent();
  await expect(student.getByLabel("Your message")).toBeEnabled();
  await expect(transcript.locator("p").last()).toHaveText("AI: Cells are the units of life.");
  // Reload from storage to prove navigation did not alter the persisted reply.
  await student.reload();
  await student.getByRole("button", { name: /What is a cell/ }).click();
  await expect(student.getByRole("log").locator("p").last()).toHaveText("AI: Cells are the units of life.");
  if (process.env.E2E_SCREENSHOT) await teacher.screenshot({ path: process.env.E2E_SCREENSHOT, fullPage: true });
});
