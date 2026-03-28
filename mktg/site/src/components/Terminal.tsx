interface TerminalProps {
  tabs: { label: string; active?: boolean }[];
  title?: string;
  children: React.ReactNode;
  onTabClick?: (index: number) => void;
}

export default function Terminal({ tabs, title, children, onTabClick }: TerminalProps) {
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
    </div>
  );
}
