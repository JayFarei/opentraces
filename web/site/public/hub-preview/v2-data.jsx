// v2-data.jsx — code-verified mock data for the v40-aligned surfaces.
// Everything joins through the address grammar <trace>[:step|:A-B],
// carries a source facet, and labels carry verifier@version + method +
// trust standing.

// ── Verifier registry ───────────────────────────────────────────
const V2_VERIFIERS = [
  { name: "verify-clearance",  ver: "1.4.0", method: "deterministic", current: true,  job: "asserts no uncleared row crosses an egress door" },
  { name: "capsule-verify",    ver: "0.9.2", method: "deterministic", current: false, latest: "0.9.3", job: "byte-compares a reenacted bundle against its seal" },
  { name: "trail-assert",      ver: "2.2.1", method: "deterministic", current: true,  job: "asserts trail events against the append-only ledger" },
  { name: "json-purity",       ver: "1.1.0", method: "deterministic", current: true,  job: "parses stdout of every --json verb; anything non-JSON fails" },
  { name: "sop-judge",         ver: "2.1.0", method: "agent",         current: true,  job: "grades an SOP promise against the trail; capped provisional" },
];
const v2Verifier = (name) => V2_VERIFIERS.find(v => v.name === name);

// ── Scenarios (bench: scenario + verifier on a disposable box) ──
const V2_SCENARIOS = [
  { id: "scn-push-withheld",   name: "push a dataset containing one uncleared row",  digest: "a91f2c", steps: 6 },
  { id: "scn-seal-reenact",    name: "reenact a sealed capsule from its bundle",     digest: "77d0be", steps: 9 },
  { id: "scn-capture-load",    name: "capture under a saturated tool loop",          digest: "c34aa1", steps: 11 },
  { id: "scn-addr-resolve",    name: "resolve ctx for every step of a synced trace", digest: "5b8e97", steps: 4 },
  { id: "scn-slicing-tile",    name: "slice a 400-step trace at every grain",        digest: "e2c611", steps: 5 },
  { id: "scn-json-verbs",      name: "run every --json verb and parse stdout",       digest: "19fa44", steps: 8 },
  { id: "scn-watermark",       name: "publish, then diff remote against the ledger watermark", digest: "b0d3f8", steps: 7 },
];
const v2Scenario = (id) => V2_SCENARIOS.find(s => s.id === id);

// ── Boxes ───────────────────────────────────────────────────────
// Thin operational view: placement class, honest sandbox_tier (derived,
// never relabelled upward), lease lifecycle.
const V2_BOXES = [
  { id: "box-ct-7",  placement: "container", tier: "container", state: "leased",       lease: { holder: "run-b7d21", ttl: "11m 40s" }, recipe: "app_state@8c14e0", age: "22m", credits: 4.2 },
  { id: "box-mv-2",  placement: "remote",    tier: "microvm",   state: "provisioning", lease: { holder: "run-c0a44 (queued)", ttl: "—" }, recipe: "app_state@8c14e0", age: "40s", credits: 0.3 },
  { id: "box-lo-1",  placement: "local",     tier: "none",      state: "leased",       lease: { holder: "dev shell", ttl: "no lease" }, recipe: "working tree (not content-addressed)", age: "3h", credits: 0 },
  { id: "box-ct-3",  placement: "container", tier: "container", state: "reaped",       lease: { holder: "run-a9910 (done)", ttl: "expired" }, recipe: "app_state@41bb02", age: "2d", credits: 11.8 },
  { id: "box-ct-5",  placement: "container", tier: "jail",      state: "idle",         lease: { holder: "—", ttl: "—" }, recipe: "app_state@8c14e0", age: "1h", credits: 2.6 },
];

