import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 15_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  retries: 0,
  use: {
    baseURL: "http://localhost:3000",
    // Clear localStorage between tests
    storageState: undefined,
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-safari",
      use: { ...devices["iPhone 14"] },
    },
    {
      name: "mobile-android",
      use: { ...devices["Pixel 7"] },
    },
  ],
  // Expect dev server already running (do not launch)
  webServer: undefined,
});
