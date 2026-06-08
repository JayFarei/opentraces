// Unlabeled installs sparkline. Takes a raw time series (newest last) and
// renders a normalized line + faint area that stretches to fill its box.
// Deliberately axis-less: it conveys relative momentum, not absolute counts.

const VIEW_W = 240;
const VIEW_H = 56;
const PAD_Y = 6;

function buildPaths(values: number[]): { line: string; area: string } | null {
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = VIEW_W / (values.length - 1);

  const points = values.map((value, i) => {
    const x = i * stepX;
    const y = VIEW_H - PAD_Y - ((value - min) / span) * (VIEW_H - PAD_Y * 2);
    return [x, y] as const;
  });

  const line = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");

  const first = points[0];
  const last = points[points.length - 1];
  const area = `${line} L${last[0].toFixed(1)} ${VIEW_H} L${first[0].toFixed(1)} ${VIEW_H} Z`;

  return { line, area };
}

export default function InstallsSparkline({
  points,
  className,
}: {
  points: number[];
  className?: string;
}) {
  const paths = buildPaths(points);

  return (
    <svg
      className={`installs-spark${className ? ` ${className}` : ""}`}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      {paths ? (
        <>
          <path className="installs-spark-area" d={paths.area} />
          <path
            className="installs-spark-line"
            d={paths.line}
            fill="none"
            vectorEffect="non-scaling-stroke"
          />
        </>
      ) : (
        <path
          className="installs-spark-line installs-spark-empty"
          d={`M0 ${VIEW_H / 2} L${VIEW_W} ${VIEW_H / 2}`}
          fill="none"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
