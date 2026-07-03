import { describe, expect, it } from "vitest";
import { isExcludedByAuthor, renderCapsuleHtml } from "../src/render.js";
import type { ResolvedCapsule } from "../src/resolve.js";
import { ENV_FACE_TOKENS } from "./helpers.js";
import { sampleRaw } from "./fixtures/sample.js";

function resolvedWith(mutate: (c: any) => void = () => {}): ResolvedCapsule {
  const capsule = JSON.parse(sampleRaw);
  mutate(capsule);
  return {
    ref: { owner: "acme", repo: "fetcher", id: "cap1", sha: "s", pinned: true, endpoint: null },
    raw: sampleRaw,
    capsule,
    resolveUrl: "",
  } as unknown as ResolvedCapsule;
}

const VIEWER = "https://capsules.opentraces.ai/acme/fetcher/cap1@s";

describe("isExcludedByAuthor", () => {
  it("recognizes the [EXCLUDED:…] marker only", () => {
    expect(isExcludedByAuthor("[EXCLUDED:context_resume_packet.system_layer]")).toBe(true);
    expect(isExcludedByAuthor("hello")).toBe(false);
    expect(isExcludedByAuthor("[REDACTED]")).toBe(false);
  });
});

describe("rule 1: untrusted content is escaped (XSS sentinel)", () => {
  it("escapes captured strings in the body, headings, and title", () => {
    const html = renderCapsuleHtml(
      resolvedWith((c) => {
        c.intent.headline = "<script>alert('xss')</script>";
        c.summary.title = "</title><script>alert(2)</script>";
        c.summary.what_happened = "<img src=x onerror=alert(3)>";
        c.slice.steps[1].observations[0].output_summary = "<svg onload=alert(4)>";
      }),
      VIEWER,
    );
    // no raw executable markup survives
    expect(html).not.toContain("<script>alert('xss')</script>");
    expect(html).not.toContain("<script>alert(2)</script>");
    expect(html).not.toContain("<img src=x onerror=alert(3)>");
    expect(html).not.toContain("<svg onload=alert(4)>");
    // escaped forms are present
    expect(html).toContain("&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;");
    expect(html).toContain("&lt;svg onload=alert(4)&gt;");
    // the untrusted banner is shown
    expect(html).toContain("captured, untrusted content");
  });
});

describe("rule 3: privacy is author intent (excluded, not broken)", () => {
  it("renders system_layer and excluded privacy fields as 'excluded by author'", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("excluded by author");
    // the raw [EXCLUDED:…] marker is not surfaced for system_layer
    expect(html).not.toContain("[EXCLUDED:context_resume_packet.system_layer]");
  });
});

describe("rule 4: limitations are rendered verbatim", () => {
  it("lists every limitation", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("no_executable_test");
    expect(html).toContain("trail_anchors_unavailable");
  });
});

describe("rule 5: replay is a claim, not proof", () => {
  it("labels the verdict as unverified / not yet reproduced", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("replay_unverified");
    expect(html.toLowerCase()).toContain("not yet reproduced");
  });
});

describe("rule 2: redaction is counts-only", () => {
  it("shows the counts badge", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("12 redactions");
    expect(html).toContain("13 paths scrubbed");
    expect(html).toContain("regex+entropy");
  });
});

describe("messages are surfaced as hash-only, never as text", () => {
  it("counts messages and labels hash-only", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("2 messages (hash-only)");
  });
});

describe("never serves the environment face", () => {
  it("renders only the name-only projection, no heavy sibling tokens", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("name-only");
    expect(html).toContain("click");
    expect(html).toContain("huggingface_hub");
    for (const token of ENV_FACE_TOKENS) expect(html).not.toContain(token);
  });
});

describe("focus-step wording is honest", () => {
  it("says 'focus step' when there was no captured error", () => {
    const html = renderCapsuleHtml(resolvedWith(), VIEWER);
    expect(html).toContain("focus step 4");
    expect(html).not.toContain("error at step 4");
  });

  it("says 'error at step' only when an error was captured", () => {
    const html = renderCapsuleHtml(
      resolvedWith((c) => {
        c.failing_step.had_error_marker = true;
        c.failing_step.error_excerpt = "httpx.ReadTimeout: timed out";
      }),
      VIEWER,
    );
    expect(html).toContain("error at step 4");
    expect(html).toContain("httpx.ReadTimeout: timed out");
  });
});
