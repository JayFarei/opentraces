"use client";

import { useEffect, useRef, useState } from "react";

// Scoped dot-grid "materialize" pulse, adapted from the kb/app dev-reload
// overlay (components/refresh-overlay.tsx). Unlike that one it is NOT a
// full-screen fixed layer driven by a context — it fills its positioned
// parent (absolute inset-0) and is driven by a single `active` prop, so it can
// sit inside the Hub window while the live iframe boots and fade out on load.

const DOT_SPACING = 20;
const DOT_RADIUS_MIN = 1.5;
const DOT_RADIUS_MAX = 4.5;
const DOT_OPACITY_MIN = 0.1;
const DOT_OPACITY_MAX = 0.95;
const ACTIVE_RATIO = 0.12;
const GLOW_RADIUS = 3;
const HOLD_MS = 120;
const FADE_MS = 400;
const SPAWN_INTERVAL_MS = 40;
const FADEOUT_DURATION_MS = 600;

type Theme = "light" | "dark";

interface Palette {
  ink: [number, number, number]; // dot color
  glow: [number, number, number]; // soft halo around lit dots
  tint: [number, number, number]; // backdrop wash behind the blur
  tintAlpha: number;
}

// Dark default reads as glowing white dots over the dark poster; light theme
// flips to ink dots with a blue halo so they're visible over a light poster.
const PALETTES: Record<Theme, Palette> = {
  dark: { ink: [255, 255, 255], glow: [200, 240, 255], tint: [0, 0, 0], tintAlpha: 0.18 },
  light: { ink: [24, 33, 56], glow: [70, 140, 220], tint: [255, 255, 255], tintAlpha: 0.28 },
};

interface Dot {
  col: number;
  row: number;
  startTime: number;
}

type Phase = "idle" | "active" | "fading";

interface HubBootPulseProps {
  /** While true the pulse runs; when it flips false the overlay eases out. */
  active: boolean;
  theme?: Theme;
  /** Fires once the fade-out has fully completed (overlay gone). */
  onExited?: () => void;
}

