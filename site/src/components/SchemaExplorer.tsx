import SectionRule from "./SectionRule";

export default function SchemaExplorer() {
  return (
    <section>
      <SectionRule label="schema" />
      <div className="section-title">TraceRecord</div>
      <p className="section-sub">
        The core unit of data. One session, one trace, one JSONL line.
      </p>

      <div className="schema-block">
        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{`{`}
{"\n"}  <span className="schema-key">&quot;trace_id&quot;</span>: <span className="schema-str">&quot;a4f2b8c1-...&quot;</span>,       <span className="schema-comment">// unique identifier</span>
{"\n"}  <span className="schema-key">&quot;session_id&quot;</span>: <span className="schema-str">&quot;sess_0x8f...&quot;</span>,     <span className="schema-comment">// agent session reference</span>
{"\n"}  <span className="schema-key">&quot;agent&quot;</span>: <span className="schema-str">&quot;claude-code&quot;</span>,           <span className="schema-comment">// agent identifier</span>
{"\n"}  <span className="schema-key">&quot;model&quot;</span>: <span className="schema-str">&quot;claude-sonnet-4&quot;</span>,       <span className="schema-comment">// model used</span>
{"\n"}  <span className="schema-key">&quot;steps&quot;</span>: [                          <span className="schema-comment">// conversation turns</span>
{"\n"}    {"{"} <span className="schema-key">&quot;role&quot;</span>: <span className="schema-str">&quot;user&quot;</span>, <span className="schema-key">&quot;content&quot;</span>: <span className="schema-str">&quot;...&quot;</span> {"}"},
{"\n"}    {"{"} <span className="schema-key">&quot;role&quot;</span>: <span className="schema-str">&quot;assistant&quot;</span>, <span className="schema-key">&quot;tool_calls&quot;</span>: [...] {"}"}
{"\n"}  ],
{"\n"}  <span className="schema-key">&quot;outcome&quot;</span>: {"{"} <span className="schema-key">&quot;success&quot;</span>: <span className="schema-type">true</span> {"}"},    <span className="schema-comment">// training signal</span>
{"\n"}  <span className="schema-key">&quot;tokens&quot;</span>: {"{"} <span className="schema-key">&quot;input&quot;</span>: <span className="schema-type">12400</span>, <span className="schema-key">&quot;output&quot;</span>: <span className="schema-type">3200</span> {"}"},
{"\n"}  <span className="schema-key">&quot;cost_usd&quot;</span>: <span className="schema-type">2.40</span>,
{"\n"}  <span className="schema-key">&quot;security_tier&quot;</span>: <span className="schema-str">&quot;automated&quot;</span>,
{"\n"}  <span className="schema-key">&quot;dependencies&quot;</span>: [<span className="schema-str">&quot;react&quot;</span>, <span className="schema-str">&quot;typescript&quot;</span>]
{"\n"}{`}`}</pre>
      </div>
    </section>
  );
}
