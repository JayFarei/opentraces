const SNOOZE_MS = 7 * 24 * 60 * 60 * 1000;

export function reminderSnoozed(key: string): boolean {
  try {
    const raw = globalThis.localStorage?.getItem(`opentraces-pi:snooze:${key}`);
    if (!raw) return false;
    return Date.now() - Number(raw) < SNOOZE_MS;
  } catch {
    return false;
  }
}

export function snoozeReminder(key: string): void {
  try {
    globalThis.localStorage?.setItem(`opentraces-pi:snooze:${key}`, String(Date.now()));
  } catch {
    // Pi may run without a browser-like localStorage. Reminder state is optional UX.
  }
}
