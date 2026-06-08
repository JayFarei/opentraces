interface TerminalProps {
  tabs: { label: string; active?: boolean }[];
  title?: string;
  children: React.ReactNode;
  /* Optional pane rendered inside the same frame, below the body, separated by
     a single divider rule — e.g. the live agent composer fused to the terminal. */
  footer?: React.ReactNode;
  onTabClick?: (index: number) => void;
}

export default function Terminal({ tabs, title, children, footer, onTabClick }: TerminalProps) {
  return (
    <div className="terminal">
      <div className="terminal-bar">
        <div className="terminal-bar-left">
          {tabs.map((tab, index) => (
            <span
              key={tab.label}
              className={`terminal-tab${tab.active ? " active" : ""}`}
              onClick={() => onTabClick?.(index)}
            >
              {tab.label}
            </span>
          ))}
        </div>
        {title && <span>{title}</span>}
      </div>
      <div className="terminal-body">
        {children}
      </div>
      {footer && <div className="terminal-footer">{footer}</div>}
    </div>
  );
}
