// verify-site-deploy.workflow.js
//
// A frozen JIT harness (see kb concept-jit-harnesses-generic): the planner (the
// agent running /deploy-site) scopes THIS run via args, the Workflow runtime
// executes deterministically, and ephemeral browser workers return against typed
// schemas. State lives in this artifact's variables, never in a reasoner's head.
//
// Run it (ultracode / Workflow tool):
//   Workflow({ scriptPath: '.../deploy-site/verify-site-deploy.workflow.js',
//              args: { baseUrl, changed, pagesOnly } })
//
// args (all optional):
//   baseUrl    string    default 'https://opentraces.ai' (use a preview URL to keep email off prod)
//   routes     string[]  override the route list
//   viewports  [{name,w,h}]  override the viewport list
//   changed    string[]  routes touched by THIS deploy -> classify-and-route: changed = deep
//                        check, others = smoke. Omit to deep-check everything.
//   pagesOnly  boolean   skip the email round-trip (read-only validation runs)
//   adminTokenEnv string env var holding the waitlist admin token (default WAITLIST_ADMIN_TOKEN)

export const meta = {
  name: 'verify-site-deploy',
  description: 'JIT harness: verify opentraces.ai across routes x viewports + email capture after a deploy',
  phases: [
    { title: 'Pages', detail: 'fan-out a browser worker per route x viewport (cheap model)' },
    { title: 'Confirm', detail: 'independent skeptic re-checks any flagged failure' },
    { title: 'Email', detail: 'waitlist round-trip + dedup + honeypot, then clean up' },
  ],
}

const BROWSER = '/opt/homebrew/bin/agent-browser'
const BASE = (args && args.baseUrl) || 'https://opentraces.ai'
const PAGES_ONLY = !!(args && args.pagesOnly)
const ADMIN_ENV = (args && args.adminTokenEnv) || 'WAITLIST_ADMIN_TOKEN'

const ROUTES = (args && args.routes) || [
  '/', '/explorer', '/hub', '/schema', '/docs',
  '/schema/0.6.0', '/docs/getting-started/installation',
  '/docs/cli/commands', '/docs/schema/trace-record',
]
const VIEWPORTS = (args && args.viewports) || [
  { name: 'desktop', w: 1440, h: 900 },
  { name: 'mobile', w: 390, h: 844 },
]

// --- Classify-and-route (deterministic control flow the runtime owns) --------
// If the planner passed `changed`, deep-check those routes and smoke the rest.
const changed = (args && args.changed) || null
const depthFor = (r) => (!changed ? 'deep' : changed.includes(r) ? 'deep' : 'smoke')

const PAGE_VERDICT = {
  type: 'object',
  required: ['route', 'viewport', 'httpOk', 'rendered', 'consoleClean', 'pass'],
  properties: {
    route: { type: 'string' },
    viewport: { type: 'string' },
    httpOk: { type: 'boolean' },
    rendered: { type: 'boolean' },
    consoleClean: { type: 'boolean' },
    layoutOk: { type: 'boolean' },
    pass: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
    evidence: { type: 'string', description: 'screenshot path or final URL' },
  },
}

const EMAIL_VERDICT = {
  type: 'object',
  required: ['roundTripOk', 'dedupOk', 'cleanedUp', 'pass'],
  properties: {
    roundTripOk: { type: 'boolean' },
    dedupOk: { type: 'boolean' },
    honeypotOk: { type: 'boolean' },
    cleanedUp: { type: 'boolean' },
    countBefore: { type: 'number' },
    countAfter: { type: 'number' },
    pass: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
  },
}

const pageWorker = (route, vp, depth, adversarial) => {
  const want = depth === 'deep'
    ? 'HTTP 200 (after redirects), primary content visible, NO layout overflow/clipping at this width, console has no errors, no network request failed, nav works'
    : 'HTTP 200 (after redirects) and the primary content renders (smoke check)'
  const tone = adversarial
    ? 'A previous worker reported this combination FAILED. Independently try to REPRODUCE the failure with a clean browser session. Only confirm pass=false if you can actually reproduce a real problem; if the page is fine, set pass=true and note the prior report was a false negative.'
    : ''
  return agent(
    `Use the agent-browser CLI at ${BROWSER} (run \`${BROWSER} --help\` if unsure of flags) to open ` +
    `${BASE}${route} headless at viewport ${vp.w}x${vp.h}. Verify: ${want}. ` +
    `Capture a screenshot and the final HTTP status + console. ${tone} ` +
    `Return the structured verdict; on failure list concrete, specific issues.`,
    {
      label: `${adversarial ? 'confirm' : 'page'}:${route}@${vp.name}`,
      phase: adversarial ? 'Confirm' : 'Pages',
      schema: PAGE_VERDICT,
      model: adversarial ? undefined : 'haiku', // route resources: cheap for volume, default for judgment
    }
  )
}

// --- Fan-out page workers, then independently confirm only the failures -------
phase('Pages')
const matrix = []
for (const r of ROUTES) for (const v of VIEWPORTS) matrix.push({ r, v, depth: depthFor(r) })
log(`Verifying ${matrix.length} page x viewport checks against ${BASE}`)

const pages = await pipeline(
  matrix,
  ({ r, v, depth }) => pageWorker(r, v, depth, false),
  (first, item) => (first && first.pass)
    ? first
    : pageWorker(item.r, item.v, item.depth, true), // adversarial confirm only on failure
)

// --- Email capture (independent verification of the write via admin read) -----
let email = null
if (!PAGES_ONLY) {
  phase('Email')
  email = await agent(
    `Use the agent-browser CLI at ${BROWSER} against ${BASE} to verify the early-access email form ` +
    `(renders on / and /hub; email input has aria-label "email address", submit is button.ea-btn, ` +
    `hidden honeypot is input[name=company]). ` +
    `1) Real round-trip on desktop: submit deploy-verify-<unique>@opentraces.ai, expect HTTP 200 {ok:true} + success UI; ` +
    `confirm it persisted via GET ${BASE}/api/waitlist with header "Authorization: Bearer $${ADMIN_ENV}" ` +
    `(record count before/after); then REMOVE that row from the store (Upstash ZREM) and re-read to confirm the ` +
    `count is restored (cleanedUp=true). ` +
    `2) Dedup: submitting the same address twice shows "already on the list", count unchanged. ` +
    `3) Honeypot/mobile: fill input[name=company] and submit at 390x844; expect success UI but NO new row. ` +
    `If /api/waitlist returns 503, set pass=false and note the store is unconfigured (BLOCKED, not a code bug). ` +
    `Return the structured verdict.`,
    { label: 'email-capture', phase: 'Email', schema: EMAIL_VERDICT }
  )
}

// --- Merge structured results (no model in the seam) --------------------------
const failures = pages.filter(Boolean).filter((p) => !p.pass)
  .map((p) => `${p.route}@${p.viewport}: ${(p.issues || []).join('; ') || 'failed'}`)
if (email && !email.pass) failures.push(`email-capture: ${(email.issues || []).join('; ') || 'failed'}`)
if (email && email.pass && email.cleanedUp === false) failures.push('email-capture: test row NOT cleaned up')

return {
  base: BASE,
  scoped: { routes: ROUTES.length, viewports: VIEWPORTS.length, checks: matrix.length, emailTested: !PAGES_ONLY, classifyAndRoute: !!changed },
  failures,
  allGreen: failures.length === 0,
}
