// pages-usage-meter.jsx — reusable usage meter card (from the credit-usage mock).
// Two instances live on the Boxes page: box credits (hourly leases) and
// AI credits (model usage). Animations: count-up %, tick sweep with hot
// leading edge, staggered row entrance, animated expand, refresh toast.

/* ── deterministic event data (90 days back from Jul 12 2026) ── */
const UM_DATA = (() => {
  let s = 4242;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
  const end = new Date(2026, 6, 12, 22, 0);
  const boxes = [
    { id: "box-ct-7", env: "container", rate: 0.45 },
    { id: "box-ct-3", env: "container", rate: 0.45 },
    { id: "box-ct-11", env: "container", rate: 0.45 },
    { id: "box-mv-2", env: "microvm", rate: 1.2 },
    { id: "box-mv-5", env: "microvm", rate: 1.2 },
    { id: "box-lo-1", env: "local", rate: 0 },
  ];
  const models = [
    { m: "claude-4.5-sonnet", w: 0.42, cr: 0.9 },
    { m: "claude-4.5-haiku", w: 0.2, cr: 0.25 },
    { m: "claude-4.5-opus", w: 0.1, cr: 3.2 },
    { m: "gpt-5.1", w: 0.18, cr: 1.1 },
    { m: "gemini-3-pro", w: 0.1, cr: 0.8 },
  ];
  const box = [], ai = [];
  for (let d = 0; d < 90; d++) {
    const day = new Date(end); day.setDate(end.getDate() - d);
    const wd = day.getDay(), weekend = wd === 0 || wd === 6;
    const nBox = weekend ? 1 : 2 + Math.floor(rnd() * 2);
    for (let i = 0; i < nBox; i++) {
      const b = pick(boxes);
      const hours = +(0.3 + rnd() * (b.env === "microvm" ? 1.6 : 3.4)).toFixed(1);
      const t = new Date(day); t.setHours(8 + Math.floor(rnd() * 13), Math.floor(rnd() * 60));
      box.push({ t: +t, box: b.id, env: b.env, hours, credits: +(hours * b.rate).toFixed(2) });
    }
    const nAi = weekend ? 2 : 3 + Math.floor(rnd() * 3);
    for (let i = 0; i < nAi; i++) {
      let r = rnd(), md = models[0];
      for (const m of models) { if (r < m.w) { md = m; break; } r -= m.w; }
      const tokens = Math.round((30 + rnd() * 640) * 1000);
      const t = new Date(day); t.setHours(8 + Math.floor(rnd() * 13), Math.floor(rnd() * 60));
      ai.push({ t: +t, model: md.m, tokens, credits: +((tokens / 1e6) * md.cr * (3 + rnd() * 3)).toFixed(2) });
    }
  }
  const desc = (a, b) => b.t - a.t;
  return { box: box.sort(desc), ai: ai.sort(desc) };
})();

/* ── formatters ── */
const umMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function umDate(ms) {
  const d = new Date(ms);
  let h = d.getHours(); const ap = h >= 12 ? "PM" : "AM"; h = h % 12 || 12;
  return umMonths[d.getMonth()] + " " + d.getDate() + ", " + String(h).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") + " " + ap;
}
const umTok = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : (n / 1e3).toFixed(1) + "K";
const umCr = (n) => n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2);

/* ── tweened number (count-up with decel easing) ── */
function useUmTween(target, nonce, dur) {
  const [v, setV] = React.useState(0);
  const [moving, setMoving] = React.useState(false);
  const fromRef = React.useRef(0);
  React.useEffect(() => {
    const from = fromRef.current, delta = target - from;
    if (Math.abs(delta) < 0.001) { setV(target); return; }
    setMoving(true);
    let raf; const t0 = performance.now(), D = dur || 900;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / D);
      const e = 1 - Math.pow(1 - p, 3);
      setV(from + delta * e);
      if (p < 1) raf = requestAnimationFrame(step);
      else { fromRef.current = target; setMoving(false); }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, nonce]);
  return [v, moving, fromRef];
}

