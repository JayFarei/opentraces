"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./TraceSlicerCompare.module.css";
import {
  SLICER_DEMO,
  SLICER_META,
  ACTION_LEGEND,
  type DemoTrace,
  type SlicerKey,
} from "@/lib/slicer-demo";

// Logical canvas width — matches HubFeatureFrame's CANVAS_W so this native
// component renders at the SAME scale as the embedded iframe examples around it
// on /hub (a 1020px design scaled to fit the tour column), instead of at native
// 1:1 where its elements read noticeably chunkier than its neighbours.
const CANVAS_W = 1020;
// Below this the scaled design is unreadable → fall back to the component's own
// responsive (stacked) layout at full width, like the rest of the page.
const MOBILE_BP = 760;

const CODE_TO_ACTION: Record<string, string> = {
  u: "user",
  p: "plan",
  t: "think",
  r: "read",
  e: "exec",
  w: "write",
};

function Pipe({ trace }: { trace: DemoTrace }) {
  return (
    <div className={styles.pipe} aria-hidden>
      {trace.steps.split("").map((code, i) => {
        const action = CODE_TO_ACTION[code] ?? "think";
        return (
          <i
            key={i}
            className={styles.pp}
            style={{ background: `var(--c-${action})` }}
            title={`step ${i}: ${action}`}
          />
        );
      })}
    </div>
  );
}

function SlicerBar({ trace, sliceKey, sig }: { trace: DemoTrace; sliceKey: SlicerKey; sig: string }) {
  const slices = trace.slicers[sliceKey];
  return (
    <div className={styles.bar2} style={{ ["--sig" as string]: `var(--c-${sig})` }}>
      {slices.map((s, i) => {
        const n = s.e - s.s + 1;
        return (
          <div
            key={i}
            className={`${styles.seg}${i % 2 ? " " + styles.segAlt : ""}`}
            style={{ flexGrow: n }}
            title={`S${i}  steps ${s.s}–${s.e} (${n})  ${s.k}: ${s.l}`}
          >
            <span className={styles.sidx}>S{i}</span>
            <span className={styles.slbl}>{s.l}</span>
          </div>
        );
      })}
    </div>
  );
}

function FigHead({ traceId, trace, open }: { traceId: string; trace: DemoTrace; open: boolean }) {
  return (
    <div className={styles.figHead}>
      <span className={styles.figCaret} data-open={open ? "1" : undefined} aria-hidden>
        ▸
      </span>
      <span className={styles.figTitle}>
        {traceId} · {trace.label} <span className={styles.figAgent}>({trace.agent})</span>
      </span>
      <span className={styles.figMeta}>— {trace.total} steps</span>
      {!open && <span className={styles.figHint}>click to slice</span>}
    </div>
  );
}

// The trace spine — the colored step pipe. Shown whether a figure is open or
// collapsed; an open figure adds the four slicer rows beneath it.
function Spine({ traceId, trace, open }: { traceId: string; trace: DemoTrace; open: boolean }) {
  return (
    <>
      <FigHead traceId={traceId} trace={trace} open={open} />
      <div className={styles.row}>
        <div className={styles.rlab}>
          <span className={`${styles.rname} ${styles.rnameTrace}`}>trace</span>
          <span className={styles.rsub}>{trace.total} steps · colored by action</span>
        </div>
        <Pipe trace={trace} />
      </div>
    </>
  );
}

function Figure({
  traceId,
  open,
  onOpen,
}: {
  traceId: string;
  open: boolean;
  onOpen: () => void;
}) {
  const trace = SLICER_DEMO[traceId];

  if (!open) {
    // Collapsed: just the spine, as one big click target that opens it.
    return (
      <button type="button" className={`${styles.fig} ${styles.figBtn}`} onClick={onOpen} aria-expanded={false}>
        <Spine traceId={traceId} trace={trace} open={false} />
      </button>
    );
  }

  return (
    <div className={`${styles.fig} ${styles.figOpen}`}>
      <Spine traceId={traceId} trace={trace} open />
      {SLICER_META.map((m) => (
        <div className={styles.row} key={m.key}>
          <div className={styles.rlab}>
            <span className={styles.rname} style={{ color: `var(--c-${m.sig})` }}>
              {m.name}
            </span>
            <span className={`${styles.badge} ${m.tier === "deterministic" ? styles.bDet : styles.bCheap}`}>
              {m.tier}
            </span>
            <span className={styles.rsub}>
              {trace.slicers[m.key].length} slices · {m.desc}
            </span>
          </div>
          <SlicerBar trace={trace} sliceKey={m.key} sig={m.sig} />
        </div>
      ))}
    </div>
  );
}

const TRACE_IDS = ["T1", "T2"];

function SlicerViewer() {
  // Accordion: exactly one trace is decomposed at a time. Opening the other
  // collapses this one back to its spine, so the card stays a constant size.
  const [openId, setOpenId] = useState(TRACE_IDS[0]);
  return (
    <div className={styles.root}>
      <div className={styles.body}>
        <div className={styles.legend}>
          {ACTION_LEGEND.map((a) => (
            <span className={styles.lgItem} key={a}>
              <span className={styles.sw} style={{ background: `var(--c-${a})` }} />
              {a}
            </span>
          ))}
        </div>
        {TRACE_IDS.map((id) => (
          <Figure key={id} traceId={id} open={openId === id} onOpen={() => setOpenId(id)} />
        ))}
      </div>
    </div>
  );
}

export default function TraceSlicerCompare() {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0);
  const [mobile, setMobile] = useState(false);
  const [height, setHeight] = useState<number | undefined>(undefined);

  useEffect(() => {
    const vp = viewportRef.current;
    const cv = canvasRef.current;
    if (!vp || !cv) return;
    const recompute = () => {
      const w = vp.clientWidth;
      if (w > 0 && w < MOBILE_BP) {
        setMobile(true);
        setScale(1);
        setHeight(undefined);
        return;
      }
      setMobile(false);
      const s = w / CANVAS_W;
      setScale(s);
      // transform doesn't shrink the layout box, so pin the viewport to the
      // scaled content height (mirrors HubFeatureFrame's scaled-iframe sizing).
      setHeight(cv.offsetHeight * s);
    };
    const ro = new ResizeObserver(recompute);
    ro.observe(vp);
    ro.observe(cv);
    recompute();
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={viewportRef}
      className={styles.frame}
      style={mobile ? undefined : { height }}
    >
      <div
        ref={canvasRef}
        className={styles.canvas}
        data-mobile={mobile ? "1" : undefined}
        style={mobile ? undefined : { width: CANVAS_W, transform: `scale(${scale})` }}
      >
        <SlicerViewer />
      </div>
    </div>
  );
}
