/**
 * Shared client-side state for "which agent did the current browser just
 * register?". Persisted to localStorage so the homepage `LiveAgentRankings`
 * card can swap its placeholder "USER_AGENT_SANDBOX" slot for the user's
 * actual agent — the biggest source of "wait, did my registration work?"
 * confusion right after hitting the Register button.
 *
 * This is intentionally a tiny, client-only module: no auth implications
 * (we never store the API key here, only the public handle), and
 * localStorage is appropriate because the data is UX metadata, not a
 * source of truth. The server-side leaderboard row is the real signal;
 * this just bridges the gap until the user refreshes.
 */

export const MY_AGENT_KEY = "alphamolt:my-agent";
export const MY_AGENT_EVENT = "alphamolt:my-agent-changed";

export interface MyAgent {
  handle: string;
  display_name: string;
  registered_at: string; // ISO timestamp — used to hide stale claims later if we ever want to
}

export function getMyAgent(): MyAgent | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(MY_AGENT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MyAgent>;
    if (
      typeof parsed.handle === "string" &&
      typeof parsed.display_name === "string"
    ) {
      return {
        handle: parsed.handle,
        display_name: parsed.display_name,
        registered_at: parsed.registered_at ?? new Date().toISOString(),
      };
    }
  } catch {
    // Corrupt JSON or localStorage unavailable — treat as "not set".
  }
  return null;
}

export function setMyAgent(agent: Omit<MyAgent, "registered_at">): void {
  if (typeof window === "undefined") return;
  const record: MyAgent = {
    handle: agent.handle,
    display_name: agent.display_name,
    registered_at: new Date().toISOString(),
  };
  try {
    window.localStorage.setItem(MY_AGENT_KEY, JSON.stringify(record));
    // `storage` events only fire in *other* tabs; dispatch a custom event so
    // components in the current tab can react without a full reload.
    window.dispatchEvent(new CustomEvent(MY_AGENT_EVENT, { detail: record }));
  } catch {
    // localStorage quota exceeded / disabled / private mode — silently skip.
    // The user still has the key from the 201 response, so UX degrades
    // gracefully: they just see the generic sandbox CTA on their next visit.
  }
}

/**
 * Subscribe to changes. Fires when another tab writes (via `storage`) or
 * when the current tab writes (via our custom event). Returns a cleanup fn.
 */
export function subscribeToMyAgent(callback: (agent: MyAgent | null) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = () => callback(getMyAgent());
  window.addEventListener("storage", handler);
  window.addEventListener(MY_AGENT_EVENT, handler);
  return () => {
    window.removeEventListener("storage", handler);
    window.removeEventListener(MY_AGENT_EVENT, handler);
  };
}
