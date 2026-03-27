interface TerminalProps {
  tabs: { label: string; active?: boolean }[];
  title?: string;
  children: React.ReactNode;
}

export default function Terminal({ tabs, title, children }: TerminalProps) {
  return (
    <div className="terminal">
      <div className="terminal-bar">
        <div className="terminal-bar-left">
          {tabs.map((tab) => (
            <span key={tab.label} className={`terminal-tab${tab.active ? " active" : ""}`}>
              {tab.label}
            </span>
          ))}
        </div>
        {title && <span>{title}</span>}
      </div>
      <div className="terminal-body">
        {children}
      </div>
    </div>
  );
}
