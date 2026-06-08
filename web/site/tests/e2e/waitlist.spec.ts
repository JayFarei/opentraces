import { test, expect, APIRequestContext } from "@playwright/test";

// End-to-end coverage for the early-access waitlist (/api/waitlist).
//
// These run against a dev server with the file-store fallback (no Vercel KV
// needed). To avoid tripping the per-IP rate limit when the whole suite hammers
// localhost, start the server with a raised limit, e.g.:
//
//   WAITLIST_RATE_LIMIT=10000 WAITLIST_ADMIN_TOKEN=test-admin-token \
//     PORT=3100 npm run dev
//   PLAYWRIGHT_BASE_URL=http://localhost:3100 npm run test:e2e -- waitlist
//
// Unique address per assertion keeps runs independent (no cross-run dedup).
let n = 0;
function uniqueEmail(tag = "user") {
  n += 1;
  return `ea-${tag}-${Date.now()}-${n}@example.com`;
}

async function post(request: APIRequestContext, body: Record<string, unknown>) {
  return request.post("/api/waitlist", { data: body });
}

test.describe("Early-access waitlist API", () => {
  test("accepts a valid email and reports it as newly added", async ({ request }) => {
    const email = uniqueEmail();
    const res = await post(request, { email });
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(true);
    expect(json.alreadyOnList).toBe(false);
  });

  test("dedupes a repeated email (alreadyOnList on the second submit)", async ({ request }) => {
    const email = uniqueEmail("dup");
    const first = await (await post(request, { email })).json();
    expect(first).toMatchObject({ ok: true, alreadyOnList: false });

    const second = await (await post(request, { email })).json();
    expect(second).toMatchObject({ ok: true, alreadyOnList: true });
  });

  test("normalizes case + whitespace before dedup", async ({ request }) => {
    const base = uniqueEmail("case").toLowerCase();
    await post(request, { email: base });
    const again = await (await post(request, { email: `  ${base.toUpperCase()}  ` })).json();
    expect(again).toMatchObject({ ok: true, alreadyOnList: true });
  });

  test("rejects an invalid email with 400", async ({ request }) => {
    const res = await post(request, { email: "not-an-email" });
    expect(res.status()).toBe(400);
    expect((await res.json()).ok).toBe(false);
  });

  test("rejects an empty email with 400", async ({ request }) => {
    const res = await post(request, { email: "" });
    expect(res.status()).toBe(400);
  });

  test("honeypot submission is silently dropped and never stored", async ({ request }) => {
    const email = uniqueEmail("bot");
    // Bot fills the honeypot -> fake success, no storage.
    const bot = await post(request, { email, company: "Acme Spam Co" });
    expect(bot.status()).toBe(200);
    expect(await bot.json()).toMatchObject({ ok: true, alreadyOnList: false });

    // Proof it was NOT stored: a clean resubmit reports it as newly added.
    const real = await (await post(request, { email })).json();
    expect(real).toMatchObject({ ok: true, alreadyOnList: false });
  });
});

test.describe("Early-access waitlist UI (/hub)", () => {
  // /hub can render more than one early-access form (teaser + CTA); scope to the first.
  test("submitting the form shows the success state", async ({ page }) => {
    await page.goto("/hub");
    const form = page.locator(".ea-form").first();
    await expect(form).toBeVisible();

    await form.locator("input.ea-input").fill(uniqueEmail("ui"));
    await form.locator("button.ea-btn").click();

    await expect(form.locator(".ea-msg.ea-success")).toBeVisible();
    await expect(form.locator(".ea-btn")).toContainText("joined");
  });

  test("honeypot field is present but hidden from real users", async ({ page }) => {
    await page.goto("/hub");
    const form = page.locator(".ea-form").first();
    const hp = form.locator("input[name='company']");
    // In the DOM so naive bots fill it...
    await expect(hp).toBeAttached();
    // ...but pushed off-screen, transparent, and skipped by keyboard tabbing,
    // so a real person never sees or focuses it.
    const box = await hp.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(0); // off-screen to the left
    expect(await hp.getAttribute("tabindex")).toBe("-1");
    const wrapHidden = await form
      .locator(".ea-hp")
      .evaluate((el) => el.getAttribute("aria-hidden"));
    expect(wrapHidden).toBe("true");
  });
});

// Emails sit at even indices in the flat [member, score, ...] WITHSCORES list.
function emailsOf(recent: (string | number)[]): string[] {
  return recent.filter((_, i) => i % 2 === 0) as string[];
}

test.describe("Early-access waitlist admin read", () => {
  test("admin GET is gated and rejects a missing or wrong token", async ({ request }) => {
    expect((await request.get("/api/waitlist")).status()).toBe(401);
    const wrong = await request.get("/api/waitlist", {
      headers: { Authorization: "Bearer definitely-not-the-token" },
    });
    expect(wrong.status()).toBe(401);
  });

  test("admin GET returns the list with a Bearer token (and ?token= fallback)", async ({
    request,
  }) => {
    const token = process.env.WAITLIST_ADMIN_TOKEN;
    test.skip(!token, "set WAITLIST_ADMIN_TOKEN on the server to test the read path");

    const email = uniqueEmail("admin");
    await post(request, { email });

    // Preferred: Authorization header (secret stays out of the URL).
    const viaHeader = await request.get("/api/waitlist", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(viaHeader.status()).toBe(200);
    const json = await viaHeader.json();
    expect(json.ok).toBe(true);
    expect(typeof json.count).toBe("number");
    expect(emailsOf(json.recent)).toContain(email);

    // Fallback: query param still works.
    const viaQuery = await request.get(`/api/waitlist?token=${encodeURIComponent(token!)}`);
    expect(viaQuery.status()).toBe(200);
  });

  test("recent() returns newest-first (KV/file backend parity contract)", async ({ request }) => {
    const token = process.env.WAITLIST_ADMIN_TOKEN;
    test.skip(!token, "set WAITLIST_ADMIN_TOKEN on the server to test the read path");

    const a = uniqueEmail("ord-a");
    const b = uniqueEmail("ord-b");
    const c = uniqueEmail("ord-c");
    for (const e of [a, b, c]) await post(request, { email: e }); // a oldest, c newest

    const json = await (
      await request.get("/api/waitlist", { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    const emails = emailsOf(json.recent);
    // Newest first: c must come before b must come before a.
    expect(emails.indexOf(c)).toBeLessThan(emails.indexOf(b));
    expect(emails.indexOf(b)).toBeLessThan(emails.indexOf(a));
  });
});