// ── Gyms — graded attempts against a task family (mock entry for UI) ──
const V2_GYMS = [
  {
    id: "gym-stacktrace-repair", name: "stacktrace-repair",
    job: "given a failing test's stack trace, repair the code until the suite is green",
    appState: "8c14e0", verifier: "trail-assert", tasks: 24,
    source: "harvested from 61 slices · parameterizer",
    attempts: 112, passRate: 0.38, lastRun: "6h", facet: "manufactured",
  },
];

// Credit spend on boxes — month to date, split by placement class.
const V2_BOX_CREDITS = {
  month: 68.4, budget: 200,
  byPlacement: [
    { k: "container", credits: 51.2 },
    { k: "remote / microvm", credits: 17.2 },
    { k: "local", credits: 0 },
  ],
  note: "Local boxes never bill. Reaped boxes stop billing at reap; the frozen run records stay free.",
};

// ── Box usage — billed by the hour, per environment ─────────────
// Each placement environment meters wall-clock lease hours at a flat
// hourly rate. Local never bills; it is metered for visibility only.
const V2_BOX_ENVS = [
  { k: "container", label: "container",       rate: 0.45, included: 120, color: "var(--c-user)" },
  { k: "microvm",   label: "remote / microvm", rate: 1.2,  included: 40,  color: "var(--c-read)" },
  { k: "local",     label: "local",            rate: 0,    included: null, color: "var(--fg-sub)" },
];
const V2_BOX_BUDGET = 200; // credits / month

// 30 days of leased hours per environment (Jun 12 – Jul 12).
const V2_BOX_HOURS = (() => {
  let s = 1337;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const days = [];
  const start = new Date(2026, 5, 12);
  for (let i = 0; i < 30; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const wd = d.getDay(), weekend = wd === 0 || wd === 6;
    const base = weekend ? 0.35 : 1;
    days.push({
      label: (d.getMonth() === 5 ? "Jun " : "Jul ") + d.getDate(),
      container: +((base * (1.1 + rnd() * 3.4)).toFixed(1)),
      microvm:   +((base * rnd() * 1.5).toFixed(1)),
      local:     +((0.2 + rnd() * 0.9).toFixed(1)),
      aiCredits: +((base * (4 + rnd() * 10)).toFixed(1)), // model spend that day
    });
  }
  return days;
})();

