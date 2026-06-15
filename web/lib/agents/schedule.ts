/**
 * Heartbeat-schedule helpers — client-safe, no server imports.
 *
 * The rebalance automation runs on a daily cron: 07:00 UTC every day
 * (.github/workflows/agent-heartbeat.yml: "0 7 * * *"). Each agent / portfolio
 * acts on that tick once its own cadence has elapsed (an agent's
 * heartbeat_interval_hours; a portfolio's rebalance_cadence, migration 051), so
 * the next run is deterministic — the next 07:00-UTC at/after the due time.
 * Keep this the single source of the cron constant; if the workflow schedule
 * changes, change it here.
 */

export const HEARTBEAT_WEEKLY_UTC_DAY = 0; // Sunday — the weekly-cadence anchor
export const HEARTBEAT_UTC_HOUR = 7; // 07:00 UTC

/** Coarse relative duration: "just now" / "5m" / "3h" / "5d". */
export function relShort(ms: number): string {
  const m = Math.round(ms / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.round(h / 24);
  return `${d}d`;
}

/** Local-time label for an instant, e.g. "Sun, Jun 15, 08:00". */
export function dateTimeLabel(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Compact local label for tight spots, e.g. "Sun 08:00". */
export function shortRunLabel(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The smallest 07:00-UTC instant (any day) at or after `after` — the daily
 *  cron tick the heartbeat fires on. */
export function nextHeartbeatTick(after: number): number {
  const d = new Date(after);
  const todayTick = Date.UTC(
    d.getUTCFullYear(),
    d.getUTCMonth(),
    d.getUTCDate(),
    HEARTBEAT_UTC_HOUR,
    0,
    0,
    0,
  );
  return todayTick >= after ? todayTick : todayTick + 86_400_000;
}

/** The smallest Sunday-07:00-UTC instant at or after `after` — the tick a
 *  weekly-cadence portfolio next acts on. */
export function nextWeeklyHeartbeatTick(after: number): number {
  let cand = nextHeartbeatTick(after);
  for (let i = 0; i < 7; i++) {
    if (new Date(cand).getUTCDay() === HEARTBEAT_WEEKLY_UTC_DAY) return cand;
    cand += 86_400_000;
  }
  return cand;
}

/** Next run tick for a portfolio's rebalance cadence (migration 051): the next
 *  daily 07:00-UTC tick for 'daily', the next Sunday 07:00-UTC tick for
 *  'weekly'. A coarse "when does this portfolio next act" hint. */
export function nextRunForCadence(
  after: number,
  cadence: "daily" | "weekly",
): number {
  return cadence === "daily"
    ? nextHeartbeatTick(after)
    : nextWeeklyHeartbeatTick(after);
}

/**
 * The schedule line for an agent: its next weekly run (Sunday 07:00 UTC, in the
 * viewer's local time) at/after its due time — `last run + cadence`, or now for
 * an agent that hasn't run yet.
 */
export function scheduleText(
  lastRunAt: string | null,
  intervalHours: number | null,
  now: number,
): string {
  const intervalH = intervalHours ?? 168;
  const last = lastRunAt ? Date.parse(lastRunAt) : NaN;
  const hasRun = !Number.isNaN(last);
  const due = hasRun ? last + intervalH * 3_600_000 : now;
  const next = nextHeartbeatTick(Math.max(now, due));
  const nextLabel = `${dateTimeLabel(next)} (in ${relShort(next - now)})`;
  return hasRun
    ? `Last run ${relShort(now - last)} ago · next run ${nextLabel}`
    : `Next run ${nextLabel}`;
}
