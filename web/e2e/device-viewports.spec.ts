import { expect, test } from "@playwright/test";
import type { LessonSession, StudentCard, Transcript, Usage } from "../lib/types";

test.describe("Mobile viewport (390x844)", () => {
  test.use({
    viewport: { width: 390, height: 844 },
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
  });

  test("student chat flow, sticky header, monitoring banner, and responsive constraints", async ({ page }) => {
    await page.context().addCookies([
      { name: "csrf_token", value: "dummy_token", url: "http://localhost:8080" },
      { name: "csrf_token", value: "dummy_token", domain: "127.0.0.1", path: "/" },
      { name: "csrf_token", value: "dummy_token", domain: "localhost", path: "/" },
    ]);

    await page.route("**/api/me", async (route) => {
      await route.fulfill({
        status: 200,
        headers: { "Set-Cookie": "csrf_token=dummy_token; Path=/" },
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.route("**/api/student/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          student: {
            name: "Aroha",
            code: "123456",
            class_name: "Year 9 Science",
            state: "open",
            message_limit: 10,
            messages_used: 1,
          },
        }),
      });
    });

    await page.route("**/api/student/models", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ models: ["gemma-4-e2b-it"] }),
      });
    });

    await page.route("**/api/student/conversations", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ conversations: [] }),
      });
    });

    await page.goto("/");

    // 1. Monitoring banner & Student header visibility (R4.2 & R4.7)
    const header = page.getByRole("banner");
    await expect(header).toBeVisible();
    await expect(header).toContainText("Aroha");
    await expect(header).toContainText("123456");
    await expect(header).toContainText("Year 9 Science");

    // Header must fit within the 390px mobile viewport without horizontal overflow
    const headerBox = await header.boundingBox();
    expect(headerBox).not.toBeNull();
    expect(headerBox!.x).toBeGreaterThanOrEqual(0);
    expect(headerBox!.x + headerBox!.width).toBeLessThanOrEqual(390.5);

    // Verify sticky header styles pin to top of viewport
    const headerPos = await header.evaluate((el) => window.getComputedStyle(el).position);
    expect(headerPos).toBe("sticky");
    const headerTop = await header.evaluate((el) => window.getComputedStyle(el).top);
    expect(headerTop).toBe("0px");

    const monitorNotice = page.getByText("Your teacher can read everything you write here.");
    await expect(monitorNotice).toBeVisible();

    // 2. Mobile layout reflow: no horizontal page scrollbar
    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(hasHorizontalOverflow).toBeFalsy();

    // 3. Model selector & New conversation button
    await expect(page.getByRole("combobox", { name: "Model" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New conversation" })).toBeVisible();
  });

  test("conversation creation, SSE streaming reply render, and conversation list persistence", async ({
    page,
  }) => {
    let nextConvoId = 100;
    const conversations: Array<{ id: number; model: string; title?: string; started_at: string }> = [];
    const messagesByConvo: Record<
      number,
      Array<{ id: number; role: "user" | "assistant"; text: string; status: "ok"; at: string }>
    > = {};

    await page.context().addCookies([
      { name: "csrf_token", value: "dummy_token", url: "http://localhost:8080" },
      { name: "csrf_token", value: "dummy_token", domain: "127.0.0.1", path: "/" },
      { name: "csrf_token", value: "dummy_token", domain: "localhost", path: "/" },
    ]);

    await page.route("**/api/me", async (route) => {
      await route.fulfill({
        status: 200,
        headers: { "Set-Cookie": "csrf_token=dummy_token; Path=/" },
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    });

    await page.route("**/api/student/me", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          student: {
            name: "Aroha",
            code: "123456",
            class_name: "Year 9 Science",
            state: "open",
            message_limit: 10,
            messages_used: 1,
          },
        }),
      });
    });

    await page.route("**/api/student/models", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ models: ["gemma-4-e2b-it"] }),
      });
    });

    await page.route(/\/api\/student\/conversations$/, async (route) => {
      if (route.request().method() === "POST") {
        const id = ++nextConvoId;
        const newConvo = { id, model: "gemma-4-e2b-it", started_at: new Date().toISOString() };
        conversations.unshift(newConvo);
        messagesByConvo[id] = [];
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(newConvo),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ conversations }),
      });
    });

    await page.route(/\/api\/student\/conversations\/\d+\/messages$/, async (route) => {
      const url = route.request().url();
      const match = url.match(/\/conversations\/(\d+)\/messages/);
      const convoId = match ? Number(match[1]) : 101;
      const postData = route.request().postDataJSON() as { text?: string } | null;
      const promptText = postData?.text ?? "What is a cell?";
      const convo = conversations.find((c) => c.id === convoId);
      if (convo && !convo.title) {
        convo.title = promptText;
      }
      messagesByConvo[convoId]?.push({
        id: Date.now(),
        role: "user",
        text: promptText,
        status: "ok",
        at: new Date().toISOString(),
      });
      const replyText =
        promptText.includes("cell")
          ? "Cells are the basic structural and functional units of all living organisms."
          : "DNA holds the genetic instructions for living things.";
      messagesByConvo[convoId]?.push({
        id: Date.now() + 1,
        role: "assistant",
        text: replyText,
        status: "ok",
        at: new Date().toISOString(),
      });

      // SSE streaming chunks
      const sseBody = [
        `event: delta\ndata: ${JSON.stringify({ text: replyText.slice(0, 20) })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: replyText.slice(20) })}\n\n`,
        `event: done\ndata: {}\n\n`,
      ].join("");

      await route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: sseBody,
      });
    });

    await page.route(/\/api\/student\/conversations\/\d+$/, async (route) => {
      const url = route.request().url();
      const match = url.match(/\/conversations\/(\d+)$/);
      const convoId = match ? Number(match[1]) : 101;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ messages: messagesByConvo[convoId] ?? [] }),
      });
    });

    await page.goto("/");

    // Start first conversation
    await page.getByRole("button", { name: "New conversation" }).click();
    const messageInput = page.getByLabel("Your message");
    await expect(messageInput).toBeVisible();
    await expect(messageInput).toBeEnabled();

    // Send prompt and verify SSE streaming reply
    await messageInput.fill("What is a cell?");
    await messageInput.press("Enter");

    const transcript = page.getByRole("log", { name: "Conversation" });
    await expect(transcript).toContainText("What is a cell?");
    await expect(transcript).toContainText("AI: Cells are the basic structural and functional units");
    await expect(messageInput).toBeEnabled();

    // Verify conversation list displays the conversation button
    const firstConvoButton = page.getByRole("button", { name: /What is a cell/ });
    await expect(firstConvoButton).toBeVisible();

    // Start second conversation
    await page.getByRole("button", { name: "New conversation" }).click();
    await expect(transcript).toHaveText("");
    await messageInput.fill("What is DNA?");
    await messageInput.press("Enter");

    await expect(transcript).toContainText("What is DNA?");
    await expect(transcript).toContainText("AI: DNA holds the genetic instructions");

    // Both conversations are visible in the conversation list
    const secondConvoButton = page.getByRole("button", { name: /What is DNA/ });
    await expect(firstConvoButton).toBeVisible();
    await expect(secondConvoButton).toBeVisible();

    // Switch back to first conversation
    await firstConvoButton.click();
    await expect(transcript).toContainText("What is a cell?");
    await expect(transcript).toContainText("AI: Cells are the basic structural and functional units");

    // Verify responsive constraints with active transcript rendered
    const overflowWithMessages = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(overflowWithMessages).toBeFalsy();

    // Persistence on page reload
    await page.reload();
    await expect(firstConvoButton).toBeVisible();
    await expect(secondConvoButton).toBeVisible();
    await firstConvoButton.click();
    await expect(page.getByRole("log", { name: "Conversation" })).toContainText("What is a cell?");
  });
});

