interface OnboardingProps {
  onLoadSample: () => void;
}

const WORKFLOW_LINES = [
  { cmd: "opentraces init", comment: "connect agents and create the repo inbox" },
  { cmd: "opentraces", comment: "open the terminal inbox" },
  { cmd: "opentraces web", comment: "open the browser inbox" },
  { cmd: "opentraces push", comment: "push committed traces to HuggingFace Hub" },
];

export function Onboarding({ onLoadSample }: OnboardingProps) {
  return (
    <div className="h-screen flex items-center justify-center bg-[var(--bg)]">
      <div className="max-w-[600px] w-full px-6 text-center">
        {/* brand */}
        <div
          className="text-[24px] font-bold tracking-tight mb-2"
          style={{ fontFamily: "var(--font-display)" }}
        >
          opentraces<span className="text-[var(--accent)]">.</span>ai
        </div>

        <div className="text-[13px] text-[var(--text-muted)] font-[family-name:var(--font-mono)] mb-8">
          the commons for agent traces
        </div>

        {/* terminal box */}
        <div className="border border-[var(--border)] bg-[var(--surface)] text-left px-5 py-4 mb-8">
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-dim)] font-[family-name:var(--font-mono)] mb-3">
            workflow
          </div>
          {WORKFLOW_LINES.map((line) => (
            <div
              key={line.cmd}
              className="font-[family-name:var(--font-mono)] text-[13px] leading-relaxed"
            >
              <span className="text-[var(--text-dim)]">$ </span>
              <span className="text-[var(--text)]">{line.cmd}</span>
              <span className="text-[var(--text-dim)] ml-4"># {line.comment}</span>
            </div>
          ))}
        </div>

        {/* actions */}
        <div className="flex items-center justify-center gap-4">
          <button
            onClick={onLoadSample}
            className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--accent)] border border-[var(--accent)] px-3 py-1 hover:bg-[var(--accent-bg)] transition-colors duration-100 cursor-pointer"
          >
            [load sample data]
          </button>
          <a
            href="https://opentraces.ai"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] font-[family-name:var(--font-mono)] text-[var(--text-muted)] border border-[var(--border)] px-3 py-1 hover:text-[var(--text)] hover:border-[var(--border-strong)] transition-colors duration-100"
          >
            [learn more]
          </a>
        </div>
      </div>
    </div>
  );
}
