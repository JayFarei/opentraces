// ─────────────────────────────────────────────────────────────
// OtCommands — the command bus (Agent Mode spec, Phase A).
// One registry of app commands with a permission taxonomy:
//   reads        — never ask
//   writes       — ask unless permission mode is auto
//   destructive  — always ask, even in auto
// UI handlers and agent tools both dispatch through it, so the
// permission policy hangs off the taxonomy, not word-matching.
// A rolling dispatch log feeds the agent-eval harness.
// ─────────────────────────────────────────────────────────────

const OtCommands = (() => {
  const cmds = new Map();
  const log = [];
  return {
    // command: { id, label, taxonomy: "reads"|"writes"|"destructive", run(params) }
    register(list) {
      list.forEach(c => { if (c && c.id && typeof c.run === "function") cmds.set(c.id, c); });
    },
    get(id) { return cmds.get(String(id)) || null; },
    list() { return Array.from(cmds.values()).map(({ run, ...meta }) => ({ ...meta })); },
    async dispatch(id, params) {
      const c = cmds.get(String(id));
      if (!c) throw new Error("Unknown command: " + id + ". Known: " + Array.from(cmds.keys()).join(", "));
      const out = await c.run(params || {});
      log.push({ id: c.id, taxonomy: c.taxonomy, params: params || {}, ts: Date.now() });
      if (log.length > 200) log.shift();
      return out || "done";
    },
    // For the eval harness: assert a command fired instead of DOM-sniffing
    log() { return log.slice(); },
    lastDispatch(id) {
      for (let i = log.length - 1; i >= 0; i--) if (log[i].id === id) return { ...log[i] };
      return null;
    },
  };
})();

Object.assign(window, { OtCommands });
