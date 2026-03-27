import SectionRule from "./SectionRule";

const cases = [
  {
    tag: "sft",
    title: "Fine-tune on real workflows",
    desc: "Filter to outcome.success == true. Extract tool_call/tool_result pairs. Real data, not synthetic.",
  },
  {
    tag: "rl / rlhf",
    title: "Reward from outcomes",
    desc: "The outcome field signals success or failure. Build process and outcome reward models from trajectory pairs.",
  },
  {
    tag: "research",
    title: "Study agent behavior",
    desc: "How do developers use agents? Which tools are overused? Where are tokens wasted? The data answers.",
  },
  {
    tag: "orchestration",
    title: "Learn sub-agent patterns",
    desc: "parent_step hierarchy captures explore/plan/execute delegation strategies for training.",
  },
  {
    tag: "attribution",
    title: "Trace code to reasoning",
    desc: "Agent Trace says which lines are AI. opentraces says which conversation produced them.",
  },
  {
    tag: "ecosystems",
    title: "Filter by dependency",
    desc: '"All sessions using Stripe." Dependencies extracted and tagged. Domain-specific datasets.',
  },
];

export default function UseCases() {
  return (
    <section>
      <SectionRule label="use cases" />
      <div className="use-grid">
        {cases.map((c) => (
          <div key={c.tag} className="use-card">
            <div className="use-card-tag">{c.tag}</div>
            <h4>{c.title}</h4>
            <p>{c.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