export function HubBootPulse({ active, theme = "dark", onExited }: HubBootPulseProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const blurRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<number>(0);
  const dotsRef = useRef<Dot[]>([]);
  const lastSpawnRef = useRef(0);
  const fadeStartRef = useRef(0);
  const pal = PALETTES[theme];
  // Read the palette + exit callback live inside the animation loop via refs,
  // so a theme change (e.g. the site settling from its SSR default a beat after
  // mount) recolors the dots WITHOUT being an effect dependency — otherwise it
  // would tear down and restart the canvas loop mid-pulse, re-spawning the whole
  // grid and reading as a second "pulse back".
  const onExitedRef = useRef(onExited);
  const palRef = useRef(pal);
  useEffect(() => {
    onExitedRef.current = onExited;
    palRef.current = pal;
  });

  useEffect(() => {
    if (active && phase === "idle") {
      setPhase("active");
    } else if (!active && phase === "active") {
      setPhase("fading");
      fadeStartRef.current = performance.now();
    }
  }, [active, phase]);

  useEffect(() => {
    if (phase === "idle") {
      dotsRef.current = [];
      if (animRef.current) cancelAnimationFrame(animRef.current);
      return;
    }

    const canvas = canvasRef.current;
    const container = containerRef.current;
    const blurEl = blurRef.current;
    if (!canvas || !container) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const rect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = rect.width + "px";
      canvas.style.height = rect.height + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    const cols = Math.ceil(canvas.clientWidth / DOT_SPACING);
    const rows = Math.ceil(canvas.clientHeight / DOT_SPACING);
    const totalDots = cols * rows;
    const targetActive = Math.floor(totalDots * ACTIVE_RATIO);

    if (phase === "active") {
      dotsRef.current = [];
      lastSpawnRef.current = 0;
    }

    const frame = (now: number) => {
      if (!ctx) return;
      const [ir, ig, ib] = palRef.current.ink;
      const [gr, gg, gb] = palRef.current.glow;
      const [tr, tg, tb] = palRef.current.tint;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);

      let globalAlpha = 1;
      if (phase === "fading") {
        const elapsed = now - fadeStartRef.current;
        const raw = 1 - Math.min(1, elapsed / FADEOUT_DURATION_MS);
        globalAlpha = raw * raw;

        if (blurEl) {
          const blurPx = Math.round(24 * globalAlpha);
          const bgAlpha = (palRef.current.tintAlpha * globalAlpha).toFixed(3);
          blurEl.style.backdropFilter = `blur(${blurPx}px)`;
          (blurEl.style as CSSStyleDeclaration & { WebkitBackdropFilter: string }).WebkitBackdropFilter = `blur(${blurPx}px)`;
          blurEl.style.background = `rgba(${tr}, ${tg}, ${tb}, ${bgAlpha})`;
        }

        if (globalAlpha < 0.01) {
          cancelAnimationFrame(animRef.current);
          setPhase("idle");
          onExitedRef.current?.();
          return;
        }
      }

      if (phase === "active" && now - lastSpawnRef.current > SPAWN_INTERVAL_MS) {
        const activeCount = dotsRef.current.filter(
          (d) => now - d.startTime < HOLD_MS + FADE_MS
        ).length;
        const toSpawn = Math.max(1, Math.min(3, targetActive - activeCount));
        for (let i = 0; i < toSpawn; i++) {
          const col = Math.floor(Math.random() * cols);
          const row = Math.floor(Math.random() * rows);
          dotsRef.current.push({ col, row, startTime: now + Math.random() * 60 });
          if (Math.random() < 0.35) {
            const nc = col + (Math.random() < 0.5 ? -1 : 1);
            const nr = row + (Math.random() < 0.5 ? -1 : 1);
            if (nc >= 0 && nc < cols && nr >= 0 && nr < rows) {
              dotsRef.current.push({ col: nc, row: nr, startTime: now + 30 + Math.random() * 40 });
            }
          }
        }
        lastSpawnRef.current = now;
      }

      dotsRef.current = dotsRef.current.filter(
        (d) => now - d.startTime < HOLD_MS + FADE_MS + 100
      );

      ctx.fillStyle = `rgba(${ir}, ${ig}, ${ib}, ${DOT_OPACITY_MIN * globalAlpha})`;
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const x = c * DOT_SPACING + DOT_SPACING / 2;
          const y = r * DOT_SPACING + DOT_SPACING / 2;
          ctx.beginPath();
          ctx.arc(x, y, DOT_RADIUS_MIN, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      for (const dot of dotsRef.current) {
        const elapsed = now - dot.startTime;
        if (elapsed < 0) continue;

        let t: number;
        if (elapsed < HOLD_MS) {
          t = Math.min(1, elapsed / 60);
        } else {
          t = 1 - Math.min(1, (elapsed - HOLD_MS) / FADE_MS);
        }
        t = t * t * (3 - 2 * t);

        const radius = DOT_RADIUS_MIN + (DOT_RADIUS_MAX - DOT_RADIUS_MIN) * t;
        const opacity = (DOT_OPACITY_MIN + (DOT_OPACITY_MAX - DOT_OPACITY_MIN) * t) * globalAlpha;
        const x = dot.col * DOT_SPACING + DOT_SPACING / 2;
        const y = dot.row * DOT_SPACING + DOT_SPACING / 2;

        if (t > 0.3 && globalAlpha > 0.1) {
          const glowOpacity = (t - 0.3) * 0.4 * globalAlpha;
          const gradient = ctx.createRadialGradient(x, y, radius, x, y, radius + GLOW_RADIUS);
          gradient.addColorStop(0, `rgba(${gr}, ${gg}, ${gb}, ${glowOpacity})`);
          gradient.addColorStop(1, `rgba(${gr}, ${gg}, ${gb}, 0)`);
          ctx.fillStyle = gradient;
          ctx.beginPath();
          ctx.arc(x, y, radius + GLOW_RADIUS, 0, Math.PI * 2);
          ctx.fill();
        }

        ctx.fillStyle = `rgba(${ir}, ${ig}, ${ib}, ${opacity})`;
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fill();
      }

      animRef.current = requestAnimationFrame(frame);
    };

    animRef.current = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", resize);
    };
    // Intentionally depends ONLY on phase: the palette is read live from palRef
    // inside the loop, so theme changes never restart the animation.
  }, [phase]);

  if (phase === "idle") return null;

  return (
    <div
      ref={containerRef}
      className="hub-boot-pulse"
      aria-hidden="true"
      style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 4 }}
    >
      <div
        ref={blurRef}
        style={{
          position: "absolute",
          inset: 0,
          backdropFilter: "blur(24px)",
          WebkitBackdropFilter: "blur(24px)",
          background: `rgba(${pal.tint[0]}, ${pal.tint[1]}, ${pal.tint[2]}, ${pal.tintAlpha})`,
        }}
      />
      <canvas
        ref={canvasRef}
        style={{ position: "absolute", inset: 0, imageRendering: "auto" }}
      />
    </div>
  );
}
