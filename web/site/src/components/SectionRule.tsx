export default function SectionRule({ label, pill }: { label: string; pill?: string }) {
  const dashes = "\u2500".repeat(Math.max(0, 40 - label.length));
  return (
    <h2 className="section-rule" role="heading" aria-level={2}>
      <span className="label">{label}</span>
      {pill && <span className="section-rule-pill">{pill}</span>}
      {" "}{dashes}
    </h2>
  );
}