/* ── animated height wrapper ── */
function UmExpand({ dep, children }) {
  const outer = React.useRef(null), inner = React.useRef(null);
  const first = React.useRef(true);
  React.useLayoutEffect(() => {
    const o = outer.current, i = inner.current;
    if (!o || !i) return;
    if (first.current) { first.current = false; o.style.height = "auto"; return; }
    const start = o.offsetHeight;
    o.style.height = start + "px";
    o.style.overflow = "hidden";
    const target = i.offsetHeight;
    requestAnimationFrame(() => { o.style.height = target + "px"; });
    const done = (e) => { if (e.target === o) { o.style.height = "auto"; o.style.overflow = "visible"; o.removeEventListener("transitionend", done); } };
    o.addEventListener("transitionend", done);
  }, [dep]);
  return <div className="um-expand" ref={outer}><div ref={inner}>{children}</div></div>;
}

/* ── dismiss-on-outside-click menu shell ── */
function UmPopover({ open, onClose, children, className }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open, onClose]);
  if (!open) return null;
  return <div className={"um-menu " + (className || "")} ref={ref}>{children}</div>;
}

const UmChevron = () => (
  <svg width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true">
    <path d="M1.5 3L4 5.5L6.5 3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"></path>
  </svg>
);
const UmCheck = () => (
  <svg className="check" width="9" height="9" viewBox="0 0 9 9" fill="none" aria-hidden="true">
    <path d="M1.5 4.5L3.5 6.5L7.5 2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"></path>
  </svg>
);

const UM_RANGES = [
  { k: "7d", label: "7 days", days: 7 },
  { k: "30d", label: "30 days", days: 30 },
  { k: "90d", label: "90 days", days: 90 },
  { k: "all", label: "All time", days: 90 },
];
const UM_NOW = +new Date(2026, 6, 12, 23, 59);

/* ── tick meter ── */
function UmTicks({ frac, moving }) {
  const N = 88;
  const head = Math.round(Math.max(0, Math.min(1, frac)) * N);
  const ticks = [];
  for (let i = 0; i < N; i++) {
    const on = i < head;
    const hot = moving && on && i >= head - 5;
    ticks.push(<span key={i} className="um-tick" data-on={on || undefined} data-hot={hot || undefined}></span>);
  }
  return <div className="um-ticks" role="img" aria-label={Math.round(frac * 100) + "% used"}>{ticks}</div>;
}

