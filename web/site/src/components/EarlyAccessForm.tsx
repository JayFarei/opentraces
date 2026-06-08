"use client";

import { useEffect, useId, useState, type FormEvent } from "react";

type State = "idle" | "loading" | "success" | "error";

export default function EarlyAccessForm() {
  const [email, setEmail] = useState("");
  // Honeypot: stays empty for real users. Bots that auto-fill every field
  // populate it and get silently dropped server-side.
  const [company, setCompany] = useState("");
  const [state, setState] = useState<State>("idle");
  // Gate the submit until the component has hydrated. Before then, onSubmit's
  // preventDefault isn't attached yet, so an early click would fall through to a
  // native form GET and reload the page. Disabling the button until mount makes
  // this JS-only form behave correctly no matter how fast the user clicks.
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);
  // Unique per instance so the form can be rendered more than once on a page
  // (e.g. teaser + CTA) without colliding on the honeypot's DOM id.
  const hpId = useId();
  const [msg, setMsg] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (state === "loading" || state === "success") return;
    setState("loading");
    setMsg("");
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, company }),
      });
      const data = await res.json();
      if (data.ok) {
        setState("success");
        setMsg(
          data.alreadyOnList
            ? "you're already on the list."
            : "you're on the list — we'll be in touch.",
        );
      } else {
        setState("error");
        setMsg(data.error ?? "something went wrong.");
      }
    } catch {
      setState("error");
      setMsg("network error — please try again.");
    }
  }

  const disabled = state === "loading" || state === "success";

  return (
    <form className="ea-form" onSubmit={submit}>
      {/* Honeypot — pushed off-screen, transparent, skipped by tab + screen
          readers. Inline-styled so the field is self-contained (no external CSS). */}
      <div
        className="ea-hp"
        aria-hidden="true"
        style={{
          position: "absolute",
          left: "-9999px",
          width: 1,
          height: 1,
          overflow: "hidden",
          opacity: 0,
          pointerEvents: "none",
        }}
      >
        <label htmlFor={hpId}>Company (leave this empty)</label>
        <input
          id={hpId}
          type="text"
          name="company"
          tabIndex={-1}
          autoComplete="off"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
        />
      </div>
      <div className="ea-row">
        <input
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@domain.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={disabled}
          aria-label="email address"
          required
          className="ea-input"
        />
        <button type="submit" className="btn btn-primary ea-btn" disabled={disabled || !ready}>
          {state === "loading"
            ? "[ … ]"
            : state === "success"
              ? "[ joined ]"
              : "[ get early access ]"}
        </button>
      </div>
      {msg && (
        <p className={`ea-msg ea-${state}`} role="status">
          {msg}
        </p>
      )}
    </form>
  );
}
