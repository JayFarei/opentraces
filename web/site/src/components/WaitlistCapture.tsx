"use client";

import { useState, type FormEvent } from "react";

type State = "idle" | "loading" | "success" | "error";

export default function WaitlistCapture() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>("idle");
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
        body: JSON.stringify({ email }),
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

  return (
    <section className="waitlist" aria-labelledby="waitlist-title">
      <div className="waitlist-rule">
        <span>early access</span>
      </div>
      <h2 id="waitlist-title">want early access to the Hub?</h2>
      <p className="waitlist-lede">
        The Hub is the browsable companion to the CLI, still in preview. Leave your
        email and we&apos;ll let you in as it opens up.
      </p>
      <form className="waitlist-form" onSubmit={submit}>
        <input
          type="email"
          inputMode="email"
          autoComplete="email"
          placeholder="you@domain.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={state === "loading" || state === "success"}
          aria-label="email address"
          required
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={state === "loading" || state === "success"}
        >
          {state === "loading"
            ? "[ … ]"
            : state === "success"
              ? "[ joined ]"
              : "[ request access ]"}
        </button>
      </form>
      {msg && (
        <p className={`waitlist-msg waitlist-${state}`} role="status">
          {msg}
        </p>
      )}

      <style jsx>{`
        .waitlist {
          max-width: 640px;
          margin: 0 auto;
          padding: 72px 24px;
          text-align: center;
        }
        .waitlist-rule {
          display: flex;
          align-items: center;
          gap: 12px;
          justify-content: center;
          font-family: var(--font-mono, ui-monospace, Menlo, monospace);
          font-size: 12px;
          letter-spacing: 0.08em;
          text-transform: lowercase;
          color: color-mix(in srgb, currentColor 55%, transparent);
          margin-bottom: 18px;
        }
        .waitlist-rule::before,
        .waitlist-rule::after {
          content: "";
          height: 1px;
          flex: 1;
          max-width: 80px;
          background: color-mix(in srgb, currentColor 18%, transparent);
        }
        h2 {
          font-family: var(--font-display, "Space Grotesk", system-ui, sans-serif);
          font-size: clamp(22px, 3vw, 30px);
          font-weight: 600;
          margin: 0 0 12px;
          letter-spacing: -0.01em;
        }
        .waitlist-lede {
          color: color-mix(in srgb, currentColor 72%, transparent);
          font-size: 15px;
          line-height: 1.6;
          margin: 0 auto 28px;
          max-width: 520px;
        }
        .waitlist-form {
          display: flex;
          gap: 8px;
          justify-content: center;
          flex-wrap: wrap;
        }
        .waitlist-form input {
          flex: 1 1 280px;
          max-width: 360px;
          background: transparent;
          color: inherit;
          font-family: var(--font-mono, ui-monospace, Menlo, monospace);
          font-size: 14px;
          padding: 10px 14px;
          border: 1px solid color-mix(in srgb, currentColor 22%, transparent);
          border-radius: var(--radius, 6px);
          outline: none;
          transition: border-color 140ms ease;
        }
        .waitlist-form input::placeholder {
          color: color-mix(in srgb, currentColor 40%, transparent);
        }
        .waitlist-form input:focus {
          border-color: color-mix(in srgb, currentColor 55%, transparent);
        }
        .waitlist-form input:disabled {
          opacity: 0.6;
        }
        /* Self-sufficient button styling so it looks right even if the global
           .btn .btn-primary classes are unavailable; inherits them when present. */
        .waitlist-form button {
          font-family: var(--font-mono, ui-monospace, Menlo, monospace);
          font-size: 14px;
          padding: 10px 18px;
          white-space: nowrap;
          border: 1px solid color-mix(in srgb, currentColor 35%, transparent);
          border-radius: var(--radius, 6px);
          background: color-mix(in srgb, currentColor 8%, transparent);
          color: inherit;
          cursor: pointer;
          transition: background 140ms ease, border-color 140ms ease;
        }
        .waitlist-form button:hover:not(:disabled) {
          background: color-mix(in srgb, currentColor 14%, transparent);
          border-color: color-mix(in srgb, currentColor 55%, transparent);
        }
        .waitlist-form button:disabled {
          cursor: default;
          opacity: 0.7;
        }
        .waitlist-msg {
          font-family: var(--font-mono, ui-monospace, Menlo, monospace);
          font-size: 13px;
          margin: 16px 0 0;
        }
        .waitlist-success {
          color: var(--c-git, #3fb950);
        }
        .waitlist-error {
          color: var(--c-error, #f85149);
        }
      `}</style>
    </section>
  );
}
