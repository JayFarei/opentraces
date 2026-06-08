// The canonical agent-trace minimap, lifted from the Hub trace viewer:
// uniform rounded pill segments, one per step, colored by action type, with a
// cursor. Deterministic (no Math.random) so server and client agree.

type Props = { className?: string; steps?: number; cursorAt?: number };

const CLASSES = ["user", "plan", "think", "read", "exec", "write"] as const;
// Distribution tuned to the read-heavy fingerprint shown in the Hub viewer.
const WEIGHTS = [0.06, 0.1, 0.13, 0.35, 0.16, 0.2];
const COLOR: Record<string, string> = {
  user: "var(--c-user)",
  plan: "var(--c-plan)",
  think: "var(--c-think)",
  read: "var(--c-read)",
  exec: "var(--c-exec)",
  write: "var(--c-write)",
};

export default function TraceMinimap({ className, steps = 96, cursorAt = 0.5 }: Props) {
  // Linear congruential generator with a fixed seed — same idea as the Hub's
  // fingerprint builder, but stable across renders.
  let seed = 1337;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    return seed / 0x7fffffff;
  };
  const segs = Array.from({ length: steps }, () => {
    let r = rnd();
    let c: string = "think";
    for (let j = 0; j < CLASSES.length; j++) {
      if (r < WEIGHTS[j]) {
        c = CLASSES[j];
        break;
      }
      r -= WEIGHTS[j];
    }
    return c;
  });

  return (
    <div className={`pipe-minimap${className ? " " + className : ""}`} aria-hidden="true">
      {segs.map((c, i) => (
        <span key={i} className="pipe-minimap-seg" style={{ background: COLOR[c] }} />
      ))}
      <span className="pipe-minimap-cursor" style={{ left: `${cursorAt * 100}%` }} />
    </div>
  );
}
