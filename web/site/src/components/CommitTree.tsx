// A small git commit graph shown under "what lasts / lineage": the session's
// commit (the bold node) survives, gets built on, and the history continues.
// Monochrome by design — the meaning is in the shape, not the color.

export default function CommitTree({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 240 34"
      className={`pipe-commit-svg${className ? " " + className : ""}`}
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      {/* main branch + "continues" arrowhead */}
      <path className="ct-line" d="M6 22 H 222" />
      <path className="ct-line" d="M222 22 l-5 -3.6 M222 22 l-5 3.6" />
      {/* a branch forks up and merges back — work built on the change */}
      <path className="ct-line" d="M64 22 C 78 22 80 8 94 8 H 138 C 152 8 154 22 168 22" />
      {/* commits — bold = the session's surviving commit, hollow = reverted */}
      <circle className="ct-node ct-node-strong" cx="20" cy="22" r="4.2" />
      <circle className="ct-node" cx="64" cy="22" r="3.4" />
      <circle className="ct-node" cx="94" cy="8" r="3.4" />
      <circle className="ct-node ct-node-revert" cx="138" cy="8" r="3.4" />
      <circle className="ct-node" cx="168" cy="22" r="3.4" />
      <circle className="ct-node" cx="202" cy="22" r="3.4" />
    </svg>
  );
}