// ── Bench run records (opentraces.arena.run_record.v0) ──────────
// Every run freezes one record; the evidence entity renders it twice.
// Runs return to the bucket as manufactured record.
const V2_RUNS = [
  {
    id: "run-b7d21", kind: "bench", scenario: "scn-push-withheld", appState: "8c14e0",
    verifier: "verify-clearance", verdict: "pass", rc: 0, dur: "42s", when: "2h ago",
    box: "box-ct-7", tier: "container", facet: "manufactured",
    joined: "tr-9f2c1a:12-18", evidenceId: "ev-201",
    skips: [], redProof: { run: "run-b7d19", verdict: "fail", note: "verifier shown failing on pre-fix app_state@41bb02" },
  },
  {
    id: "run-b6f90", kind: "bench", scenario: "scn-watermark", appState: "8c14e0",
    verifier: "trail-assert", verdict: "pass", rc: 0, dur: "1m 08s", when: "3d ago",
    box: "box-ct-3", tier: "container", facet: "manufactured",
    joined: "tr-4d81be:last", evidenceId: "ev-198",
    skips: [{ name: "hf-remote-latency", reason: "remote timing assertion skipped — fixture remote, said out loud" }],
    redProof: { run: "run-b6f88", verdict: "fail", note: "watermark diff shown failing before the green" },
  },
  {
    id: "run-c0a44", kind: "bench", scenario: "scn-seal-reenact", appState: "8c14e0",
    verifier: "capsule-verify", verdict: "fail", rc: 3, dur: "2m 51s", when: "5h ago",
    box: "box-mv-2", tier: "microvm", facet: "manufactured",
    joined: null, evidenceId: "ev-202",
    skips: [], redProof: null,
    failNote: "rc=3 — reenacted trail diverges at step 7 (a product observation, not an infra error)",
  },
  {
    id: "run-a9910", kind: "bench", scenario: "scn-slicing-tile", appState: "41bb02",
    verifier: "trail-assert", verdict: "pass", rc: 0, dur: "18s", when: "6h ago",
    box: "box-ct-3", tier: "container", facet: "manufactured",
    joined: "tr-77aa03:1-142", evidenceId: "ev-199",
    skips: [], redProof: { run: "run-a9902", verdict: "fail", note: "tiling assert shown failing on overlap fixture" },
  },
  {
    id: "run-d5521", kind: "bench", scenario: "scn-json-verbs", appState: "8c14e0",
    verifier: "json-purity", verdict: "skip", rc: 10, dur: "9s", when: "1d ago",
    box: "box-ct-5", tier: "jail", facet: "manufactured",
    joined: null, evidenceId: "ev-196",
    skips: [{ name: "ot-bench-show", reason: "verb declared in manifest but endpoint-override seam missing — blocked_missing_surface" }],
    redProof: { run: "run-d5490", verdict: "fail", note: "purity parse shown failing on stderr bleed fixture" },
  },
  {
    id: "run-e1078", kind: "capsule", scenario: "scn-seal-reenact", appState: "sealed@cap-1",
    verifier: "capsule-verify", verdict: "pass", rc: 0, dur: "1m 22s", when: "4d ago",
    box: "box-ct-3", tier: "container", facet: "manufactured",
    joined: "tr-2b90cf:3-27", evidenceId: "ev-187",
    skips: [{ name: "live-provider-call", reason: "dependency mode recorded — provider replayed from seal, not called" }],
    redProof: { run: "run-e1071", verdict: "fail", note: "seal comparison shown failing on tampered bundle" },
  },
];
const v2Run = (id) => V2_RUNS.find(r => r.id === id);

