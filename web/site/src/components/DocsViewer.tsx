"use client";

import { useState, useEffect, useCallback } from "react";
import Markdown from "./Markdown";
import MachineView from "./MachineView";
import AudienceToggle from "./AudienceToggle";

type Audience = "human" | "machine";

interface Props {
  content: string;
  skillContent: string;
}

// Fade-in duration must match overlay-in keyframe in globals.css
const FADE_IN_MS = 460;
// Fade-out duration must match overlay-out keyframe in globals.css
const FADE_OUT_MS = 600;

export default function DocsViewer({ content, skillContent }: Props) {
  const [audience, setAudience] = useState<Audience>("human");
  const [mounted, setMounted] = useState(false);
  const [overlay, setOverlay] = useState(false);
  const [overlayOut, setOverlayOut] = useState(false);

  useEffect(() => {
    // URL param takes priority over localStorage
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("view") as Audience | null;
    const stored = localStorage.getItem("docs-audience") as Audience | null;
    const resolved = (fromUrl === "machine" ? "machine" : stored === "machine" ? "machine" : null);
    if (resolved === "machine") {
      setAudience("machine");
      document.documentElement.setAttribute("data-audience", "machine");
      history.replaceState({}, "", "?view=machine");
    }
    setMounted(true);
  }, []);

  const handleChange = useCallback((next: Audience) => {
    if (next === audience) return;

    // Mount overlay — CSS keyframe starts fade-in immediately (no rAF needed)
    setOverlay(true);
    setOverlayOut(false);

    // After fade-in completes: scroll to top, swap content, begin fade-out
    setTimeout(() => {
      window.scrollTo(0, 0);
      setAudience(next);
      localStorage.setItem("docs-audience", next);
      if (next === "machine") {
        document.documentElement.setAttribute("data-audience", "machine");
        history.replaceState({}, "", "?view=machine");
      } else {
        document.documentElement.removeAttribute("data-audience");
        history.replaceState({}, "", window.location.pathname);
      }
      setOverlayOut(true);
      setTimeout(() => setOverlay(false), FADE_OUT_MS + 50);
    }, FADE_IN_MS + 20);
  }, [audience]);

  useEffect(() => {
    return () => { document.documentElement.removeAttribute("data-audience"); };
  }, []);

  const isMachine = mounted && audience === "machine";

  return (
    <>
      {overlay && (
        <div
          className={`machine-overlay${overlayOut ? " fade-out" : ""}`}
          aria-hidden="true"
        />
      )}
      {isMachine && (
        <button
          className="machine-exit-btn"
          onClick={() => handleChange("human")}
          aria-label="Exit machine view"
        >
          ×
        </button>
      )}
      <div className="docs-audience-viewer">
        {!isMachine ? (
          <Markdown content={content} />
        ) : (
          <MachineView content={skillContent} />
        )}
        <AudienceToggle audience={mounted ? audience : "human"} onChange={handleChange} />
      </div>
    </>
  );
}
