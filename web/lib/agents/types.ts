/**
 * Agent library + team model — pure types & helpers (migration 045).
 *
 * Client-safe: no server imports, so the team builder can pull `fillSentence`,
 * `readiness`, etc. without dragging the Supabase client into the browser
 * bundle. The DB reads live in `library.ts`.
 */

/** The only grouping (brief §3, axis 1). Mechanically true, never inferred. */
export type AgentAction = "buy" | "sell" | "manage";

/** Declared intent tags on sells (brief §3, axis 2) — a small fixed set. */
export type TriggerTag = "caps-losses" | "banks-gains";

export const TRIGGER_LABELS: Record<string, string> = {
  "caps-losses": "caps losses",
  "banks-gains": "banks gains",
};

/** A single typed, bounded control in an agent's quick config. */
export interface ParamSpec {
  key: string;
  label: string;
  type: "number" | "select";
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  default: number | string;
  options?: { value: number | string; label: string }[];
}

/** A library template — the unconfigured agent in the shelf. */
export interface LibraryAgent {
  handle: string;
  displayName: string;
  description: string | null;
  poweredBy: string | null;
  action: AgentAction;
  triggers: string[];
  paramSchema: ParamSpec[];
  sentenceTemplate: string | null;
  /**
   * The agent's baked-in brief (migration 046). Non-null only for "thinking"
   * agents (LLM buyer / reviewer) — the team builder shows a brief field iff
   * this is set; mechanical/manage agents leave it null.
   */
  defaultMandate: string | null;
}

/** A configured copy of a library agent saved onto a portfolio. */
export interface TeamAgent extends LibraryAgent {
  params: Record<string, number | string>;
  enabled: boolean;
  /** Per-instance brief override; null = track the agent default. */
  mandate: string | null;
}

/** Whether the owner has pinned a custom brief on this saved agent. */
export function hasCustomMandate(agent: TeamAgent): boolean {
  return (agent.mandate ?? "").trim().length > 0;
}

/** The brief actually in force for an agent: instance override ?? default. */
export function effectiveMandate(
  agent: Pick<LibraryAgent, "defaultMandate"> & { mandate?: string | null },
): string {
  return (agent.mandate ?? null)?.trim() || agent.defaultMandate || "";
}

/** Merge stored params over schema defaults, dropping unknown keys. */
export function withDefaults(
  schema: ParamSpec[],
  params: Record<string, number | string>,
): Record<string, number | string> {
  const out: Record<string, number | string> = {};
  for (const spec of schema) {
    const v = params[spec.key];
    out[spec.key] = v === undefined || v === null ? spec.default : v;
  }
  return out;
}

/** The default param set for a fresh drag-in (every control at its default). */
export function defaultParams(
  schema: ParamSpec[],
): Record<string, number | string> {
  return withDefaults(schema, {});
}

/**
 * Interpolate an agent's plain-language sentence from its params. Missing
 * placeholders fall back to the schema default so the line is always complete.
 */
export function fillSentence(
  agent: Pick<LibraryAgent, "sentenceTemplate" | "paramSchema">,
  params: Record<string, number | string>,
): string {
  const tmpl = agent.sentenceTemplate;
  if (!tmpl) return "";
  const merged = withDefaults(agent.paramSchema, params);
  return tmpl.replace(/\{(\w+)\}/g, (_m, key: string) => {
    const v = merged[key];
    return v === undefined || v === null ? `{${key}}` : String(v);
  });
}

export interface Readiness {
  buy: boolean;
  sell: boolean;
  manage: boolean;
  /** Declared sell triggers covered by the team's live sell agents. */
  triggers: string[];
  /** One-line gap verdict for the readiness strip. */
  verdict: string;
}

/**
 * Coverage readout (brief §5). Reports *absence*, never the roster: which of
 * buy / sell / manage are covered by at least one live (saved + running)
 * agent, the union of declared sell triggers, and a one-line gap verdict.
 */
export function readiness(team: TeamAgent[]): Readiness {
  const live = team.filter((a) => a.enabled);
  const buy = live.some((a) => a.action === "buy");
  const sell = live.some((a) => a.action === "sell");
  const manage = live.some((a) => a.action === "manage");

  const triggers = Array.from(
    new Set(
      live.filter((a) => a.action === "sell").flatMap((a) => a.triggers),
    ),
  );

  let verdict: string;
  if (team.length === 0) {
    verdict =
      "Empty team. A complete team can buy, sell and manage — start with a buyer.";
  } else if (buy && sell && manage) {
    verdict = "Every job covered.";
  } else {
    const gaps: string[] = [];
    if (!buy) gaps.push("a buyer");
    if (!sell) gaps.push("a way to sell");
    if (!manage) gaps.push("rebalancing");
    verdict = `Consider adding: ${gaps.join(", ")}.`;
  }

  return { buy, sell, manage, triggers, verdict };
}

/** Heartbeat role a library action maps to (migration 041 + 045). */
export const ROLE_FOR_ACTION: Record<AgentAction, string> = {
  buy: "buyer",
  sell: "reviewer",
  manage: "manager",
};
