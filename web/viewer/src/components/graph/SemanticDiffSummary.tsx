import type { Theme } from "../../tokens";
import { F } from "../../tokens";

export type SemanticDiffKind = "added" | "modified" | "deleted" | "renamed" | "unknown";
type KnownSemanticDiffKind = Exclude<SemanticDiffKind, "unknown">;

export interface SemanticDiffPart {
  kind: SemanticDiffKind;
  symbol: string;
  verb: string;
  body: string;
  raw: string;
}

const PART_PATTERNS: Array<{
  kind: KnownSemanticDiffKind;
  symbol: string;
  verb: string;
  pattern: RegExp;
}> = [
  { kind: "added", symbol: "+", verb: "Added", pattern: /^\+\s+Added\s+(.+)$/i },
  { kind: "modified", symbol: "~", verb: "Modified", pattern: /^~\s+Modified\s+(.+)$/i },
  { kind: "deleted", symbol: "-", verb: "Deleted", pattern: /^-\s+Deleted\s+(.+)$/i },
  { kind: "renamed", symbol: "↷", verb: "Renamed", pattern: /^↷\s+Renamed\s+(.+)$/i },
];

export function parseSemanticDiffSummary(text: string | null | undefined): SemanticDiffPart[] {
  if (!text) return [];
  return text
    .split(/\s+·\s+/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((raw) => {
      for (const entry of PART_PATTERNS) {
        const match = raw.match(entry.pattern);
        if (match) {
          return {
            kind: entry.kind,
            symbol: entry.symbol,
            verb: entry.verb,
            body: match[1] ?? "",
            raw,
          };
        }
      }
      return {
        kind: "unknown",
        symbol: "",
        verb: "",
        body: raw,
        raw,
      };
    });
}

export function countSemanticDiffKinds(text: string | null | undefined) {
  const counts = new Map<KnownSemanticDiffKind, number>();
  for (const part of parseSemanticDiffSummary(text)) {
    if (part.kind === "unknown") continue;
    const entityCount = part.body
      .split(/\s*,\s*/)
      .map((entry) => entry.trim())
      .filter(Boolean)
      .length;
    counts.set(part.kind, (counts.get(part.kind) ?? 0) + Math.max(entityCount, 1));
  }
  return PART_PATTERNS
    .map((entry) => ({
      kind: entry.kind,
      symbol: entry.symbol,
      verb: entry.verb,
      count: counts.get(entry.kind) ?? 0,
    }))
    .filter((entry) => entry.count > 0);
}

function diffColors(kind: SemanticDiffKind, t: Theme) {
  switch (kind) {
    case "added":
      return { fg: t.green, bg: `${t.green}12`, border: `${t.green}36`, badgeBg: `${t.green}20` };
    case "modified":
      return { fg: t.cyan, bg: `${t.cyan}12`, border: `${t.cyan}36`, badgeBg: `${t.cyan}20` };
    case "deleted":
      return { fg: t.red, bg: `${t.red}12`, border: `${t.red}36`, badgeBg: `${t.red}20` };
    case "renamed":
      return { fg: t.yellow, bg: `${t.yellow}12`, border: `${t.yellow}36`, badgeBg: `${t.yellow}20` };
    case "unknown":
      return { fg: t.textMuted, bg: t.bgAlt, border: t.border, badgeBg: t.bgAlt };
  }
}

export function SemanticDiffSummary({
  text,
  t,
  compact = false,
  stacked = false,
}: {
  text: string | null | undefined;
  t: Theme;
  compact?: boolean;
  stacked?: boolean;
}) {
  const parts = parseSemanticDiffSummary(text);
  if (parts.length === 0) return null;

  return (
    <div style={{
      display: stacked ? "grid" : "flex",
      flexWrap: stacked ? undefined : "wrap",
      gap: compact ? 4 : 6,
      minWidth: 0,
      maxWidth: "100%",
      alignItems: stacked ? undefined : "center",
      justifyItems: stacked ? "start" : undefined,
    }} data-diff-layout={stacked ? "stacked" : "inline"}>
      {parts.map((part, index) => {
        const colors = diffColors(part.kind, t);
        if (part.kind === "unknown") {
          return (
            <span
              key={`${part.raw}-${index}`}
              style={{
                color: t.textMuted,
                minWidth: 0,
                overflowWrap: "anywhere",
                wordBreak: "break-word",
              }}
            >
              {part.raw}
            </span>
          );
        }
        return (
          <span
            key={`${part.raw}-${index}`}
            data-diff-kind={part.kind}
            aria-label={`${part.verb} ${part.body}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: compact ? 4 : 5,
              padding: compact ? "1px 6px" : "2px 7px",
              border: `1px solid ${colors.border}`,
              borderRadius: 999,
              background: colors.bg,
              color: colors.fg,
              fontFamily: F.code,
              fontSize: compact ? 10 : 11,
              lineHeight: 1.4,
              minWidth: 0,
              maxWidth: "100%",
            }}
          >
            <span style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              minWidth: compact ? 14 : 16,
              height: compact ? 14 : 16,
              padding: "0 4px",
              borderRadius: 999,
              background: colors.badgeBg,
              fontWeight: 700,
              flex: "0 0 auto",
            }}>
              {part.symbol}
            </span>
            <span style={{ fontWeight: 600, flex: "0 0 auto" }}>{part.verb}</span>
            <span style={{
              color: t.textSec,
              minWidth: 0,
              overflowWrap: "anywhere",
              wordBreak: "break-word",
            }}>
              {part.body}
            </span>
          </span>
        );
      })}
    </div>
  );
}

export function SemanticDiffCounts({
  text,
  t,
  compact = false,
}: {
  text: string | null | undefined;
  t: Theme;
  compact?: boolean;
}) {
  const counts = countSemanticDiffKinds(text);
  if (counts.length === 0) return null;

  return (
    <div style={{
      display: "flex",
      flexWrap: "wrap",
      gap: compact ? 4 : 6,
      minWidth: 0,
      alignItems: "center",
    }} data-diff-layout="counts">
      {counts.map((entry) => {
        const colors = diffColors(entry.kind, t);
        return (
          <span
            key={entry.kind}
            data-diff-count-kind={entry.kind}
            aria-label={`${entry.count} ${entry.verb.toLowerCase()} change${entry.count === 1 ? "" : "s"}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: compact ? "0 4px" : "1px 5px",
              borderRadius: 999,
              border: `1px solid ${colors.border}`,
              background: colors.bg,
              color: colors.fg,
              fontFamily: F.code,
              fontSize: compact ? 10 : 11,
              lineHeight: 1.5,
              whiteSpace: "nowrap",
            }}
          >
            <span style={{ fontWeight: 700 }}>{entry.symbol}</span>
            <span>{entry.count}</span>
          </span>
        );
      })}
    </div>
  );
}
