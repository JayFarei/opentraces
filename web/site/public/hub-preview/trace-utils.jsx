// Trace data helpers — classify steps, render messages, redact, etc.

// classify a single step (mirrors hub-next/src/lib/classify.ts)
const READ_CMDS = new Set(["ls","cat","grep","rg","ack","find","wc","head","tail","pwd","echo","which","stat","file","du","tree","printf","diff","cmp","test","uname","date","env"]);
const WRITE_CMDS = new Set(["mkdir","rmdir","rm","mv","cp","touch","chmod","chown","ln","patch"]);
const GIT_READ = new Set(["log","status","diff","show","blame","ls-files","rev-parse","branch","remote","config","shortlog","reflog"]);
const GIT_WRITE = new Set(["commit","add","checkout","switch","restore","reset","rebase","merge","tag","cherry-pick","revert","clean","stash","apply","am","mv","rm","notes","worktree"]);

function classifyBash(cmd) {
  if (!cmd) return "exec";
  if (/(^|\s)>>?\s+[^&\d\s]/.test(cmd) || /\|\s*tee(\s|$)/.test(cmd)) return "write";
  let c = cmd.replace(/^\s*cd\s+\S+\s*(&&|;)\s*/g, "");
  c = c.replace(/^(\w+=\S+\s+)+/, "");
  const tokens = c.trim().split(/\s+/);
  const first = (tokens[0] || "").split("/").pop() || "";
  if (first === "git") {
    const sub = tokens[1] || "";
    if (GIT_READ.has(sub)) return "read";
    if (GIT_WRITE.has(sub)) return "write";
    return "exec";
  }
  if (first === "awk" || first === "sed") {
    if (/\s-i(?:\s|$|[A-Za-z.])/.test(c) || /\s--in-place/.test(c)) return "write";
    return "read";
  }
  if (READ_CMDS.has(first)) return "read";
  if (WRITE_CMDS.has(first)) return "write";
  return "exec";
}

function classifyTool(tc) {
  const n = tc.tool_name;
  if (["Edit","Write","MultiEdit","NotebookEdit","TaskCreate","TaskUpdate"].includes(n)) return "write";
  if (["Read","Grep","Glob","ToolSearch","WebFetch","WebSearch"].includes(n)) return "read";
  if (["Skill","Agent","Task"].includes(n)) return "exec";
  if (n === "Bash") return classifyBash(tc.input?.command);
  return "exec";
}

const PRIORITY = { think: 0, read: 1, exec: 2, write: 3, plan: 4, user: 5 };

function classifyStep(step) {
  if (step.role === "user") return "user";
  const tcs = step.tool_calls || [];
  if (tcs.length === 0) {
    if (step.content && step.content.trim()) return "plan";
    return "think";
  }
  let best = "think";
  for (const tc of tcs) {
    const k = classifyTool(tc);
    if ((PRIORITY[k] || 0) > (PRIORITY[best] || 0)) best = k;
  }
  return best;
}

function summarizeToolCall(tc) {
  const n = tc.tool_name;
  const i = tc.input || {};
  const s = (v) => typeof v === "string" ? v : (v == null ? "" : String(v));
  if (n === "Read") {
    const fp = s(i.file_path) || s(i.path);
    return fp.split("/").slice(-3).join("/");
  }
  if (n === "Edit") return s(i.file_path).split("/").slice(-3).join("/");
  if (n === "Write") return s(i.file_path).split("/").slice(-3).join("/");
  if (n === "Grep") return `pattern "${s(i.pattern).slice(0, 60)}"`;
  if (n === "Glob") return s(i.pattern);
  if (n === "Bash") return s(i.description) || s(i.command).slice(0, 100);
  if (n === "TaskCreate") return "todo: " + s(i.title).slice(0, 70);
  if (n === "TaskUpdate") return s(i.status) + ": " + s(i.title).slice(0, 60);
  if (n === "WebFetch") return s(i.url).slice(0, 80);
  if (n === "WebSearch") return `"${s(i.query).slice(0, 60)}"`;
  if (n === "Skill") return s(i.name) || s(i.skill);
  return n;
}