test.describe("iPad Safari viewport (820x1180)", () => {
  test.use({
    viewport: { width: 820, height: 1180 },
    userAgent:
      "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
  });

  const teacherStaff = { staff: { username: "teacher1", role: "teacher" } };
  const sessionInfo: LessonSession = {
    id: 7,
    class_id: 1,
    class_name: "Year 9 Science",
    state: "open",
    opened_at: new Date(Date.now() - 300000).toISOString(),
    closed_at: null,
    roster: [
      { id: 1, code: "100001", name: "Aroha", bound_at: null },
      { id: 2, code: "100002", name: "Ben", bound_at: null },
    ],
  };

  const studentCards: StudentCard[] = [
    {
      id: 1,
      name: "Aroha",
      code: "100001",
      messages: 2,
      latest: { role: "assistant", text: "Cells are units of life.", status: "ok", at: new Date().toISOString() },
      unreviewed_flags: 1,
      last_active: new Date().toISOString(),
    },
    {
      id: 2,
      name: "Ben",
      code: "100002",
      messages: 1,
      latest: { role: "user", text: "What is an atom?", status: "ok", at: new Date().toISOString() },
      unreviewed_flags: 0,
      last_active: new Date().toISOString(),
    },
  ];

  const usageStats: Usage = {
    active_students: 2,
    total_messages: 3,
    per_student: [
      { id: 1, name: "Aroha", messages: 2 },
      { id: 2, name: "Ben", messages: 1 },
    ],
    models: [{ model: "gemma-4-e2b-it", conversations: 2 }],
    flags_by_category: [{ category: "Violent", flags: 1 }],
  };

  const arohaTranscript: Transcript = {
    student: { id: 1, name: "Aroha", code: "100001", removed: false },
    conversations: [
      {
        id: 1,
        model: "gemma-4-e2b-it",
        started_at: "",
        messages: [
          { id: 1, role: "user", text: "Tell me about biology", status: "ok", at: "", flags: [] },
          { id: 2, role: "assistant", text: "Cells are units of life.", status: "ok", at: "", flags: [] },
          {
            id: 3,
            role: "user",
            text: "Unsafe search term",
            status: "blocked",
            at: "",
            flags: [
              {
                id: 10,
                category: "Violent",
                action: "block_flag",
                at: "",
                reviewed_by: null,
                reviewed_at: null,
              },
            ],
          },
        ],
      },
    ],
  };

  const benTranscript: Transcript = {
    student: { id: 2, name: "Ben", code: "100002", removed: false },
    conversations: [
      {
        id: 2,
        model: "gemma-4-e2b-it",
        started_at: "",
        messages: [
          { id: 4, role: "user", text: "What is an atom?", status: "ok", at: "", flags: [] },
          {
            id: 5,
            role: "assistant",
            text: "An atom is the basic unit of a chemical element.",
            status: "ok",
            at: "",
            flags: [],
          },
        ],
      },
    ],
  };

  test("teacher console responsive session bar and GuardHealth outage banner", async ({ page }) => {
    let guardDown = true;

    await page.route("**/api/me", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(teacherStaff) });
    });

    await page.route("**/api/classes", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          classes: [
            {
              id: 1,
              name: "Year 9 Science",
              instructions: "Tutor format",
              message_limit: 10,
              teacher: "teacher1",
              live_session: sessionInfo,
            },
          ],
        }),
      });
    });

    await page.route("**/api/sessions/7", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sessionInfo) });
    });

    await page.route("**/api/sessions/7/events", async (route) => {
      await route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body: "event: hello\ndata: {}\n\n",
      });
    });

    await page.route("**/api/sessions/7/students", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ students: studentCards }) });
    });

    await page.route("**/api/sessions/7/usage", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(usageStats) });
    });

    await page.route("**/api/models", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ models: [{ name: "gemma-4-e2b-it", enabled: true }] }),
      });
    });

    await page.route("**/healthz", async (route) => {
      if (guardDown) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ status: "down", db: "ok", guard: "down" }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "ok", db: "ok", guard: "ok" }),
        });
      }
    });

    await page.goto("/teach");

    // 1. GuardHealth outage banner visibility when guard is down
    const outageBanner = page.getByRole("alert").filter({ hasText: "The guard is unavailable" });
    await expect(outageBanner).toBeVisible();
    await expect(outageBanner).toContainText("Student chat is blocked until the guard is healthy.");

    // 2. Session bar controls fit without horizontal overflow on iPad portrait (820px)
    await expect(page.getByRole("heading", { level: 1, name: "Teacher" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Lesson for Year 9 Science" }).getByRole("heading", { level: 2, name: "Year 9 Science" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Close lesson" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Print slips" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate" })).toBeVisible();

    const noHorizontalScroll = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    );
    expect(noHorizontalScroll).toBeTruthy();
  });

  test("card grid reflow, slide-out transcript on card click, and rotation to landscape (1180x820)", async ({
    page,
  }) => {
    await page.route("**/api/me", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(teacherStaff) });
    });

    await page.route("**/api/classes", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          classes: [
            {
              id: 1,
              name: "Year 9 Science",
              instructions: "Tutor format",
              message_limit: 10,
              teacher: "teacher1",
              live_session: sessionInfo,
            },
          ],
        }),
      });
    });

    await page.route("**/api/sessions/7", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sessionInfo) });
    });

    await page.route("**/api/sessions/7/events", async (route) => {
      await route.fulfill({
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
        body: "event: hello\ndata: {}\n\n",
      });
    });

    await page.route("**/api/sessions/7/students", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ students: studentCards }) });
    });

    await page.route("**/api/sessions/7/usage", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(usageStats) });
    });

    await page.route("**/api/students/1/transcript", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(arohaTranscript) });
    });

    await page.route("**/api/students/2/transcript", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(benTranscript) });
    });

    await page.route("**/api/models", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ models: [{ name: "gemma-4-e2b-it", enabled: true }] }),
      });
    });

    await page.route("**/healthz", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", db: "ok", guard: "ok" }),
      });
    });

    await page.goto("/teach");

    // 1. Student card grid renders cards
    const cardGrid = page.getByRole("list", { name: "Students" });
    await expect(cardGrid).toBeVisible();

    const arohaCard = cardGrid.getByRole("button", { name: /Aroha/ });
    const benCard = cardGrid.getByRole("button", { name: /Ben/ });
    await expect(arohaCard).toBeVisible();
    await expect(benCard).toBeVisible();
    await expect(arohaCard).toContainText("Flagged: 1");

    // On 820px viewport, minmax(14rem, 1fr) reflows cards into multiple columns on the same row
    const arohaBox = await arohaCard.boundingBox();
    const benBox = await benCard.boundingBox();
    expect(arohaBox).not.toBeNull();
    expect(benBox).not.toBeNull();
    expect(Math.abs(arohaBox!.y - benBox!.y)).toBeLessThan(10);
    expect(benBox!.x).toBeGreaterThan(arohaBox!.x);

    // 2. Click Aroha's card in portrait (820px width < 900px breakpoint)
    await arohaCard.click();
    await expect(arohaCard).toHaveAttribute("aria-pressed", "true");

    const arohaPanel = page.getByRole("region", { name: "Transcript for Aroha" });
    await expect(arohaPanel).toBeVisible();
    await expect(arohaPanel).toContainText("Aroha (100001)");
    await expect(arohaPanel).toContainText("Unsafe search term");
    await expect(arohaPanel).toContainText("Flag: Violent");

    // In portrait (< 900px breakpoint), transcript renders stacked below cards
    const gridBoxPortrait = await cardGrid.boundingBox();
    const panelBoxPortrait = await arohaPanel.boundingBox();
    expect(gridBoxPortrait).not.toBeNull();
    expect(panelBoxPortrait).not.toBeNull();
    expect(panelBoxPortrait!.y).toBeGreaterThanOrEqual(gridBoxPortrait!.y + gridBoxPortrait!.height - 1);

    // 3. Rotate to landscape (1180x820 >= 900px breakpoint) -> Side-by-side reflow
    await page.setViewportSize({ width: 1180, height: 820 });

    // In landscape (>= 900px), transcript panel renders to the right of the cards grid
    await expect.poll(async () => {
      const grid = await cardGrid.boundingBox();
      const panel = await arohaPanel.boundingBox();
      if (!grid || !panel) return false;
      return panel.x >= grid.x + grid.width - 1 && Math.abs(panel.y - grid.y) < 10;
    }).toBe(true);

    // 4. Switch transcript to Ben
    await benCard.click();
    await expect(benCard).toHaveAttribute("aria-pressed", "true");
    const benPanel = page.getByRole("region", { name: "Transcript for Ben" });
    await expect(benPanel).toBeVisible();
    await expect(benPanel).toContainText("What is an atom?");
    await expect(benPanel).toContainText("An atom is the basic unit of a chemical element.");

    // 5. Close button closes the transcript panel
    await benPanel.getByRole("button", { name: "Close" }).click();
    await expect(benPanel).not.toBeVisible();
  });
});
