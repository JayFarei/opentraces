export default function SectionRule({ label }: { label: string }) {
  const dashes = "\u2500".repeat(Math.max(0, 40 - label.length));
  return (
    <h2 className="section-rule" role="heading" aria-level={2}>
      <span className="label">{label}</span>{" "}{dashes}
    </h2>
  );
}