function toolCategory(tc) {
  // for filter sidebar mapping
  const n = tc.tool_name;
  if (["Edit","Write","MultiEdit"].includes(n)) return "file_edits";
  if (n === "Bash") return "bash";
  if (n === "Read") return "read";
  return "other";
}

// Render content with redaction simulation. We deterministically pick spans
// to redact by tag based on hash of step+offset, so the same trace
// renders the same redactions every time.
function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

const REDACT_TAGS = ["PRIVATE PERSON", "PRIVATE CONTACT", "PRIVATE DATA", "SENSITIVE"];

function redactify(text, stepIdx) {
  // We pick "private-looking" spans by simple heuristics: file paths inside
  // /Users/<name>/, email-shaped tokens, and full names like "John Doe".
  // Each match gets replaced with a <span class="redact"> pill.
  if (!text) return [text];
  const parts = [];
  const re = /(\/Users\/[a-zA-Z0-9_-]+|\/home\/[a-zA-Z0-9_-]+|[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}|jayfarei|jay@\w+|06506792)/g;
  let last = 0;
  let m;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tag = REDACT_TAGS[hashStr(`${stepIdx}-${m.index}-${i}`) % REDACT_TAGS.length];
    parts.push({ redact: tag, key: `r${stepIdx}-${i}` });
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

// Quick markdown-ish formatting for content blocks
function renderRichText(text, stepIdx) {
  if (!text) return null;
  // Split into paragraphs by blank lines
  const paragraphs = text.split(/\n{2,}/);
  return paragraphs.map((para, pi) => {
    // Bullet lists
    const lines = para.split(/\n/);
    if (lines.every(l => /^\s*[-*•]\s/.test(l)) && lines.length > 1) {
      return (
        <ul key={pi}>
          {lines.map((l, li) => {
            const item = l.replace(/^\s*[-*•]\s/, "");
            return <li key={li}>{renderInline(item, stepIdx, `${pi}-${li}`)}</li>;
          })}
        </ul>
      );
    }
    return <p key={pi}>{renderInline(para, stepIdx, pi)}</p>;
  });
}

function renderInline(text, stepIdx, key) {
  // backtick code
  const out = [];
  const re = /`([^`]+)`/g;
  let last = 0; let m; let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      out.push(...renderWithRedaction(text.slice(last, m.index), stepIdx, `${key}-${i}t`));
    }
    out.push(<code key={`${key}-${i}c`}>{m[1]}</code>);
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) {
    out.push(...renderWithRedaction(text.slice(last), stepIdx, `${key}-end`));
  }
  return out;
}

function renderWithRedaction(text, stepIdx, key) {
  const parts = redactify(text, stepIdx);
  return parts.map((p, i) => {
    if (typeof p === "string") return <React.Fragment key={`${key}-${i}`}>{p}</React.Fragment>;
    return <span key={p.key} className="redact" title="Redacted by privacy filter">{p.redact}</span>;
  });
}

// First sentence / title for a content block
function firstLine(s) {
  if (!s) return "";
  const t = s.trim().split(/\n/)[0];
  return t.length > 110 ? t.slice(0, 110) + "…" : t;
}

window.classifyStep = classifyStep;
window.classifyTool = classifyTool;
window.summarizeToolCall = summarizeToolCall;
window.toolCategory = toolCategory;
window.renderRichText = renderRichText;
window.firstLine = firstLine;
window.redactify = redactify;

// ── Trail-derived helpers (ports of hub-next/src/lib) ──

function fmtTok(n) {
  if (!n || n === 0) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(Math.round(n));
}
function formatShort(s) {
  if (s == null) return "—";
  if (s < 1) return "0s";
  if (s < 60) return Math.round(s) + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  return (s / 3600).toFixed(1) + "h";
}
function truncate(str, n) {
  if (!str) return "";
  return str.length > n ? str.slice(0, n - 1) + "…" : str;
}

// Small action-color mapping (oklch tokens drive lane fills)
function actionColor(cls) {
  return `var(--c-${cls})`;
}

// Build per-step git snapshot info: which lane events fire on each step.
// We emulate hub-next/src/lib/gitline buildFileStages with a simpler model
// that derives directly from tool calls in the step.
function buildFileStages(trace) {
  const snapshots = [];
  const working = new Map(); // file -> "edited"
  let firstCommit = -1, lastCommit = -1, firstPush = -1, lastPush = -1;

  trace.steps.forEach((s, i) => {
    let event = "none";
    let pushed = false;
    let commitSubject = "";
    const tcs = s.tool_calls || [];

    for (const tc of tcs) {
      const n = tc.tool_name;
      const inp = tc.input || {};
      if (["Edit", "Write", "MultiEdit", "NotebookEdit"].includes(n)) {
        const fp = inp.file_path || inp.path;
        if (fp) working.set(fp, "edited");
        if (event === "none") event = "edit";
      } else if (n === "Bash") {
        const cmd = inp.command || "";
        if (/git\s+commit/.test(cmd)) {
          event = event === "push" ? "commit+push" : "commit";
          // Extract -m "msg"
          const m = cmd.match(/-m\s+["'](.+?)["']/);
          if (m) commitSubject = m[1];
          else commitSubject = inp.description || "commit";
          if (firstCommit < 0) firstCommit = i;
          lastCommit = i;
          working.clear();
        }
        if (/git\s+push/.test(cmd)) {
          pushed = true;
          event = event === "commit" ? "commit+push" : "push";
          if (firstPush < 0) firstPush = i;
          lastPush = i;
        }
      }
    }

    snapshots.push({
      event,
      pushed,
      commitSubject,
      working: new Map(working),
      local: firstCommit >= 0 ? new Array(i - firstCommit + 1).fill(0) : [],
      remote: firstPush >= 0 ? new Array(i - firstPush + 1).fill(0) : [],
    });
  });

  return { snapshots, ranges: { firstCommit, lastCommit, firstPush, lastPush } };
}

function trunkFor(index, first, last) {
  if (first < 0 || index < first || index > last) return "none";
  if (first === last) return "cap-single";
  if (index === first) return "cap-bottom";
  if (index === last) return "cap-top";
  return "mid";
}

// Per-step deltas for telemetry
function perStepTelemetry(trace) {
  const deltaIn = [], deltaOut = [], deltaSec = [], ctxPct = [];
  const t0 = trace.timestamp_start ? Date.parse(trace.timestamp_start) : 0;
  let prevT = t0;
  let prevIn = 0, prevOut = 0;
  trace.steps.forEach((s, i) => {
    const tu = s.token_usage || {};
    const inTok = (tu.input_tokens || 0) + (tu.cache_write_tokens || 0);
    const outTok = tu.output_tokens || 0;
    const tStr = s.timestamp || s.timestamp_start || trace.timestamp_start;
    const t = tStr ? Date.parse(tStr) : prevT;
    deltaIn.push(Math.max(0, inTok));
    deltaOut.push(Math.max(0, outTok));
    deltaSec.push(i === 0 ? 0 : Math.max(0, (t - prevT) / 1000));
    // ctx: prompt as % of inferred ceiling 200k
    const prompt = (tu.input_tokens || 0) + (tu.cache_read_tokens || 0);
    ctxPct.push(s.role === "user" ? null : (prompt > 0 ? (prompt / 200000) * 100 : null));
    prevT = t; prevIn = inTok; prevOut = outTok;
  });
  return { deltaIn, deltaOut, deltaSec, ctxPct };
}

// Extract a short SHA from a step's tool calls / observations
function extractSha(trace, i) {
  const s = trace.steps[i];
  const tcs = s.tool_calls || [];
  for (const tc of tcs) {
    if (tc.tool_name === "Bash") {
      const cmd = tc.input?.command || "";
      const m = cmd.match(/[0-9a-f]{7,40}/i);
      if (m) return m[0].slice(0, 7);
    }
  }
  // try observations
  const obs = s.observations || [];
  for (const o of obs) {
    const txt = o.content || o.output_summary || "";
    const m = String(txt).match(/[0-9a-f]{7}/i);
    if (m) return m[0];
  }
  return null;
}

window.fmtTok = fmtTok;
window.formatShort = formatShort;
window.truncate = truncate;
window.actionColor = actionColor;
window.buildFileStages = buildFileStages;
window.trunkFor = trunkFor;
window.perStepTelemetry = perStepTelemetry;
window.extractSha = extractSha;