/* ── the card ── */
function UsageMeterCard({ eyebrow, accent, pool, toggleLabel, columns, cols, events, cells, csvName, footNote, big, onBig }) {
  const [range, setRange] = React.useState("30d");
  const [rangeOpen, setRangeOpen] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);
  const [perPage, setPerPage] = React.useState(8);
  const [perOpen, setPerOpen] = React.useState(false);
  const [page, setPage] = React.useState(0);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [toggled, setToggled] = React.useState(true);
  const [nonce, setNonce] = React.useState(0);
  const [toast, setToast] = React.useState(null);

  const rc = UM_RANGES.find(r => r.k === range);
  const winStart = UM_NOW - rc.days * 86400e3;
  const rows = React.useMemo(
    () => (range === "all" ? events : events.filter(e => e.t >= winStart)),
    [range, events]
  );
  const used = React.useMemo(() => rows.reduce((a, e) => a + e.credits, 0), [rows]);
  const allowance = pool * (rc.days / 30);
  const pct = Math.min(100, used / allowance * 100);

  const [animPct, moving] = useUmTween(pct, nonce, 900);
  const animUsed = animPct / 100 * allowance;

  const pages = Math.max(1, Math.ceil(rows.length / perPage));
  const safePage = Math.min(page, pages - 1);
  const visible = expanded ? rows.slice(safePage * perPage, safePage * perPage + perPage) : rows.slice(0, big ? 6 : 4);
  // container key changes only when the underlying data changes (range/page/refresh),
  // NOT on expand/collapse — so expanding just reveals rows without re-rendering them.
  const rowKey = range + "-" + safePage + "-" + perPage + "-" + nonce;

  const setRangeAnd = (k) => { setRange(k); setPage(0); setRangeOpen(false); };

  const doRefresh = () => {
    setMenuOpen(false);
    setNonce(n => n + 1);
    setToast("Usage data refreshed");
  };
  React.useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2400);
    return () => clearTimeout(t);
  }, [toast]);

  const doExport = () => {
    setMenuOpen(false);
    const head = columns.map(c => c.label).join(",");
    const body = rows.map(e => cells(e).map(c => String(c).replace(/,/g, "")).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([head + "\n" + body], { type: "text/csv" }));
    a.download = csvName + "-" + range + ".csv";
    a.click();
    setToast("Exported " + rows.length + " rows as CSV");
  };

  // pager window (max 5 numbers)
  const pageBtns = [];
  const lo = Math.max(0, Math.min(safePage - 2, pages - 5));
  for (let p = lo; p < Math.min(pages, lo + 5); p++) pageBtns.push(p);

  return (
    <div className="um-card" data-big={big || undefined} style={{ "--um-accent": accent, "--um-cols": cols }}>
      <div className="um-head">
        <div>
          <div className="um-eyebrow">{eyebrow}</div>
          <div className="um-pct">{animPct.toFixed(1)}<small>%</small></div>
        </div>
        <div className="um-toggle-wrap">
          <span className="um-toggle-label">{toggleLabel}</span>
          <button className="um-toggle" role="switch" aria-checked={toggled} onClick={() => setToggled(v => !v)} type="button">
            <span className="knob"></span>
          </button>
          {onBig && (
            <button className="um-icon-btn um-big-btn" type="button" aria-label={big ? "Shrink card" : "Enlarge card"} title={big ? "Shrink" : "Enlarge"} onClick={onBig}>
              {big ? (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M5 7H2M5 7v3M5 7L1.5 10.5M7 5h3M7 5V2M7 5l3.5-3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"></path></svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M7.5 1.5h3v3M10.5 1.5L6.8 5.2M4.5 10.5h-3v-3M1.5 10.5l3.7-3.7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"></path></svg>
              )}
            </button>
          )}
        </div>
      </div>

      <div className="um-meter">
        <UmTicks frac={animPct / 100} moving={moving} />
        <div className="um-meter-labels">
          <span><span className="lead">{umCr(animUsed)}</span> / {umCr(allowance)} cr allowance</span>
          <span>{umCr(Math.max(0, allowance - animUsed))} cr left</span>
        </div>
      </div>

      <div className="um-history">
        <div className="um-history-head">
          <span className="um-history-title">Usage history</span>
          <button className="um-chip" type="button" onClick={() => { setExpanded(v => !v); setPage(0); }}>
            {expanded ? "Show less" : "View all"}
          </button>
          <div className="um-select-wrap">
            <button className="um-select" type="button" aria-expanded={rangeOpen}
              onMouseDown={(e) => e.stopPropagation()} onClick={() => setRangeOpen(v => !v)}>
              {rc.label} <UmChevron />
            </button>
            <UmPopover open={rangeOpen} onClose={() => setRangeOpen(false)}>
              {UM_RANGES.map(r => (
                <button key={r.k} className="um-menu-item" data-active={r.k === range || undefined} type="button" onClick={() => setRangeAnd(r.k)}>
                  {r.label} <UmCheck />
                </button>
              ))}
            </UmPopover>
          </div>
        </div>

        <UmExpand dep={rowKey + "-" + expanded}>
          <div className="um-table">
            <div className="um-thead">
              {columns.map((c, i) => <span key={i} className={c.r ? "um-cell-r" : ""}>{c.label}</span>)}
            </div>
            <div key={rowKey}>
              {visible.map((e, i) => (
                <div key={e.t + "-" + (e.box || e.model) + "-" + i} className="um-row" style={{ "--d": (i * 35) + "ms" }}>
                  {cells(e).map((c, j) => (
                    <span key={j} className={(j === 0 ? "c-date" : j === 1 ? "c-main" : "c-num") + (columns[j].r ? " um-cell-r" : "")}>{c}</span>
                  ))}
                </div>
              ))}
            </div>
            {expanded && (
              <div className="um-pagination">
                <div className="um-perpage">
                  show per page
                  <div className="um-menu-wrap">
                    <button className="um-select" type="button" aria-expanded={perOpen}
                      onMouseDown={(e) => e.stopPropagation()} onClick={() => setPerOpen(v => !v)}>
                      {perPage} <UmChevron />
                    </button>
                    <UmPopover open={perOpen} onClose={() => setPerOpen(false)} className="from-left">
                      {[8, 12, 20].map(n => (
                        <button key={n} className="um-menu-item" data-active={n === perPage || undefined} type="button"
                          onClick={() => { setPerPage(n); setPage(0); setPerOpen(false); }}>
                          {n} rows <UmCheck />
                        </button>
                      ))}
                    </UmPopover>
                  </div>
                </div>
                <div className="um-pager">
                  <button className="um-page" type="button" disabled={safePage === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>‹</button>
                  {pageBtns.map(p => (
                    <button key={p} className="um-page" data-active={p === safePage || undefined} type="button" onClick={() => setPage(p)}>{p + 1}</button>
                  ))}
                  <button className="um-page" type="button" disabled={safePage >= pages - 1} onClick={() => setPage(p => Math.min(pages - 1, p + 1))}>›</button>
                </div>
              </div>
            )}
          </div>
        </UmExpand>
      </div>

      <div className="um-foot">
        <div className="um-menu-wrap">
          <button className="um-icon-btn" type="button" aria-label="More actions"
            onMouseDown={(e) => e.stopPropagation()} onClick={() => setMenuOpen(v => !v)}>
            <svg width="13" height="13" viewBox="0 0 13 13" fill="currentColor" aria-hidden="true">
              <circle cx="6.5" cy="2.5" r="1.2"></circle><circle cx="6.5" cy="6.5" r="1.2"></circle><circle cx="6.5" cy="10.5" r="1.2"></circle>
            </svg>
          </button>
          <UmPopover open={menuOpen} onClose={() => setMenuOpen(false)} className="from-left">
            <button className="um-menu-item" type="button" onClick={doExport}>
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none" aria-hidden="true"><path d="M5.5 1v6M3 5l2.5 2.5L8 5M1.5 9.5h8" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round"></path></svg>
              Export as CSV
            </button>
            <button className="um-menu-item" type="button" onClick={doRefresh}>
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none" aria-hidden="true"><path d="M9.5 5.5a4 4 0 1 1-1.2-2.85M9.5 1v2.5H7" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round"></path></svg>
              Refresh data
            </button>
            <button className="um-menu-item" type="button" onClick={() => setMenuOpen(false)}>
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none" aria-hidden="true"><circle cx="5.5" cy="5.5" r="4" stroke="currentColor" strokeWidth="1.1"></circle><path d="M5.5 5v3M5.5 3.2v.2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"></path></svg>
              Billing settings
            </button>
          </UmPopover>
        </div>
        <button className="um-icon-btn" type="button" aria-label="Export CSV" onClick={doExport}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true"><path d="M6.5 1.5v6.5M3.5 5.5l3 3 3-3M2 11.5h9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round"></path></svg>
        </button>
        <span className="um-foot-note"><span className="dot"></span>{footNote}</span>
        <div className="um-foot-actions">
          <button className="um-plan-btn ghost" type="button">Buy credits</button>
          <button className="um-plan-btn" type="button">Upgrade</button>
        </div>
      </div>

      {toast && <div className="um-toast"><span className="dot"></span>{toast}</div>}
    </div>
  );
}