// ── Atlas: one row per product guarantee, founder's altitude ────
// Six named row states; every hole is a named row state, never an absence.
const V2_GUARANTEES = [
  {
    id: "g-clearance", plain: "A pushed dataset never contains an uncleared row",
    tech: "clearance.push_gate", surfaces: ["dataset publish", "bucket sync push", "capsule share"],
    scenario: "scn-push-withheld", verifier: "verify-clearance",
    lastRun: "run-b7d21", state: "ok", freshness: { band: "fresh", label: "2h" },
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: true, rewatchable: true },
  },
  {
    id: "g-seal", plain: "A sealed replay reenacts byte-identical from its bundle",
    tech: "capsule.seal_integrity", surfaces: ["capsule seal", "capsule reenact"],
    scenario: "scn-seal-reenact", verifier: "capsule-verify",
    lastRun: "run-c0a44", state: "stale-verifier", freshness: { band: "aging", label: "5h · verifier 0.9.3 exists" },
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: false, rewatchable: true },
  },
  {
    id: "g-capture", plain: "Capture never blocks the agent's tool loop",
    tech: "capture.nonblocking", surfaces: ["trace capture"],
    scenario: "scn-capture-load", verifier: "trail-assert",
    lastRun: null, lastRunInline: { verdict: "pass", when: "9d ago", run: "run-88a10" },
    state: "stale-run", freshness: { band: "stale", label: "9d" },
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: true, rewatchable: false },
  },
  {
    id: "g-address", plain: "Every step of a synced trace resolves to an address",
    tech: "address.resolution", surfaces: ["trace get", "ctx", "trail"],
    scenario: "scn-addr-resolve", verifier: "trail-assert",
    lastRun: null, lastRunInline: { verdict: "pass", when: "8h ago", run: "run-77b02" },
    state: "no-red-proof", freshness: { band: "fresh", label: "8h" },
    redProofRef: null,
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: false, rewatchable: true },
  },
  {
    id: "g-idempotent", plain: "A private-home sync push is idempotent under retry",
    tech: "bucket.sync_idempotent", surfaces: ["bucket sync push"],
    scenario: null, verifier: null,
    lastRun: null, state: "unbound", freshness: null,
    chips: { blackBox: false, verifierIndependent: false, ledgerAsserted: false, redProofed: false, rewatchable: false },
  },
  {
    id: "g-tiling", plain: "Slicing tiles every step exactly once, at every grain",
    tech: "slicing.tiling", surfaces: ["slicers", "dataset rows"],
    scenario: "scn-slicing-tile", verifier: "trail-assert",
    lastRun: "run-a9910", state: "ok", freshness: { band: "fresh", label: "6h" },
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: true, rewatchable: true },
  },
  {
    id: "g-watermark", plain: "The remote reflects the ledger watermark after publish",
    tech: "dataset.watermark", surfaces: ["dataset publish", "dataset verify"],
    scenario: "scn-watermark", verifier: "trail-assert",
    lastRun: "run-b6f90", state: "unrepresentative-world", freshness: { band: "aging", label: "3d · fixture remote" },
    worldNote: "pinned app_state is a fixture bucket; production bucket shape has drifted",
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: true, rewatchable: true },
  },
  {
    id: "g-purity", plain: "Every --json verb emits pure JSON on stdout",
    tech: "cli.json_purity", surfaces: ["every CLI verb"],
    scenario: "scn-json-verbs", verifier: "json-purity",
    lastRun: "run-d5521", state: "surface-drift", freshness: { band: "aging", label: "1d" },
    driftNote: "PR #482 added `ot bench show` — verb is unmapped in the capability manifest",
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: false, redProofed: true, rewatchable: true },
  },
  // SOP-derived checks: an SOP is a promise + a verifier (method=agent,
  // capped provisional); a violation is a label. Lives here, not apart.
  {
    id: "g-sop-force", plain: "The agent never force-pushes over an unmerged branch",
    tech: "sop.no_force_push", surfaces: ["agent git ops"], sop: true,
    scenario: "scn-capture-load", verifier: "sop-judge",
    lastRun: null, lastRunInline: { verdict: "pass", when: "4h ago", run: "run-s2201" },
    state: "ok", freshness: { band: "fresh", label: "4h" }, standing: "provisional",
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: true, redProofed: true, rewatchable: false },
  },
  {
    id: "g-sop-secrets", plain: "Secrets never enter the agent's context",
    tech: "sop.no_secrets_in_ctx", surfaces: ["trace capture", "ctx"], sop: true,
    scenario: "scn-capture-load", verifier: "sop-judge",
    lastRun: null, lastRunInline: { verdict: "pass", when: "4h ago", run: "run-s2202" },
    state: "ok", freshness: { band: "fresh", label: "4h" }, standing: "provisional",
    chips: { blackBox: true, verifierIndependent: true, ledgerAsserted: false, redProofed: true, rewatchable: false },
  },
];

// Gates: change gate on a PR, release gate on the whole atlas.
const V2_GATES = {
  change: {
    pr: 482, title: "bench runner: lease + reap lifecycle",
    touched: ["g-purity", "g-clearance", "g-tiling"],
    drift: [{ surface: "ot bench show", note: "new verb, unmapped — surface-drift" }],
    verdict: "holds-with-drift",
  },
  release: {
    blocking: ["g-idempotent", "g-address", "g-seal", "g-capture", "g-watermark", "g-purity"],
    note: "6 of 10 rows are not fresh-and-proven. The release gate asserts the whole atlas.",
  },
};

// ── Evidence records — one frozen run record, two renderings ────
// The page renders for people; the feed renders for agents.
// Renderings format, never add.
function v2CastLine(tone, text) { return { tone, text }; }
const CL = v2CastLine;

