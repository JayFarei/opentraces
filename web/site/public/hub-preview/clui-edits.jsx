// ─────────────────────────────────────────────────────────────
// CLUI live edits — typed-patch checkpoints (Agent Mode spec §4).
// A checkpoint is a TYPED PATCH, not a CSS blob:
//   { id, ts, author, class, target, op, rendered,
//     provenance: { capsule_ref, message_ref }, state, branch, change }
// States: checkpointed → kept; reverted checkpoints become
// TOMBSTONES (retained, not applied — "user tried X and undid it"
// constrains future re-derivation and enriches feedback capsules).
// The branch carries base_version — the hinge for re-enactment
// on upstream updates. Sealing packages kept history as an
// opentraces.capsule.v1 (kind hub_change) feedback capsule.
// Rendered CSS is injected via <style id="clui-live-edits">.
// ─────────────────────────────────────────────────────────────

window.OT_APP_VERSION = window.OT_APP_VERSION || "hub@1.4.2";

const CluiEdits = (() => {
  const KEY = "ot-clui-edits-v2";
  const LEGACY_KEY = "ot-clui-edits-v1";
  const subs = new Set();
  let checkpoints = [];
  let baseVersion = window.OT_APP_VERSION;

  // v2 load, with v1 migration (css blob → typed style patch)
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || "null");
    if (s && Array.isArray(s.checkpoints)) {
      checkpoints = s.checkpoints;
      baseVersion = s.base_version || baseVersion;
    } else {
      const v1 = JSON.parse(localStorage.getItem(LEGACY_KEY) || "[]");
      if (Array.isArray(v1)) {
        checkpoints = v1.filter(c => c && typeof c.css === "string").map(c => ({
          id: c.id || ("cp" + Math.random().toString(36).slice(2, 10)),
          ts: c.ts || Date.now(),
          author: "claude",
          class: "style",
          target: { surface: "app" },
          op: { css: c.css },
          rendered: c.css,
          provenance: {},
          state: c.kept ? "kept" : "checkpointed",
          branch: c.branch || "dev/edit",
          change: c.change || "edit",
        }));
      }
    }
  } catch (e) {}

  const isLive = (c) => c.state === "checkpointed" || c.state === "kept";

  function styleEl() {
    let el = document.getElementById("clui-live-edits");
    if (!el) {
      el = document.createElement("style");
      el.id = "clui-live-edits";
      document.head.appendChild(el);
    }
    return el;
  }
  function apply() {
    styleEl().textContent = checkpoints
      .filter(isLive)
      .map(c => "/* " + c.branch + " — " + c.change + " */\n" + (c.rendered || ""))
      .join("\n\n");
  }
  function persist() {
    try {
      localStorage.setItem(KEY, JSON.stringify({
        base_version: baseVersion,
        checkpoints: checkpoints.slice(-40),
      }));
    } catch (e) {}
  }
  function get() { return checkpoints.filter(isLive).map(c => ({ ...c })); }
  function emit() {
    persist(); apply();
    const snap = get();
    subs.forEach(f => { try { f(snap); } catch (e) {} });
  }

  apply(); // re-apply live checkpoints on load

  return {
    get,
    all() { return checkpoints.map(c => ({ ...c })); }, // incl. tombstones
    tombstones() { return checkpoints.filter(c => c.state === "tombstone").map(c => ({ ...c })); },
    baseVersion() { return baseVersion; },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    pendingBranch() {
      const p = checkpoints.filter(c => isLive(c) && c.state !== "kept");
      return p.length ? p[p.length - 1].branch : null;
    },
    // Apply a typed patch live + record a checkpoint. Back-compat:
    // accepts the old {branch, change, css} shape.
    add(input) {
      const { branch, change, css, cls, target, op, rendered, provenance } = input || {};
      const cp = {
        id: "cp" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        ts: Date.now(),
        author: "claude",
        class: cls || "style",
        target: target || { surface: "app" },
        op: op || { css: String(css || "") },
        rendered: rendered != null ? String(rendered) : String(css || ""),
        provenance: provenance || {},
        state: "checkpointed",
        branch: branch || "dev/edit",
        change: String(change || "edit"),
      };
      checkpoints.push(cp);
      emit();
      return cp;
    },
    // Roll back to checkpoint `id` (null = baseline). Later live
    // checkpoints become tombstones — retained as evidence.
    revertTo(id) {
      const live = checkpoints.filter(isLive);
      let cut;
      if (id == null) cut = -1;
      else {
        cut = live.findIndex(c => c.id === id);
        if (cut < 0) return 0;
      }
      const dead = new Set(live.slice(cut + 1).map(c => c.id));
      checkpoints = checkpoints.map(c => dead.has(c.id) ? { ...c, state: "tombstone", tombstonedAt: Date.now() } : c);
      emit();
      return dead.size;
    },
    // Keep, squashing this session's pending checkpoints into one
    // legible kept change when there are several.
    keepAll() {
      const pending = checkpoints.filter(c => c.state === "checkpointed");
      if (pending.length > 1) {
        const squashed = {
          id: "cp" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          ts: Date.now(),
          author: "claude",
          class: pending.every(c => c.class === pending[0].class) ? pending[0].class : "mixed",
          target: pending[0].target,
          op: { squashOf: pending.map(c => ({ change: c.change, op: c.op })) },
          rendered: pending.map(c => "/* " + c.change + " */\n" + (c.rendered || "")).join("\n"),
          provenance: pending[0].provenance || {},
          state: "kept",
          branch: pending[pending.length - 1].branch,
          change: pending.map(c => c.change).join(" · "),
          squashCount: pending.length,
        };
        const ids = new Set(pending.map(c => c.id));
        checkpoints = checkpoints.filter(c => !ids.has(c.id));
        checkpoints.push(squashed);
      } else {
        checkpoints = checkpoints.map(c => c.state === "checkpointed" ? { ...c, state: "kept" } : c);
      }
      emit();
    },
    discardPending() {
      const pending = checkpoints.filter(c => c.state === "checkpointed");
      checkpoints = checkpoints.map(c => c.state === "checkpointed" ? { ...c, state: "tombstone", tombstonedAt: Date.now() } : c);
      emit();
      return pending.length;
    },
    // Package the kept history + tombstones as a feedback capsule
    // (opentraces.capsule.v1, kind hub_change). Stored locally;
    // sharing/registry integration is Phase D+.
    sealAsCapsule({ sessions } = {}) {
      const kept = checkpoints.filter(c => c.state === "kept");
      if (!kept.length) return null;
      const capsule = {
        v: "opentraces.capsule.v1",
        kind: "hub_change",
        cid: "cap_" + Math.random().toString(36).slice(2, 9),
        title: "Hub personalization — " + kept.length + " change" + (kept.length > 1 ? "s" : ""),
        base_version: baseVersion,
        sealed_at: new Date().toISOString(),
        intent: kept.map(c => c.change).join("; "),
        checkpoints: kept.map(c => ({ ...c })),
        tombstones: checkpoints.filter(c => c.state === "tombstone").map(c => ({ id: c.id, change: c.change, class: c.class })),
        sessions: sessions || [],
      };
      try {
        const KEY_C = "ot-hub-change-capsules";
        const list = JSON.parse(localStorage.getItem(KEY_C) || "[]");
        list.unshift(capsule);
        localStorage.setItem(KEY_C, JSON.stringify(list.slice(0, 10)));
      } catch (e) {}
      return capsule;
    },
  };
})();

