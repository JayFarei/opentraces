import Link from "next/link";
import SectionRule from "./SectionRule";

export default function SchemaExplorer() {
  return (
    <section>
      <SectionRule label="schema" />
      <div className="section-title">TraceRecord</div>
      <p className="section-sub">
        One session, one JSONL line. <Link href="/schema" style={{ color: "var(--accent)" }}>Full schema docs {"\u2192"}</Link>
      </p>

      <div className="schema-block">
        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{`{`}
{"\n"}  <span className="schema-key">&quot;schema_version&quot;</span>: <span className="schema-str">&quot;0.1.0&quot;</span>,
{"\n"}  <span className="schema-key">&quot;trace_id&quot;</span>: <span className="schema-str">&quot;uuid&quot;</span>,
{"\n"}  <span className="schema-key">&quot;task&quot;</span>: {"{"} <span className="schema-key">&quot;description&quot;</span>: <span className="schema-str">&quot;Fix the failing test...&quot;</span>, <span className="schema-key">&quot;repository&quot;</span>: <span className="schema-str">&quot;owner/repo&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;agent&quot;</span>: {"{"} <span className="schema-key">&quot;name&quot;</span>: <span className="schema-str">&quot;claude-code&quot;</span>, <span className="schema-key">&quot;model&quot;</span>: <span className="schema-str">&quot;anthropic/claude-sonnet-4&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;steps&quot;</span>: [                                    <span className="schema-comment">{"// TAO loop"}</span>
{"\n"}    {"{"} <span className="schema-key">&quot;role&quot;</span>: <span className="schema-str">&quot;user&quot;</span>, <span className="schema-key">&quot;content&quot;</span>: <span className="schema-str">&quot;...&quot;</span> {"}"},
{"\n"}    {"{"} <span className="schema-key">&quot;role&quot;</span>: <span className="schema-str">&quot;agent&quot;</span>, <span className="schema-key">&quot;tool_calls&quot;</span>: [...], <span className="schema-key">&quot;reasoning_content&quot;</span>: <span className="schema-str">&quot;...&quot;</span> {"}"}
{"\n"}  ],
{"\n"}  <span className="schema-key">&quot;outcome&quot;</span>: {"{"} <span className="schema-key">&quot;success&quot;</span>: <span className="schema-type">true</span>, <span className="schema-key">&quot;committed&quot;</span>: <span className="schema-type">true</span>, <span className="schema-key">&quot;patch&quot;</span>: <span className="schema-str">&quot;...&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;attribution&quot;</span>: {"{"} <span className="schema-key">&quot;files&quot;</span>: [{"{"} <span className="schema-key">&quot;path&quot;</span>: <span className="schema-str">&quot;src/parser.ts&quot;</span>, <span className="schema-key">&quot;ranges&quot;</span>: [...] {"}"}] {"}"},
{"\n"}  <span className="schema-key">&quot;metrics&quot;</span>: {"{"} <span className="schema-key">&quot;total_steps&quot;</span>: <span className="schema-type">42</span>, <span className="schema-key">&quot;estimated_cost_usd&quot;</span>: <span className="schema-type">2.40</span> {"}"},
{"\n"}  <span className="schema-key">&quot;security&quot;</span>: {"{"} <span className="schema-key">&quot;tier&quot;</span>: <span className="schema-type">2</span> {"}"},
{"\n"}  <span className="schema-key">&quot;dependencies&quot;</span>: [<span className="schema-str">&quot;react&quot;</span>, <span className="schema-str">&quot;typescript&quot;</span>]
{"\n"}{`}`}</pre>
      </div>
    </section>
  );
}
