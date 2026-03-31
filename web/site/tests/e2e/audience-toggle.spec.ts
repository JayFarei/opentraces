import { test, expect, Page } from "@playwright/test";

const DOCS_URL = "/docs";

// Helper: clear localStorage before each test to start in human mode
async function clearStorage(page: Page) {
  await page.goto(DOCS_URL);
  await page.evaluate(() => localStorage.removeItem("docs-audience"));
  await page.reload();
  await page.waitForLoadState("networkidle");
}

test.describe("Audience toggle — general", () => {
  test.beforeEach(async ({ page }) => {
    await clearStorage(page);
  });

  test("toggle pill is visible on the docs page", async ({ page }) => {
    const toggle = page.locator(".audience-toggle");
    await expect(toggle).toBeVisible();
  });

  test("default audience is HUMAN", async ({ page }) => {
    const humanBtn = page.locator(".audience-toggle-option").filter({ hasText: "HUMAN" });
    await expect(humanBtn).toHaveClass(/active/);

    const machineBtn = page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" });
    await expect(machineBtn).not.toHaveClass(/active/);
  });

  test("clicking MACHINE shows machine view", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    // Wait for overlay to fade and content to swap
    await page.waitForTimeout(900);

    await expect(page.locator(".machine-view")).toBeVisible();
    await expect(page.locator(".docs-content")).not.toBeVisible();
  });

  test("MACHINE view shows front matter", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    const fm = page.locator(".machine-frontmatter");
    await expect(fm).toBeVisible();
    await expect(fm).toContainText("name");
    await expect(fm).toContainText("skill");
    await expect(fm).toContainText("agent traces");
  });

  test("MACHINE view shows copy raw button", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await expect(page.locator(".machine-copy-btn")).toBeVisible();
  });

  test("switching back to HUMAN restores docs content", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await page.locator(".audience-toggle-option").filter({ hasText: "HUMAN" }).click();
    await page.waitForTimeout(900);

    await expect(page.locator(".docs-content")).toBeVisible();
    await expect(page.locator(".machine-view")).not.toBeVisible();
  });

  test("machine mode persists on reload", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(300);

    await expect(page.locator(".machine-view")).toBeVisible();
    const machineBtn = page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" });
    await expect(machineBtn).toHaveClass(/active/);
  });

  test("page background goes black in machine mode", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    const bg = await page.evaluate(() =>
      window.getComputedStyle(document.documentElement).backgroundColor
    );
    // rgb(0, 0, 0) = black
    expect(bg).toBe("rgb(0, 0, 0)");
  });

  test("sidebar is hidden in machine mode", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    const sidebar = page.locator(".docs-sidebar");
    await expect(sidebar).not.toBeVisible();
  });

  test("nav is hidden in machine mode", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await expect(page.locator(".nav")).not.toBeVisible();
  });

  test("X exit button appears in machine mode", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await expect(page.locator(".machine-exit-btn")).toBeVisible();
  });

  test("X exit button returns to human mode", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await page.locator(".machine-exit-btn").click();
    await page.waitForTimeout(900);

    await expect(page.locator(".docs-content")).toBeVisible();
    await expect(page.locator(".machine-exit-btn")).not.toBeVisible();
  });

  test("toggle is visible on any docs sub-page", async ({ page }) => {
    await page.goto("/docs/getting-started/installation");
    await page.waitForLoadState("networkidle");

    await expect(page.locator(".audience-toggle")).toBeVisible();
  });
});

test.describe("Audience toggle — mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await clearStorage(page);
  });

  test("toggle pill is visible on mobile", async ({ page }) => {
    await expect(page.locator(".audience-toggle")).toBeVisible();
  });

  test("toggle is horizontally centered on mobile", async ({ page }) => {
    const toggle = await page.locator(".audience-toggle").boundingBox();
    const viewport = page.viewportSize()!;

    expect(toggle).not.toBeNull();
    // Center of toggle should be within 20px of center of viewport
    const toggleCenter = toggle!.x + toggle!.width / 2;
    expect(Math.abs(toggleCenter - viewport.width / 2)).toBeLessThan(20);
  });

  test("MACHINE toggle works on mobile", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await expect(page.locator(".machine-view")).toBeVisible();
  });

  test("machine view is scrollable on mobile", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    const contentHeight = await page.evaluate(
      () => document.querySelector(".machine-view")?.scrollHeight ?? 0
    );
    const viewport = page.viewportSize()!;
    // Machine view content should be taller than the viewport (it has a lot of content)
    expect(contentHeight).toBeGreaterThan(viewport.height);
  });

  test("toggle remains visible while scrolled on mobile", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await page.mouse.wheel(0, 800);
    await page.waitForTimeout(100);

    // Toggle is fixed so should still be in viewport
    await expect(page.locator(".audience-toggle")).toBeVisible();
  });

  test("machine view front matter readable on mobile", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    const fm = page.locator(".machine-frontmatter");
    await expect(fm).toBeVisible();

    // Front matter should not overflow viewport width
    const box = await fm.boundingBox();
    const viewport = page.viewportSize()!;
    expect(box!.width).toBeLessThanOrEqual(viewport.width);
  });

  test("switching back to HUMAN works on mobile", async ({ page }) => {
    await page.locator(".audience-toggle-option").filter({ hasText: "MACHINE" }).click();
    await page.waitForTimeout(900);

    await page.locator(".audience-toggle-option").filter({ hasText: "HUMAN" }).click();
    await page.waitForTimeout(900);

    await expect(page.locator(".docs-content")).toBeVisible();
  });
});
