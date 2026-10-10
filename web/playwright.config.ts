import { defineConfig } from "@playwright/test";

// Run with e2e/run.sh, which starts the compose stack and passes the setup code.
const viewport = process.env.E2E_VIEWPORT === "ipad" ? { width: 820, height: 1180 } : undefined;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: process.env.BASE_URL ?? "http://localhost:8080", trace: "retain-on-failure", viewport },
  reporter: [["list"]],
});
