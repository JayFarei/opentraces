import { useViewPreferences } from "../../contexts/ViewPreferencesContext";
import { ReasoningBlock } from "./ReasoningBlock";
import { SecurityBadge } from "./SecurityBadge";
import { ToolCallDetail } from "./ToolCallDetail";
import type { Step, SecurityMetadata } from "../../types/trace";

interface StepDetailProps {
  step: Step;
  security: SecurityMetadata | null;
}

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-[family-name:var(--font-mono)] border-b border-[var(--border)] pb-1 mb-2 mt-4 first:mt-0">
      {label}
    </div>
  );
}

export function StepDetail({ step, security }: StepDetailProps) {
  const { showReasoning } = useViewPreferences();

  const usage = step.token_usage;

  return (
    <div>
      {/* METADATA */}
      <SectionHeader label="metadata" />
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] font-[family-name:var(--font-mono)]">
        <div>
          <span className="text-[var(--text-muted)]">role: </span>
          <span className="text-[var(--text)]">{step.role}</span>
        </div>
        <div>
          <span className="text-[var(--text-muted)]">step: </span>
          <span className="text-[var(--text)]">{step.step_index}</span>
        </div>
        {step.model && (
          <div>
            <span className="text-[var(--text-muted)]">model: </span>
            <span className="text-[var(--text)]">{step.model}</span>
          </div>
        )}
        {step.call_type && (
          <div>
            <span className="text-[var(--text-muted)]">call_type: </span>
            <span className="text-[var(--text)]">{step.call_type}</span>
          </div>
        )}
        <div className="col-span-2">
          <span className="text-[var(--text-muted)]">tokens: </span>
          <span className="text-[var(--text)]">
            <span title="input">&#8595;{usage.input_tokens.toLocaleString()}</span>
            {" "}
            <span title="output">&#8593;{usage.output_tokens.toLocaleString()}</span>
            {" "}
            <span className="text-[var(--text-dim)]" title="cache read">
              cache:{usage.cache_read_tokens.toLocaleString()}
            </span>
          </span>
        </div>
      </div>

      {security && (
        <div className="mt-2">
          <SecurityBadge security={security} />
        </div>
      )}

      {/* REASONING */}
      {step.reasoning_content && showReasoning && (
        <>
          <SectionHeader label="reasoning" />
          <ReasoningBlock content={step.reasoning_content} />
        </>
      )}

      {/* CONTENT */}
      {step.content && (
        <>
          <SectionHeader label="content" />
          <div className="bg-[var(--bg-alt)] border border-[var(--border)] p-2 text-[12px] font-[family-name:var(--font-mono)] whitespace-pre-wrap max-h-[300px] overflow-y-auto text-[var(--text)]">
            {step.content}
          </div>
        </>
      )}

      {/* TOOL CALLS */}
      {step.tool_calls.length > 0 && (
        <>
          <SectionHeader label={`tool calls (${step.tool_calls.length})`} />
          <div className="flex flex-col gap-2">
            {step.tool_calls.map((tc) => {
              const obs = step.observations.find(
                (o) => o.source_call_id === tc.tool_call_id,
              );
              return (
                <ToolCallDetail
                  key={tc.tool_call_id}
                  toolCall={tc}
                  observation={obs ?? null}
                  compact
                />
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