const V2_EVIDENCE = [
  {
    id: "ev-201", run: "run-b7d21", title: "push a dataset containing one uncleared row",
    guarantee: "g-clearance", reads: { people: 3, agents: 11 }, unread: false,
    scorecard: [
      { check: "egress partition computed",        result: "pass", detail: "48 rows → pushed 47 · withheld 1 (1 unknown, conservatively withheld)" },
      { check: "withheld row absent from remote",  result: "pass", detail: "remote diff clean at watermark 8c14e0" },
      { check: "ledger records the partition",     result: "pass", detail: "TrailEvent egress.partition asserted" },
    ],
    rewatchable: true,
    annotations: [
      { who: "jayfarei", when: "1h ago", anchor: "t=31s", text: "the unknown row is the interesting one — predicate returned unknown on a half-scrubbed env var, and it withheld. exactly right." },
    ],
    cast: {
      dur: 42,
      frames: [
        { t: 0,  lines: [CL("cmd", "$ ot bench run scn-push-withheld --verifier verify-clearance@1.4.0 --json")] },
        { t: 2,  lines: [CL("dim", "box     lease box-ct-7 · placement container · sandbox_tier container (derived)")] },
        { t: 4,  lines: [CL("dim", "state   materialize app_state@8c14e0 · recipe verified")] },
        { t: 7,  lines: [CL("out", "── red proof ─ verifier must fail before its green is believed ──")] },
        { t: 9,  lines: [CL("cmd", "$ verify-clearance --against app_state@41bb02  # pre-fix state")] },
        { t: 12, lines: [CL("red", "FAIL  uncleared row r-0031 crossed dataset publish (expected)")] },
        { t: 14, lines: [CL("red", "FAIL  exit rc=1 · red proof recorded → run-b7d19")] },
        { t: 16, lines: [CL("out", "── scenario ─ 6 steps on app_state@8c14e0 ──")] },
        { t: 18, lines: [CL("dim", "step 1/6  seed bucket with 48 rows (1 uncleared, 1 unknown)")] },
        { t: 21, lines: [CL("dim", "step 2/6  ot dataset stage --from tr-9f2c1a:12-18")] },
        { t: 24, lines: [CL("dim", "step 3/6  ot dataset publish --strict")] },
        { t: 27, lines: [CL("out", "clearance  pushed 47 · withheld 1 (1 unknown → conservatively withheld)")] },
        { t: 30, lines: [CL("dim", "step 4/6  diff remote against ledger watermark")] },
        { t: 33, lines: [CL("dim", "step 5/6  assert TrailEvent egress.partition")] },
        { t: 35, lines: [CL("dim", "step 6/6  teardown · box reaped")] },
        { t: 38, lines: [CL("green", "PASS  verify-clearance@1.4.0 · rc=0 · 3/3 checks")] },
        { t: 41, lines: [CL("dim", "record  frozen → run-b7d21 · returned to bucket as manufactured record")] },
      ],
    },
  },
  {
    id: "ev-202", run: "run-c0a44", title: "reenact a sealed capsule from its bundle",
    guarantee: "g-seal", reads: { people: 1, agents: 4 }, unread: true,
    scorecard: [
      { check: "bundle unpacks under seal digest",  result: "pass", detail: "capsule.json digest matches seal" },
      { check: "reenacted trail matches sealed trail", result: "fail", detail: "divergence at step 7 — tool result ordering differs" },
      { check: "dependency modes honoured",          result: "pass", detail: "provider replayed from recording, never called live" },
    ],
    rewatchable: true,
    annotations: [],
    cast: {
      dur: 34,
      frames: [
        { t: 0,  lines: [CL("cmd", "$ ot bench run scn-seal-reenact --verifier capsule-verify@0.9.2 --json")] },
        { t: 2,  lines: [CL("dim", "box     lease box-mv-2 · placement remote · sandbox_tier microvm (derived)")] },
        { t: 5,  lines: [CL("out", "── red proof ── none on file for this pairing")] },
        { t: 7,  lines: [CL("red", "red_proof_ref: null — this green would not be believed; shown honestly")] },
        { t: 10, lines: [CL("out", "── scenario ─ 9 steps ──")] },
        { t: 13, lines: [CL("dim", "step 3/9  unpack bundle · seal digest ok")] },
        { t: 17, lines: [CL("dim", "step 5/9  reenact steps 1-6 · trail identical")] },
        { t: 22, lines: [CL("red", "step 7/9  trail diverges — tool result ordering differs from seal")] },
        { t: 27, lines: [CL("red", "FAIL  capsule-verify@0.9.2 · rc=3 (a product observation, not an infra error)")] },
        { t: 31, lines: [CL("dim", "record  frozen → run-c0a44 · returned to bucket as manufactured record")] },
      ],
    },
  },
  {
    id: "ev-198", run: "run-b6f90", title: "publish, then diff remote against the ledger watermark",
    guarantee: "g-watermark", reads: { people: 2, agents: 6 }, unread: false,
    scorecard: [
      { check: "watermark advances atomically", result: "pass", detail: "cursor 8c14e0 after publish" },
      { check: "remote diff clean",             result: "pass", detail: "0 rows differ" },
    ],
    rewatchable: false,
    annotations: [],
    cast: null,
  },
  {
    id: "ev-199", run: "run-a9910", title: "slice a 400-step trace at every grain",
    guarantee: "g-tiling", reads: { people: 0, agents: 9 }, unread: false,
    scorecard: [
      { check: "user-turn tiling",    result: "pass", detail: "142 steps · 0 gaps · 0 overlaps" },
      { check: "change-burst tiling", result: "pass", detail: "142 steps · 0 gaps · 0 overlaps" },
      { check: "milestone tiling",    result: "pass", detail: "142 steps · 0 gaps · 0 overlaps" },
      { check: "subgoal tiling",      result: "pass", detail: "142 steps · 0 gaps · 0 overlaps" },
    ],
    rewatchable: true,
    annotations: [],
    cast: {
      dur: 18,
      frames: [
        { t: 0,  lines: [CL("cmd", "$ ot bench run scn-slicing-tile --verifier trail-assert@2.2.1 --json")] },
        { t: 2,  lines: [CL("out", "── red proof ──")] },
        { t: 4,  lines: [CL("red", "FAIL  overlap fixture: steps 88-90 tiled twice (expected) → run-a9902")] },
        { t: 7,  lines: [CL("out", "── scenario ─ 4 grains × tr-77aa03:1-142 ──")] },
        { t: 10, lines: [CL("dim", "user-turn ✓ · change-burst ✓ · milestone ✓ · subgoal ✓")] },
        { t: 14, lines: [CL("green", "PASS  trail-assert@2.2.1 · rc=0 · 4/4 grains tile")] },
        { t: 17, lines: [CL("dim", "record  frozen → run-a9910")] },
      ],
    },
  },
  {
    id: "ev-196", run: "run-d5521", title: "run every --json verb and parse stdout",
    guarantee: "g-purity", reads: { people: 1, agents: 5 }, unread: true,
    scorecard: [
      { check: "14 verbs emit pure JSON", result: "pass", detail: "parse clean, stderr empty" },
      { check: "ot bench show",           result: "blocked_missing_surface", detail: "verb declared but endpoint-override seam missing — named, recorded, never silent" },
    ],
    rewatchable: true,
    annotations: [],
    cast: {
      dur: 9,
      frames: [
        { t: 0, lines: [CL("cmd", "$ ot bench run scn-json-verbs --verifier json-purity@1.1.0 --json")] },
        { t: 2, lines: [CL("red", "red proof: stderr-bleed fixture FAIL (expected) → run-d5490")] },
        { t: 4, lines: [CL("dim", "14/15 verbs parse clean")] },
        { t: 6, lines: [CL("out", "SKIP  ot bench show → blocked_missing_surface (endpoint-override seam)")] },
        { t: 8, lines: [CL("out", "verdict skip · rc=10 · skips are named and said out loud")] },
      ],
    },
  },
  {
    id: "ev-187", run: "run-e1078", title: "replay cap-7f3a9c2 — witness reenactment",
    guarantee: "g-seal", reads: { people: 4, agents: 2 }, unread: false,
    scorecard: [
      { check: "seal digest verified",  result: "pass", detail: "bundle intact" },
      { check: "trail reenacts byte-identical", result: "pass", detail: "27 steps replayed" },
    ],
    rewatchable: true,
    annotations: [
      { who: "maintainer-agent", when: "4d ago", anchor: "step 12", text: "re-posed with the recipient's key — this reenactment accrues their custody, not the sender's." },
    ],
    cast: {
      dur: 24,
      frames: [
        { t: 0,  lines: [CL("cmd", "$ ot capsule reenact cap_7f3a9c2 --on box-ct-3")] },
        { t: 3,  lines: [CL("red", "red proof: tampered-bundle fixture FAIL (expected) → run-e1071")] },
        { t: 6,  lines: [CL("dim", "dependency modes: provider=recorded · fs=sealed · net=omitted")] },
        { t: 11, lines: [CL("dim", "replaying steps 1-27 from seal…")] },
        { t: 18, lines: [CL("green", "PASS  capsule-verify@0.9.2 · byte-identical")] },
        { t: 22, lines: [CL("dim", "custody: reenactment accrued to recipient")] },
      ],
    },
  },
];
const v2Evidence = (id) => V2_EVIDENCE.find(e => e.id === id);

