import { useEffect, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Theme } from "../../tokens";
import { F, pctColor, trunc, traceColor } from "../../tokens";
import { Panel } from "../Panel";
import { ForwardBlame } from "./ForwardBlame";
import { SemanticDiffCounts } from "./SemanticDiffSummary";
import { api } from "../../lib/api";
import type { GraphCommit } from "../../lib/api";

const PAGE_SIZE = 10;

function TraceSwatch({ color }: { color: string }) {
  return (
    <span
      aria-hidden="true"
      style={{
        color,
        fontSize: 8,
        lineHeight: 1,
        minWidth: 10,
        textAlign: "center",
        flex: "0 0 auto",
      }}
    >
      ●
    </span>
  );
}

export function GraphView({
  t, wide, mode, gotoTrace,
}: {
  t: Theme; wide: boolean; mode: "dark" | "light";
  gotoTrace: (id: string) => void;
}) {
  const graphQ = useInfiniteQuery({
    queryKey: ["graph"],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => api.graph({ limit: PAGE_SIZE, page: pageParam as number }),
    getNextPageParam: (last, pages) =>
      (last.commits.length < PAGE_SIZE ? undefined : pages.length + 1),
  });
  const commits: GraphCommit[] = graphQ.data?.pages.flatMap((p) => p.commits) ?? [];
  const [selIdx, setSelIdx] = useState(0);
  useEffect(() => { if (selIdx >= commits.length) setSelIdx(0); }, [commits.length, selIdx]);

  const selected = commits[selIdx];
  const blameQ = useQuery({
    queryKey: ["blame", selected?.sha],
    queryFn: () => api.blame(selected!.sha),
    enabled: !!selected,
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelIdx((i) => Math.min(i + 1, commits.length - 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelIdx((i) => Math.max(i - 1, 0));
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [commits.length]);

  const left = (
    <div style={{
      width: wide ? 400 : "100%", minWidth: wide ? 400 : 0,
      display: "flex", flexDirection: "column",
      ...(wide ? {} : { marginBottom: 20 }),
    }}>
      <Panel n={1} label="Commits" t={t} style={{ flex: 1 }}>
        <div style={{ flex: 1, overflow: "auto" }}>
          {graphQ.isLoading && (
            <div style={{ padding: 14, fontFamily: F.code, fontSize: 11, color: t.textMuted }}>
              Loading…
            </div>
          )}
          {graphQ.isError && (
            <div style={{ padding: 14, fontFamily: F.code, fontSize: 11, color: t.red }}>
              Failed to load graph.
            </div>
          )}
          {!graphQ.isLoading && commits.length === 0 && (
            <div style={{ padding: 14, fontFamily: F.code, fontSize: 11, color: t.textMuted }}>
              No attribution data. Run <span style={{ color: t.cyan }}>ot backfill</span>.
            </div>
          )}
          {commits.length > 0 && commits.map((c, ci) => {
            const isSel = ci === selIdx;
            const pc = pctColor(c.pct, t);
            return (
              <div
                key={c.sha}
                data-testid={`graph-commit-${c.sha}`}
                onClick={() => setSelIdx(ci)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  setSelIdx(ci);
                }}
                role="button"
                tabIndex={0}
                aria-pressed={isSel}
                style={{
                  borderBottom: `1px solid ${t.border}`,
                  background: isSel ? `${t.cyan}10` : "transparent",
                  borderLeft: isSel ? `2px solid ${t.cyan}` : "2px solid transparent",
                  cursor: "pointer",
                  outline: "none",
                }}
              >
                <div style={{
                  display: "flex", alignItems: "baseline", gap: 8,
                  padding: "10px 14px 4px", fontFamily: F.code, fontSize: 12,
                }}>
                  <span style={{ color: pc, fontSize: 10 }}>●</span>
                  <span style={{
                    color: isSel ? t.cyan : t.yellow, fontWeight: 500,
                    textDecoration: "underline",
                    textDecorationColor: isSel ? `${t.cyan}40` : `${t.yellow}40`,
                    textUnderlineOffset: 2,
                  }}>{c.id}</span>
                  <span style={{
                    color: isSel ? t.text : t.textSec, flex: 1, minWidth: 0,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  }}>{trunc(c.msg, wide ? 36 : 55)}</span>
                  <span style={{ color: pc, fontWeight: 600, whiteSpace: "nowrap" }}>{c.pct}%</span>
                </div>
                <div style={{ padding: "0 14px 10px 32px" }}>
                  {c.traces.map((tr, ti) => {
                    const color = traceColor(tr.trace_id, mode);
                    return (
                      <div
                        key={tr.trace_id + ti}
                        style={{
                          display: "flex", alignItems: "center", gap: 8,
                          fontFamily: F.code, fontSize: 11, lineHeight: 1.6,
                          padding: "2px 0",
                          minWidth: 0,
                        }}
                      >
                        <span style={{ color: t.textDim, minWidth: 10, flex: "0 0 auto" }}>
                          {ti === c.traces.length - 1 ? "└" : "├"}
                        </span>
                        <TraceSwatch color={color} />
                        {tr.canonical ? (
                          <button
                            data-testid={`graph-trace-link-${tr.trace_id}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              setSelIdx(ci);
                              gotoTrace(tr.trace_id);
                            }}
                            style={{
                              border: "none",
                              background: "transparent",
                              padding: 0,
                              margin: 0,
                              color,
                              cursor: "pointer",
                              fontFamily: F.code,
                              fontSize: 11,
                              textDecoration: "underline",
                              textDecorationColor: `${color}40`,
                              textUnderlineOffset: 2,
                              flex: "0 0 auto",
                            }}
                          >
                            {tr.id}
                          </button>
                        ) : (
                          <span style={{ color, flex: "0 0 auto" }}>{tr.id}</span>
                        )}
                        {tr.changes ? (
                          <div style={{
                            display: "flex", gap: 4, alignItems: "center",
                            marginLeft: "auto", flex: "0 0 auto",
                            whiteSpace: "nowrap", overflow: "hidden",
                          }}>
                            <SemanticDiffCounts text={tr.changes} t={t} compact />
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {commits.length > 0 && (graphQ.hasNextPage || graphQ.isFetchingNextPage) && (
            <div
              onClick={() => { if (!graphQ.isFetchingNextPage) graphQ.fetchNextPage(); }}
              style={{
                padding: "10px 14px", textAlign: "center",
                fontFamily: F.code, fontSize: 11,
                color: graphQ.isFetchingNextPage ? t.textMuted : t.cyan,
                cursor: graphQ.isFetchingNextPage ? "default" : "pointer",
                borderTop: `1px solid ${t.border}`,
              }}
            >
              {graphQ.isFetchingNextPage ? "Loading…" : "Load more"}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );

  const right = (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <Panel n={2} label="Blame" t={t} style={{ flex: 1 }}>
        <div style={{ flex: 1, overflow: "auto", padding: "4px 18px 16px" }}>
          {!selected && (
            <div style={{ fontFamily: F.code, fontSize: 12, color: t.textMuted }}>
              Select a commit.
            </div>
          )}
          {selected && blameQ.isLoading && (
            <div style={{ fontFamily: F.code, fontSize: 12, color: t.textMuted }}>Loading…</div>
          )}
          {selected && blameQ.isError && (
            <div style={{ fontFamily: F.code, fontSize: 12, color: t.red }}>
              No attribution cached for this commit.
            </div>
          )}
          {selected && blameQ.data && (
            <ForwardBlame
              t={t}
              blame={blameQ.data}
              mode={mode}
              onSelectTrace={gotoTrace}
              commitMsgFallback={selected.msg}
            />
          )}
        </div>
      </Panel>
    </div>
  );

  if (!wide) {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "18px 16px 8px", overflow: "auto" }}>
        {left}{right}
      </div>
    );
  }
  return (
    <div style={{ flex: 1, display: "flex", padding: "20px 16px 8px", gap: 16, minHeight: 0 }}>
      {left}{right}
    </div>
  );
}
