import type { Theme } from "../tokens";
import { F } from "../tokens";

function hasInlineFormatting(text: string): boolean {
  return text.includes("**") || text.includes("`");
}

function Fmt({ text, t }: { text: string; t: Theme }) {
  if (!hasInlineFormatting(text)) {
    return <>{text}</>;
  }

  const parts: React.ReactNode[] = [];
  let rem = text, k = 0;
  while (rem.length > 0) {
    const bm = rem.match(/\*\*(.+?)\*\*/);
    const cm = rem.match(/`(.+?)`/);
    let pick: { tp: "b" | "c"; m: RegExpMatchArray; i: number } | null = null;
    let pickI = Infinity;
    if (bm && bm[0] && rem.indexOf(bm[0]) < pickI) {
      pick = { tp: "b", m: bm, i: rem.indexOf(bm[0]) }; pickI = pick.i;
    }
    if (cm && cm[0] && rem.indexOf(cm[0]) < pickI) {
      pick = { tp: "c", m: cm, i: rem.indexOf(cm[0]) }; pickI = pick.i;
    }
    if (!pick) { parts.push(<span key={k++}>{rem}</span>); break; }
    if (pickI > 0) parts.push(<span key={k++}>{rem.slice(0, pickI)}</span>);
    const inner = pick.m[1] ?? "";
    if (pick.tp === "b") {
      parts.push(<span key={k++} style={{ color: t.text, fontWeight: 600 }}>{inner}</span>);
    } else {
      parts.push(
        <span key={k++} style={{
          fontFamily: F.code, fontSize: 11, color: t.cyan,
          background: `${t.cyan}10`, padding: "1px 4px",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}>{inner}</span>,
      );
    }
    rem = rem.slice(pickI + (pick.m[0]?.length ?? 0));
  }
  return <>{parts}</>;
}

export function RichText({ text, t }: { text: string; t: Theme }) {
  return (
    <div style={{ width: "100%", minWidth: 0 }}>
      {text.split("\n").map((line, li) => {
        const inlineFormatting = hasInlineFormatting(line);
        const num = line.match(/^(\d+)\.\s(.*)$/);
        if (num) {
          return (
            <div key={li} style={{ display: "flex", gap: 6, marginBottom: 2, width: "100%", minWidth: 0 }}>
              <span style={{ color: t.textDim, minWidth: 16, textAlign: "right" }}>{num[1]}.</span>
              <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>
                {inlineFormatting ? <Fmt text={num[2] ?? ""} t={t} /> : (num[2] ?? "")}
              </span>
            </div>
          );
        }
        if (line.startsWith("- ")) {
          return (
            <div key={li} style={{ display: "flex", gap: 6, paddingLeft: 4, marginBottom: 2, width: "100%", minWidth: 0 }}>
              <span style={{ color: t.textDim }}>-</span>
              <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere", wordBreak: "break-word" }}>
                {inlineFormatting ? <Fmt text={line.slice(2)} t={t} /> : line.slice(2)}
              </span>
            </div>
          );
        }
        const bh = line.match(/^\*\*(.+?)\*\*$/);
        if (bh) {
          return (
            <div key={li} style={{ color: t.text, fontWeight: 600, marginTop: 10, marginBottom: 4, overflowWrap: "anywhere", wordBreak: "break-word" }}>
              <Fmt text={bh[1] ?? ""} t={t} />
            </div>
          );
        }
        if (!line.trim()) return <div key={li} style={{ height: 10 }} />;
        return (
          <div key={li} style={{ marginBottom: 2, overflowWrap: "anywhere", wordBreak: "break-word" }}>
            {inlineFormatting ? <Fmt text={line} t={t} /> : line}
          </div>
        );
      })}
    </div>
  );
}