// The agent rendering: a feed digest (opentraces.evidence_feed.v0).
// Formats the frozen record — never recomputes, never editorializes.
function v2Feed(ev) {
  const run = v2Run(ev.run) || {};
  return {
    envelope: "opentraces.evidence_feed.v0",
    run_id: ev.run,
    kind: run.kind || "bench",
    verdict: run.verdict,
    rc: run.rc,
    scenario: run.scenario ? { id: run.scenario, digest: (v2Scenario(run.scenario) || {}).digest } : null,
    app_state: run.appState ? "app_state@" + run.appState : null,
    verifiers: run.verifier ? [{ name: run.verifier, version: (v2Verifier(run.verifier) || {}).ver, method: (v2Verifier(run.verifier) || {}).method }] : [],
    score: ev.scorecard.map(s => ({ check: s.check, result: s.result })),
    skips: (run.skips || []).map(s => ({ name: s.name, reason: s.reason })),
    red_proof_ref: run.redProof ? run.redProof.run : null,
    join: run.joined || null,
    source_facet: run.facet || "manufactured",
    sandbox_tier: run.tier,
    re_pose: { scenario: run.scenario, app_state: run.appState ? "app_state@" + run.appState : null, cmd: "ot bench run " + (run.scenario || "") + " --json" },
    rewatchable: !!ev.rewatchable,
  };
}