function cluiTimeAgo(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return "now";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

// Timeline popover: newest first, HEAD marked, ↩ reverts the app
// to how it looked at that checkpoint (later ones tombstone).
function CluiCheckpointTimeline({ checkpoints, onRevert, onClose, onSeal }) {
  const rows = checkpoints.slice().reverse();
  const keptCount = checkpoints.filter(c => c.state === "kept").length;
  return (
    <React.Fragment>
      <div className="clui-pop-backdrop" onClick={onClose}></div>
      <div className="clui-cp-pop clui-glass">
        <div className="cp-head">
          <span>Checkpoints — revert rolls the app back</span>
          <span className="cp-base-v mono" title="Branch base version">{CluiEdits.baseVersion()}</span>
        </div>
        {rows.map((c, i) => (
          <div className="cp-row" key={c.id} data-current={i === 0 ? "true" : "false"}>
            <span className="cp-dot" data-kept={c.state === "kept" ? "true" : "false"}></span>
            <span className="cp-txt" title={c.change}>
              {c.change}
              {c.squashCount > 1 && <span className="cp-squash mono"> ·{c.squashCount}</span>}
            </span>
            <span className="cp-cls mono">{c.class}</span>
            <span className="cp-ts mono">{cluiTimeAgo(c.ts)}</span>
            {i === 0 ? (
              <span className="cp-cur mono">HEAD</span>
            ) : (
              <button className="cp-revert" title="Revert to this checkpoint" onClick={() => onRevert(c.id)}>↩</button>
            )}
          </div>
        ))}
        <div className="cp-row cp-base">
          <span className="cp-dot" data-base="true"></span>
          <span className="cp-txt">baseline — before any edits</span>
          <button className="cp-revert" title="Revert everything" onClick={() => onRevert(null)}>↩</button>
        </div>
        {keptCount > 0 && onSeal && (
          <button className="cp-seal" title="Package kept changes + session as a hub_change feedback capsule for the upstream developer" onClick={onSeal}>
            ◈ Seal {keptCount} kept change{keptCount > 1 ? "s" : ""} as feedback capsule
          </button>
        )}
      </div>
    </React.Fragment>
  );
}

Object.assign(window, { CluiEdits, CluiCheckpointTimeline });
