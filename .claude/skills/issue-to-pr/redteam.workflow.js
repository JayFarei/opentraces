// redteam.workflow.js
//
// The concrete codex-unavailable fallback for /issue-to-pr's adversarial gates
// (Stage 3b plan review, Stage 6 PR-diff review). When `mcp__codex__codex` is
// absent (headless / cron / no GPT), this stands in as the *external* adversary:
// a fan-out of independent skeptics, each told to BREAK the target, then a
// synthesizer that dedupes and ranks. Not as good as a different-vendor model,
// but a real gate — never a hand-wave (honesty, not theater).
//
// args: { kind: "plan" | "diff", target: "<plan file path | `git diff base...HEAD` text>",
//         repoRoot: "<worktree path>", base?: "<base branch, for kind=diff>" }

export const meta = {
  name: 'issue-to-pr-redteam',
  description: 'Codex-unavailable fallback: multi-agent adversarial review of a plan or a PR diff, then synthesize',
  phases: [
    { title: 'Attack', detail: 'independent skeptics each try to break the target' },
    { title: 'Synthesize', detail: 'dedupe + severity-rank into a verdict' },
  ],
}

let A = args
if (typeof A === 'string') { try { A = JSON.parse(A) } catch (_e) { A = {} } }
A = A || {}
const kind = A.kind === 'diff' ? 'diff' : 'plan'
const target = A.target || ''
const repoRoot = A.repoRoot || '.'
const base = A.base || 'main'
if (!target) {
  throw new Error('redteam.workflow: missing `target` (plan file path or diff spec) in args')
}

const FINDING_SCHEMA = {
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
        required: ['severity', 'title', 'why', 'fix'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          title: { type: 'string' },
          why: { type: 'string', description: 'concrete defect with file:line evidence from the real tree' },
          fix: { type: 'string' },
        },
      },
    },
  },
}

const SUBJECT = kind === 'diff'
  ? `the change on branch under review (inspect it with \`git -C ${repoRoot} diff ${base}...HEAD\` and read the touched files)`
  : `the implementation plan at ${target}`

const PRE = `You are an adversarial reviewer of ${SUBJECT}, in the opentraces repo at ${repoRoot}. Try to BREAK it by reading the REAL code — do not trust the ${kind}'s claims. Hunt for correctness, data-safety, regression, missed-surface, wrong-API/fictional-ref (landmine), and test-gap defects. Give file:line evidence for each. Default to skepticism. Return ONLY the structured object.`

phase('Attack')

const LENSES = kind === 'diff'
  ? [
      { key: 'correctness', p: 'LENS: CORRECTNESS — logic/off-by-one/edge cases; does it do what it claims; will it break existing behavior?' },
      { key: 'data-safety', p: 'LENS: DATA-SAFETY — could it delete/corrupt user data, clobber user files, leave a daemon running, or report success on partial failure?' },
      { key: 'regression', p: 'LENS: REGRESSION — frozen opentraces.*.v1 envelopes/schema, otbox journeys, surface-sweep snapshots, consumers of the touched code. What silently breaks?' },
      { key: 'tests', p: 'LENS: TEST INTEGRITY — does a test pass on the buggy code (green theater)? Is the acceptance covered by an otbox journey? Any unverified claim?' },
    ]
  : [
      { key: 'landmines', p: 'LENS: LANDMINES & API SHAPE — does the plan call functions/files/flags that exist with the cited shape? Verify each against the tree; a refuted ref is a blocker.' },
      { key: 'data-safety', p: 'LENS: DATA-SAFETY & ORDERING — data-loss paths, irreversible steps, ordering/race footguns, missing confirmations.' },
      { key: 'completeness', p: 'LENS: COMPLETENESS — missed surfaces/consumers the fix must also touch; blast radius the plan ignored; ACs it cannot meet.' },
      { key: 'tests', p: 'LENS: TEST PLAN — is the test red-before-green? Is there an otbox acceptance journey for any user/agent-observable change? Is completeness proven, not asserted?' },
    ]

const attacks = await parallel(
  LENSES.map((l) => () =>
    agent(`${PRE}\n\n${l.p}`, { label: `redteam:${l.key}`, phase: 'Attack', schema: FINDING_SCHEMA, effort: 'max', model: 'opus' })
  )
)

phase('Synthesize')
const clean = attacks.filter(Boolean)
const synthesis = await agent(
  `You are the red-team lead. Below are independent adversarial findings on ${SUBJECT}. Produce a consolidated report: BLOCKERS (must fix, each with file:line + fix), MAJOR, MINOR/NITS (bundled), and a verdict (APPROVE / APPROVE-WITH-FIXES / REJECT). Dedupe; rank by severity. Convergence between lenses on the same defect is the strongest signal. Cite file:line.\n\nFINDINGS:\n${JSON.stringify(clean, null, 2)}`,
  { label: 'redteam-synthesis', phase: 'Synthesize', effort: 'max', model: 'opus' }
)

return { kind, synthesis, raw: clean }
