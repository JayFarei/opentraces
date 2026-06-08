// Line icons for the agent-session story: sees → does → changes → lasts → lineage.
// Stroke-based, currentColor, 24px grid — monochrome by design.

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

// What it sees — an eye (context)
export function EyeIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z" />
      <circle cx="12" cy="12" r="2.5" />
    </svg>
  );
}

// What it changes — an edit/pencil
export function EditIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" />
    </svg>
  );
}

// What lasts — an anchor (persists past the session)
export function AnchorIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <circle cx="12" cy="5" r="2.5" />
      <line x1="12" y1="22" x2="12" y2="7.5" />
      <path d="M5 12H2a10 10 0 0 0 20 0h-3" />
    </svg>
  );
}

// Lineage — a branch (piled on or reverted over time)
export function LineageIcon({ className }: IconProps) {
  return (
    <svg className={className} {...svg}>
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <path d="M18 8.5a9 9 0 0 1-9 9" />
    </svg>
  );
}
