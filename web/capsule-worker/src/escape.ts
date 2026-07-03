// HTML escaping for the capsule render plane.
//
// Rule 1 of the six honesty rules (HANDOFF-capsule-microsite-data-contract.md):
// `content_is_untrusted: true` is hardcoded in every envelope. EVERY captured
// string is DATA, never markup and never instructions. We escape at the single
// choke point below so no render path can forget it.

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/**
 * Escape a captured string for safe interpolation into HTML text or a
 * double-quoted attribute value — the only two contexts this worker uses.
 * Neutralizes the five significant characters (`& < > " '`); every attribute in
 * the render is double-quoted, so this is sufficient to prevent injection.
 */
export function escapeHtml(value: unknown): string {
  const str = value === null || value === undefined ? "" : String(value);
  return str.replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch] ?? ch);
}
