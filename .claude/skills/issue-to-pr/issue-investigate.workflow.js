// issue-investigate.workflow.js
//
// Stage 1 of the /issue-to-pr skill: a frozen JIT harness (see kb
// concept-jit-harnesses-generic). The planner (the agent running /issue-to-pr)
// scopes THIS run via `args`; the Workflow runtime fans out a SMALL number of
// READ-ONLY Explore workers that return against typed schemas, then one
// synthesizer merges them. State lives in this artifact, never in a reasoner's
// head. Nothing here mutates the repo or calls an external service — it is the
// safe, parallel, evidence-gathering phase only.
//
// Belief it encodes: an issue is a hypothesis, not a spec. So we (a) find the
// REAL cause, (b) verify every claim the issue makes against the CURRENT tree
// (issues drift and cite things that no longer exist), and (c) map the blast
// radius before we plan a fix.
//
// args: { issue: number, title: string, body: string, repoRoot: string }

export const meta = {
  name: 'issue-investigate',
  description: 'Read-only multi-agent investigation of a GitHub issue: real cause, claim verification, blast radius',
  phases: [
    { title: 'Investigate', detail: 'parallel read-only lenses: cause / claim-verification / blast-radius' },
    { title: 'Synthesize', detail: 'merge into one verification report with a readiness verdict' },
  ],
}

// Defensive: `args` should arrive as a JSON object, but a large/quoted body
// can hit the harness footgun where the object is delivered JSON-STRINGIFIED.
// Parse a string form so the workflow is robust either way (a smoke run caught
// this: a multi-KB body with backticks arrived stringified -> args.issue was
// undefined -> the whole run investigated an empty "unknown" issue).
let A = args
if (typeof A === 'string') {
  try { A = JSON.parse(A) } catch (_e) { A = {} }
}
A = A || {}
// Fail fast: a missing issue/repoRoot would silently "investigate" an empty
// unknown issue and return a plausible-but-meaningless report. Refuse instead.
if (!A.issue || !A.repoRoot) {
  throw new Error(
    `issue-investigate.workflow: missing required args (issue=${A.issue ?? '<missing>'}, ` +
    `repoRoot=${A.repoRoot ?? '<missing>'}). Pass {issue, title, body, repoRoot} as a JSON object.`
  )
}
const issue = A.issue
const title = A.title || ''
const body = A.body || ''
const repoRoot = A.repoRoot

const CLAIM_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['lens', 'findings'],
  properties: {
    lens: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['statement', 'verdict', 'evidence'],
        properties: {
          statement: { type: 'string', description: 'the claim/cause/impact being asserted' },
          verdict: { type: 'string', enum: ['confirmed', 'drifted', 'refuted', 'not-found', 'risk'] },
          evidence: { type: 'string', description: 'actual current file:line + code excerpt proving the verdict' },
          corrected: { type: 'string', description: 'for drifted: the true current file:line / signature' },
        },
      },
    },
    notes: { type: 'array', items: { type: 'string' } },
  },
}

const PRE = `You are a read-only investigator for GitHub issue #${issue} ("${title}") of the opentraces repo at ${repoRoot}. Read the ACTUAL current code with Read/Grep — never trust the issue's assertions or line numbers; an issue is a hypothesis, and issues drift from the code and cite functions/paths that no longer exist. Always report the TRUE current file:line + a short code excerpt as evidence. Be terse and specific. Return ONLY the structured object.

WORK WITHIN A BOUNDED BUDGET. Target the surfaces the issue names and their immediate neighbors (callers, tests, docs); prefer \`grep\` to exhaustive full-file reads; ~15-25 targeted lookups is plenty. A thorough-but-bounded pass beats an exhaustive crawl of a large repo — surface your findings rather than chasing every tangent, and never read the whole tree.

ISSUE BODY:
${body}`

phase('Investigate')

const LENSES = [
  {
    key: 'cause',
    prompt: `${PRE}

LENS: ROOT CAUSE. Determine what ACTUALLY produces the behavior the issue describes — the real mechanism, not the issue's stated cause. Trace the code path with file:line evidence. If the behavior cannot be reproduced from the current code (already fixed / misdiagnosed / not a bug), say so explicitly with verdict "refuted" and the evidence. Each finding: statement = a candidate cause or mechanism; verdict = confirmed (this is the cause) / refuted (not the cause or not reproducible) / risk (contributing factor).`,
  },
  {
    key: 'claims',
    prompt: `${PRE}

LENS: CLAIM VERIFICATION. Extract EVERY concrete claim the issue makes — file paths, line numbers, function/method names, API shapes, flags, behaviors — and check each against the CURRENT tree. verdict: confirmed (exact) / drifted (right behavior, wrong location/detail — put the true current file:line in "corrected") / refuted (the cited function/behavior does NOT exist or is wrong — a LANDMINE the plan must not rely on) / not-found. This is the most important lens: the #85 issue cited two functions that did not exist. Be exhaustive about refuted/drifted claims.`,
  },
  {
    key: 'blast-radius',
    prompt: `${PRE}

LENS: BLAST RADIUS. Map what a fix would touch or could disturb: callers/consumers of the affected code; existing tests that cover it (and CLI surface-sweep / envelope-snapshot / otbox journey tests a change would trip); frozen consumer contracts (opentraces.*.v1 envelopes, the schema package, dataset/bucket layout); docs surfaces; and any cross-module coupling. Each finding: statement = a thing the fix must preserve or update; verdict = risk (could break) / confirmed (definitely in scope) / not-found. Flag anything that makes the fix bigger than it looks.`,
  },
]

const found = await parallel(
  LENSES.map((l) => () =>
    // Model policy: the read-only grep-and-verify lenses are the cheapest,
    // highest-fan-out work in the pipeline -> latest Sonnet. effort: high
    // (read-only evidence-gathering); the wall-clock bound is the BUDGET
    // instruction in the prompt, not a low effort tier. The judgment-bearing
    // synthesis below runs on Opus; planning + adversarial run at max in the
    // main loop on Opus.
    agent(l.prompt, { label: `investigate:${l.key}`, phase: 'Investigate', schema: CLAIM_SCHEMA, agentType: 'Explore', effort: 'high', model: 'sonnet' })
  )
)

phase('Synthesize')

const clean = found.filter(Boolean)
const merged = JSON.stringify(clean, null, 2)

const report = await agent(
  `You are the synthesis agent for the /issue-to-pr investigation of issue #${issue} ("${title}"). Below are findings from three read-only lenses (cause, claim-verification, blast-radius) against the opentraces repo at ${repoRoot}. Produce a single consolidated VERIFICATION REPORT in markdown:

1. **Real root cause** — the verified mechanism with file:line (state plainly if the issue is invalid / already-fixed / misdiagnosed — that is a valid terminal finding).
2. **Confirmed claims** — terse.
3. **Drifted claims** — with the CORRECTED current file:line for each (the implementation must use these, not the issue's stale refs).
4. **Refuted / landmine claims** — call out loudly anything the issue gets WRONG; the plan must NOT rely on these.
5. **Blast radius** — what a fix must preserve or update (tests to expect to trip, consumer contracts, docs).
6. **Readiness verdict** — is the issue actionable as-is, and what must be re-derived before coding? If not actionable, say why with evidence.

Be specific with file:line; only use the findings below; do not invent.

FINDINGS JSON:
${merged}`,
  // Judgment-bearing (the readiness verdict that gates planning) -> Opus.
  { label: 'synthesize-report', phase: 'Synthesize', model: 'opus' }
)

return { issue, report, raw: clean }
