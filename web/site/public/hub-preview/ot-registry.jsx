// ─────────────────────────────────────────────────────────────
// OtRegistry — the app describes itself to the agent.
//
// Two halves:
//  · ROUTES — every navigable surface, registered once by the app
//    shell with real open() handlers. The sidebar/agent/palette can
//    all read the same source of truth; the agent's site map and its
//    `navigate` tool are DERIVED from it, so a route registered here
//    is automatically operable by Claude with zero prompt edits.
//  · PAGE ACTIONS — within-page interactable elements, discovered
//    live from the DOM via data-agent-* annotations:
//      data-agent-action="unique-id"   (required)
//      data-agent-desc="what it does"  (recommended)
//      data-agent-mutates="true"       (asks user permission first)
//    Any annotated element anywhere in the app is instantly listable
//    and clickable by the agent — no registry edits needed.
// ─────────────────────────────────────────────────────────────

const OtRegistry = (() => {
  const routes = new Map();

  return {
    // route: { id, label, description, aliases?[], params?{name: "desc"},
    //          entities?: "query_hub kind that lists valid ids", open(params) }
    registerRoutes(list) {
      list.forEach(r => { if (r && r.id && typeof r.open === "function") routes.set(r.id, r); });
    },
    unregisterRoutes(ids) { ids.forEach(id => routes.delete(id)); },
    routes() { return Array.from(routes.values()); },
    hasRoute(id) { return routes.has(id); },

    // Resolve id or alias → route
    resolve(idOrAlias) {
      const q = String(idOrAlias || "").toLowerCase().trim();
      if (routes.has(q)) return routes.get(q);
      for (const r of routes.values()) {
        if ((r.aliases || []).some(a => a.toLowerCase() === q)) return r;
      }
      return null;
    },

    open(idOrAlias, params) {
      const r = this.resolve(idOrAlias);
      if (!r) {
        const known = Array.from(routes.keys()).join(", ");
        throw new Error("Unknown route: " + idOrAlias + ". Known routes: " + known);
      }
      const out = r.open(params || {});
      return out || ("Now on " + r.label);
    },

    // Human/agent-readable site map, generated — never hand-maintained.
    describe() {
      const lines = ["Site map — routes you can open with the navigate tool (id → what it is):"];
      for (const r of routes.values()) {
        let l = "· " + r.id;
        if (r.params && Object.keys(r.params).length) {
          l += " {" + Object.entries(r.params).map(([k, v]) => k + ": " + v).join(", ") + "}";
        }
        l += " — " + (r.description || r.label);
        if (r.aliases && r.aliases.length) l += " (aka " + r.aliases.join(", ") + ")";
        if (r.entities) l += " [ids via query_hub kind=" + r.entities + "]";
        lines.push(l);
      }
      return lines.join("\n");
    },
  };
})();

// ── DOM-discovered page actions ──
// Two tiers:
//  · annotated  — data-agent-action elements: curated id/desc, can be
//    marked data-agent-mutates to require user permission.
//  · discovered — ANY visible, labeled control (button/link/input) in
//    the main view. Id is auto-derived from its label, so every page
//    is agent-operable without hand-annotating 700 controls.
// Words that imply mutation gate discovered controls behind permission.
const OT_MUTATE_WORDS = /\b(create|new|add|delete|remove|log out|re-auth|save|publish|share|mint|run|connect|swap|report)\b/i;

function otActionLabel(el) {
  return (
    el.getAttribute("data-agent-desc") ||
    el.getAttribute("title") ||
    el.getAttribute("aria-label") ||
    (el.tagName === "INPUT" ? (el.getAttribute("placeholder") || "") : "") ||
    (el.textContent || "").trim().replace(/\s+/g, " ")
  ).trim().slice(0, 70);
}
function otSlug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
}
function otVisible(el) {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
// Chrome that isn't "the page": Claude island, eval harness, palette.
const OT_ACTION_EXCLUDE = ".clui-island, .clui-side, .oteval, .cmdk, [data-agent-ignore]";

const OtAgentActions = {
  _collect() {
    const seen = new Map(); // id → el
    // 1. curated annotations, anywhere in the app
    document.querySelectorAll("[data-agent-action]").forEach(el => {
      if (el.closest(OT_ACTION_EXCLUDE)) return;
      seen.set(el.getAttribute("data-agent-action"), { el, curated: true });
    });
    // 2. auto-discovered labeled controls in the main view + topbar + sidebar
    document.querySelectorAll("button, a[href], input, select, [role='button']").forEach(el => {
      if (el.closest(OT_ACTION_EXCLUDE) || el.hasAttribute("data-agent-action")) return;
      if (!otVisible(el)) return;
      const label = otActionLabel(el);
      if (!label || label.length < 2) return;
      let id = otSlug(label);
      if (!id) return;
      if (seen.has(id)) { let n = 2; while (seen.has(id + "-" + n)) n++; id = id + "-" + n; }
      seen.set(id, { el, curated: false });
    });
    return seen;
  },
  list() {
    const out = [];
    for (const [id, { el, curated }] of this._collect()) {
      const label = otActionLabel(el);
      out.push({
        id,
        description: label,
        kind: el.tagName === "INPUT" ? "input" : "button",
        mutates: el.getAttribute("data-agent-mutates") === "true" || (!curated && OT_MUTATE_WORDS.test(label)),
        curated,
        visible: otVisible(el),
      });
    }
    return out;
  },
  find(id) {
    const hit = this._collect().get(String(id));
    return hit ? hit.el : null;
  },
  // Type into a discovered input (search boxes etc.)
  type(id, text) {
    const el = this.find(id);
    if (!el || el.tagName !== "INPUT") throw new Error("No input '" + id + "' on the current view");
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(el, text);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    return "Typed \"" + text + "\" into " + id;
  },
  invoke(id) {
    const el = this.find(id);
    if (!el) {
      const known = this.list().filter(a => a.visible).slice(0, 40).map(a => a.id).join(", ");
      throw new Error("No action '" + id + "' on the current view. Visible actions: " + (known || "none"));
    }
    el.click();
    return "Clicked: " + (otActionLabel(el) || id);
  },
};

Object.assign(window, { OtRegistry, OtAgentActions });