/* ── the two instances used on the Boxes page ── */
function BoxUsageMeters({ children }) {
  const [big, setBig] = React.useState(null);
  const gridRef = React.useRef(null);
  const firstRects = React.useRef(null);
  const mk = (k) => () => {
    const g = gridRef.current;
    if (g) firstRects.current = [...g.children].map(el => el.getBoundingClientRect());
    setBig(v => (v === k ? null : k));
  };
  // FLIP: animate every tile from its previous rect to its new one,
  // so the resize is continuous and the other tiles glide into place.
  React.useLayoutEffect(() => {
    const g = gridRef.current, first = firstRects.current;
    firstRects.current = null;
    if (!g || !first) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const tiles = [...g.children];
    // cancel any in-flight FLIP so measurements are taken on the natural layout
    tiles.forEach(el => el.getAnimations().forEach(a => a.cancel()));
    // LOCK the grid tracks at their used sizes: animating width/height on a
    // grid item otherwise re-sizes the fr tracks every frame and the whole
    // grid (including the other tiles) dances under the animation
    const gcs = getComputedStyle(g);
    const lockCols = gcs.gridTemplateColumns, lockRows = gcs.gridTemplateRows;
    g.style.gridTemplateColumns = lockCols;
    g.style.gridTemplateRows = lockRows;
    // clip transient overflow while tiles fly, so scrollbars can't toggle
    // mid-animation and shift the layout under the measured rects
    g.classList.add("um-flip");
    let pending = 0;
    const done = () => {
      if (--pending === 0) {
        g.classList.remove("um-flip");
        g.style.gridTemplateColumns = "";
        g.style.gridTemplateRows = "";
      }
    };
    tiles.forEach((el, i) => {
      const f = first[i];
      if (!f) return;
      const l = el.getBoundingClientRect();
      const resized = Math.abs(f.width - l.width) > 1 || Math.abs(f.height - l.height) > 1;
      let dx = f.left - l.left, dy = f.top - l.top;
      if (resized) {
        // Inline width/height on a stretched grid item shifts the box inside
        // its cell (the browser centers the overflow). Probe the shift with
        // the start size applied and fold it into the translate — the shift
        // scales linearly with the interpolated size, so this is exact.
        const pw = el.style.width, ph = el.style.height;
        el.style.width = f.width + "px"; el.style.height = f.height + "px";
        const probe = el.getBoundingClientRect();
        el.style.width = pw; el.style.height = ph;
        dx = f.left - probe.left; dy = f.top - probe.top;
      }
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && !resized) return;
      const kf = resized
        ? [{ transform: "translate(" + dx + "px," + dy + "px)", width: f.width + "px", height: f.height + "px" },
           { transform: "translate(0px,0px)", width: l.width + "px", height: l.height + "px" }]
        : [{ transform: "translate(" + dx + "px," + dy + "px)" },
           { transform: "translate(0px,0px)" }];
      const prevOverflow = el.style.overflow, prevZ = el.style.zIndex;
      el.style.overflow = "hidden";
      el.style.zIndex = resized ? "5" : "1";
      pending++;
      const anim = el.animate(kf, { duration: 460, easing: "cubic-bezier(0.3, 0.85, 0.25, 1)" });
      anim.onfinish = anim.oncancel = () => {
        el.style.overflow = prevOverflow; el.style.zIndex = prevZ;
        done();
      };
    });
    if (pending === 0) {
      g.classList.remove("um-flip");
      g.style.gridTemplateColumns = "";
      g.style.gridTemplateRows = "";
    }
  }, [big]);
  return (
    <div className="um-grid" ref={gridRef} data-has-big={big ? "" : undefined}>
      <UsageMeterCard
        big={big === "box"}
        onBig={mk("box")}
        eyebrow="Box credits used"
        accent="var(--c-user)"
        pool={200}
        toggleLabel="Auto-reap idle boxes at limit"
        columns={[{ label: "Date" }, { label: "Box" }, { label: "Hours", r: true }, { label: "Credits", r: true }]}
        cols="1.4fr 1.7fr 0.7fr 0.8fr"
        events={UM_DATA.box}
        cells={(e) => [umDate(e.t), e.box + " · " + e.env, e.hours + " h", e.credits > 0 ? e.credits.toFixed(2) + " cr" : "—"]}
        csvName="box-usage"
        footNote="Boxes bill hourly, via Stripe"
      />
      <UsageMeterCard
        big={big === "ai"}
        onBig={mk("ai")}
        eyebrow="AI credits used"
        accent="var(--c-exec)"
        pool={400}
        toggleLabel="Auto-switch to cheaper model at limit"
        columns={[{ label: "Date" }, { label: "Model" }, { label: "Tokens", r: true }, { label: "Credits", r: true }]}
        cols="1.4fr 1.7fr 0.7fr 0.8fr"
        events={UM_DATA.ai}
        cells={(e) => [umDate(e.t), e.model, umTok(e.tokens), e.credits.toFixed(2) + " cr"]}
        csvName="ai-credits"
        footNote="Credit billing is via Stripe"
      />
      {children && <div className="um-chart-tile">{children}</div>}
    </div>
  );
}

Object.assign(window, { UsageMeterCard, BoxUsageMeters });
