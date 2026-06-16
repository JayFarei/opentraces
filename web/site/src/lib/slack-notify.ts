// Slack notification for early-access waitlist signups.
//
// Best-effort, dependency-free (plain fetch, mirroring waitlist-store.ts): a
// Slack Incoming Webhook URL is read from SLACK_WAITLIST_WEBHOOK_URL. When it
// is unset (e.g. local dev, or before the webhook is provisioned) the call is a
// silent no-op. A Slack outage never blocks or fails a signup — the caller
// awaits this only so the serverless function does not freeze before the POST
// completes, and any error is swallowed.

export interface WaitlistSignupMeta {
  /** Current total size of the waitlist, if known. */
  count?: number;
  /** Best-effort client IP (from x-forwarded-for). */
  ip?: string;
}

export async function notifyWaitlistSignup(
  email: string,
  meta: WaitlistSignupMeta = {},
): Promise<void> {
  const url = process.env.SLACK_WAITLIST_WEBHOOK_URL;
  if (!url) return;

  const lines = [`:tada: New early-access signup: *${email}*`];
  const context: string[] = [];
  if (typeof meta.count === "number") context.push(`waitlist #${meta.count}`);
  if (meta.ip) context.push(`ip ${meta.ip}`);
  if (context.length) lines.push(`_${context.join(" · ")}_`);

  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: lines.join("\n") }),
      cache: "no-store",
    });
  } catch {
    /* notifications are best-effort; never block a signup on Slack */
  }
}
