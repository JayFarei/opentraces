export default function SectionRule({ label }: { label: string }) {
  const dashes = "\u2500".repeat(Math.max(0, 40 - label.length));
  return (
    <div className="section-rule">
      <span className="label">{label}</span>{" "}{dashes}
    </div>
  );
}