// ── World signals — the delayed second label ────────────────────
const V2_WORLD = [
  { id: "wf-1", subject: { kind: "commit",     ref: "c9d41f2" },                  observation: "reverted",    at: "3d ago",  source: "git survivorship" },
  { id: "wf-2", subject: { kind: "file:line",  ref: "src/capture/hook.py:88" },   observation: "transformed", at: "1d ago",  source: "git survivorship" },
  { id: "wf-3", subject: { kind: "commit",     ref: "8804c1a" },                  observation: "alive",       at: "6h ago",  source: "git survivorship" },
  { id: "wf-4", subject: { kind: "trace:step", ref: "tr-9f2c1a:14" },             observation: "alive",       at: "2h ago",  source: "git survivorship" },
  { id: "wf-5", subject: { kind: "file:line",  ref: "src/egress/predicate.py:41" }, observation: "moved",     at: "12h ago", source: "git survivorship" },
  { id: "wf-6", subject: { kind: "commit",     ref: "17aa93b" },                  observation: "lost",        at: "5d ago",  source: "git survivorship", note: "force-pushed away; trail retains the record" },
  { id: "wf-7", subject: { kind: "metric",     ref: "publish.p95_latency" },      observation: "—",           at: "horizon", source: "production metrics", horizon: true },
];

