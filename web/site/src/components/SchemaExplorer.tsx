import Link from "next/link";
import SectionRule from "./SectionRule";
import { latestVersion } from "@/lib/schema-versions";

export default function SchemaExplorer() {
  return (
    <section>
      <SectionRule label="schema" />
      <div className="section-title">TraceRecord</div>
      <p className="section-sub">
        One trace spine, plus bucket companions for trail, context, and source
        evidence. Dataset rows are workflow projections over this record. <Link href="/schema" style={{ color: "var(--accent)" }}>Full schema docs {"\u2192"}</Link>
      </p>

      <div className="schema-block">
        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{`{`}
{"\n"}  <span className="schema-key">&quot;schema_version&quot;</span>: <span className="schema-str">&quot;{latestVersion}&quot;</span>,
{"\n"}  <span className="schema-key">&quot;trace_id&quot;</span>: <span className="schema-str">&quot;uuid&quot;</span>,
{"\n"}  <span className="schema-key">&quot;execution_context&quot;</span>: <span className="schema-str">&quot;devtime&quot;</span>,
{"\n"}  <span className="schema-key">&quot;task&quot;</span>: {"{"} <span className="schema-key">&quot;description&quot;</span>: <span className="schema-str">&quot;Fix the failing test...&quot;</span>, <span className="schema-key">&quot;repository&quot;</span>: <span className="schema-str">&quot;owner/repo&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;agent&quot;</span>: {"{"} <span className="schema-key">&quot;name&quot;</span>: <span className="schema-str">&quot;claude-code&quot;</span>, <span className="schema-key">&quot;model&quot;</span>: <span className="schema-str">&quot;anthropic/claude-sonnet-4&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;steps&quot;</span>: [                                    <span className="schema-comment">{"// TAO loop"}</span>
{"\n"}    {"{"} <span className="schema-key">&quot;role&quot;</span>: <span className="schema-str">&quot;user&quot;</span>, <span className="schema-key">&quot;content&quot;</span>: <span className="schema-str">&quot;...&quot;</span> {"}"},
{"\n"}    {"{"} <span className="schema-key">&quot;role&quot;</span>: <span className="schema-str">&quot;agent&quot;</span>, <span className="schema-key">&quot;tool_calls&quot;</span>: [...], <span className="schema-key">&quot;context_node_id&quot;</span>: <span className="schema-str">&quot;sha256:...&quot;</span> {"}"}
{"\n"}  ],
{"\n"}  <span className="schema-key">&quot;outcome&quot;</span>: {"{"} <span className="schema-key">&quot;success&quot;</span>: <span className="schema-type">true</span>, <span className="schema-key">&quot;committed&quot;</span>: <span className="schema-type">true</span>, <span className="schema-key">&quot;commit_sha&quot;</span>: <span className="schema-str">&quot;abc123&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;patches&quot;</span>: [{"{"} <span className="schema-key">&quot;patch_id&quot;</span>: <span className="schema-str">&quot;tracepatch-sha256:...&quot;</span>, <span className="schema-key">&quot;file_path&quot;</span>: <span className="schema-str">&quot;src/parser.ts&quot;</span>, <span className="schema-key">&quot;step_index&quot;</span>: <span className="schema-type">7</span> {"}"}],
{"\n"}  <span className="schema-key">&quot;context_tree_summary&quot;</span>: {"{"} <span className="schema-key">&quot;node_count&quot;</span>: <span className="schema-type">18</span>, <span className="schema-key">&quot;active_path_leaf_id&quot;</span>: <span className="schema-str">&quot;sha256:...&quot;</span> {"}"},
{"\n"}  <span className="schema-key">&quot;attribution&quot;</span>: {"{"} <span className="schema-key">&quot;files&quot;</span>: [{"{"} <span className="schema-key">&quot;path&quot;</span>: <span className="schema-str">&quot;src/parser.ts&quot;</span>, <span className="schema-key">&quot;ranges&quot;</span>: [...] {"}"}] {"}"},
{"\n"}  <span className="schema-key">&quot;metrics&quot;</span>: {"{"} <span className="schema-key">&quot;total_steps&quot;</span>: <span className="schema-type">42</span>, <span className="schema-key">&quot;estimated_cost_usd&quot;</span>: <span className="schema-type">2.40</span> {"}"},
{"\n"}  <span className="schema-key">&quot;security&quot;</span>: {"{"} <span className="schema-key">&quot;scanned&quot;</span>: <span className="schema-type">true</span>, <span className="schema-key">&quot;redactions_applied&quot;</span>: <span className="schema-type">2</span> {"}"},
{"\n"}  <span className="schema-key">&quot;metadata&quot;</span>: {"{"} <span className="schema-key">&quot;security&quot;</span>: {"{"} <span className="schema-key">&quot;tools_applied&quot;</span>: [<span className="schema-str">&quot;regex&quot;</span>, <span className="schema-str">&quot;entropy&quot;</span>] {"}"} {"}"},
{"\n"}  <span className="schema-key">&quot;dependencies&quot;</span>: [<span className="schema-str">&quot;react&quot;</span>, <span className="schema-str">&quot;typescript&quot;</span>]
{"\n"}{`}`}</pre>
      </div>
    </section>
  );
}
