#!/usr/bin/env node
// Export the launch announcement's HTML figures and examples as PNGs
// (dark + light, 2x) for republishing the article elsewhere.
// Usage: node scripts/export-launch-blog-figures.mjs [base-url]
// Re-run after rebuilding public/blog/introducing-opentraces-0-4/index.html.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.argv[2] || "http://localhost:3000";
const PAGE = `${BASE}/blog/introducing-opentraces-0-4`;
const OUT = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "public", "blog", "introducing-opentraces-0-4", "figures",
);

const TARGETS = [
  { name: "origin-thread", selector: ".x-card" },
  { name: "fig1-survival", selector: "figure:has(figcaption:has-text('fig 1'))" },
  { name: "fig2-one-session-three-views", selector: "figure:has(figcaption:has-text('fig 2'))" },
  { name: "fig3-pipeline", selector: "figure:has(figcaption:has-text('fig 3'))" },
  { name: "examples-terminal-and-agent", selector: ".duo" },
  { name: "example-distillation", selector: ".abox:has-text('distillation')" },
  { name: "example-skill-file", selector: ".filebox" },
  { name: "example-schedule", selector: ".abox:has-text('/schedule daily 7am')" },
  { name: "case-trace-capsule", selector: ".case:has-text('trace capsule')" },
  { name: "case-trace-capsule-panel", selector: ".case:has-text('trace capsule') .hub-panel" },
  { name: "case-intent-pull-request", selector: ".case:has-text('intent pull request')" },
  { name: "case-intent-pull-request-panel", selector: ".case:has-text('intent pull request') .hub-panel" },
  { name: "case-skill-verifier", selector: ".case:has-text('skill verifier')" },
  { name: "case-skill-verifier-panel", selector: ".case:has-text('skill verifier') .hub-panel" },
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1000, height: 1400 },
  deviceScaleFactor: 2,
});
await page.goto(PAGE, { waitUntil: "networkidle" });
// Kill animations/transitions so captures are deterministic.
await page.addStyleTag({
  content: "*, *::before, *::after { animation: none !important; transition: none !important; }",
});

for (const theme of ["dark", "light"]) {
  await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
  await page.waitForTimeout(150);
  for (const t of TARGETS) {
    let loc = page.locator(t.selector);
    if (t.first) loc = loc.first();
    const n = await loc.count();
    if (n === 0) { console.error(`MISS  ${t.name}  (${t.selector})`); continue; }
    if (n > 1 && !t.first) loc = loc.first();
    await loc.scrollIntoViewIfNeeded();
    const path = join(OUT, `${t.name}-${theme}.png`);
    await loc.screenshot({ path });
    console.log(`ok    ${t.name}-${theme}.png`);
  }
}

await browser.close();
console.log("done →", OUT);