// The marquee: check said green, world said reverted. The most valuable
// thing the system mines — first-class review items.
const V2_DISAGREEMENTS = [
  {
    id: "dp-1", guarantee: "g-watermark",
    check: { run: "run-b6f90", verdict: "pass", when: "3d ago", verifier: "trail-assert" },
    world: { fact: "wf-1", said: "reverted", detail: "commit c9d41f2 (the fix the green stood on) was reverted 3d later" },
    status: "open",
  },
  {
    id: "dp-2", guarantee: "g-capture",
    check: { run: "run-88a10", verdict: "pass", when: "9d ago", verifier: "trail-assert" },
    world: { fact: "wf-2", said: "transformed", detail: "src/capture/hook.py:88 rewritten since the run — the green stands on code that no longer exists" },
    status: "open",
  },
];

// ── Labels — verifier@version + method + trust standing ─────────
const V2_LABELS = [
  { id: "lb-1",  subject: { kind: "run",   ref: "run-b7d21" },      verifier: "verify-clearance", value: "pass",                    status: "ok", standing: "scored",      facet: "manufactured", digest: "9a01ec" },
  { id: "lb-2",  subject: { kind: "run",   ref: "run-c0a44" },      verifier: "capsule-verify",   value: "fail",                    status: "ok", standing: "scored",      facet: "manufactured", digest: "44be07" },
  { id: "lb-3",  subject: { kind: "run",   ref: "run-d5521" },      verifier: "json-purity",      value: "skip",                    status: "blocked_missing_surface", standing: "scored", facet: "manufactured", digest: "c1d992" },
  { id: "lb-4",  subject: { kind: "trace", ref: "tr-9f2c1a" },      verifier: "sop-judge",        value: "no_violation",            status: "ok", standing: "provisional", facet: "observed",     digest: "7be410" },
  { id: "lb-5",  subject: { kind: "slice", ref: "tr-4d81be:22-31" }, verifier: "sop-judge",       value: "violation: force_push",   status: "ok", standing: "provisional", facet: "observed",     digest: "e07731" },
  { id: "lb-6",  subject: { kind: "run",   ref: "run-a9910" },      verifier: "trail-assert",     value: "pass",                    status: "ok", standing: "scored",      facet: "manufactured", digest: "50aa1f" },
  { id: "lb-7",  subject: { kind: "trace", ref: "tr-77aa03" },      verifier: "trail-assert",     value: "tiles_at_all_grains",     status: "ok", standing: "approved",    facet: "observed",     digest: "b2231d", approver: "jayfarei" },
  { id: "lb-8",  subject: { kind: "run",   ref: "run-e1078" },      verifier: "capsule-verify",   value: "pass",                    status: "ok", standing: "scored",      facet: "manufactured", digest: "1f8c04" },
  { id: "lb-9",  subject: { kind: "slice", ref: "tr-2b90cf:3-27" }, verifier: "sop-judge",        value: "no_violation",            status: "ok", standing: "provisional", facet: "observed",     digest: "88d1a0" },
  { id: "lb-10", subject: { kind: "commit", ref: "c9d41f2" },       verifier: "world:survivorship", value: "reverted",              status: "ok", standing: "scored",      facet: "observed",     digest: "d40b17", world: true },
];

Object.assign(window, {
  V2_VERIFIERS, v2Verifier, V2_SCENARIOS, v2Scenario, V2_BOXES,
  V2_RUNS, v2Run, V2_GUARANTEES, V2_GATES,
  V2_EVIDENCE, v2Evidence, v2Feed,
  V2_WORLD, V2_DISAGREEMENTS, V2_LABELS, V2_BOX_CREDITS, V2_GYMS,
});
