// Line icons for the "Beyond Training" consumer cards. All ride currentColor,
// stroke-based, 24px grid — consistent with the minimal/terminal aesthetic.

type IconProps = { className?: string };

const svg = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

// Trace Capsule — a sealed capsule you can share
export function CapsuleIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <g transform="rotate(45 12 12)">
        <rect x="2.5" y="9" width="19" height="6" rx="3" />
        <line x1="12" y1="9" x2="12" y2="15" />
      </g>
    </svg>
  );
}

// Skill Evaluation — a scoring target
export function EvalIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Standup — a daily sunrise
export function StandupIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <line x1="3" y1="18" x2="21" y2="18" />
      <path d="M7 18a5 5 0 0 1 10 0" />
      <line x1="12" y1="4" x2="12" y2="7" />
      <line x1="5" y1="9" x2="6.4" y2="10.4" />
      <line x1="19" y1="9" x2="17.6" y2="10.4" />
    </svg>
  );
}

// Spotlight — search
export function SpotlightIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <circle cx="11" cy="11" r="6.5" />
      <line x1="16" y1="16" x2="21" y2="21" />
    </svg>
  );
}

// Alerts — a bell
export function AlertIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <path d="M18 8.5a6 6 0 1 0-12 0c0 6.5-2.5 8.5-2.5 8.5h17S18 15 18 8.5" />
      <path d="M13.7 20.5a2 2 0 0 1-3.4 0" />
    </svg>
  );
}

// Intent Pull Request — a git pull request
export function PrIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <line x1="6" y1="8.5" x2="6" y2="15.5" />
      <path d="M13 6h2.5a2.5 2.5 0 0 1 2.5 2.5v7" />
    </svg>
  );
}
