export const COLORS = {
  bg: "#0A0A0A",
  bgCard: "#111111",
  bgHover: "#1A1A1A",
  border: "#222222",
  borderLight: "#333333",
  text: "#EDEDED",
  textDim: "#888888",
  textMuted: "#555555",
  green: "#00FF41",
  greenDim: "#00CC33",
  orange: "#FF9900",
  red: "#FF3333",
  yellow: "#FFD700",
} as const;

export function formatNumber(
  val: number | null | undefined,
  opts?: { decimals?: number; suffix?: string; prefix?: string }
): string {
  if (val == null || isNaN(val)) return "--";
  const d = opts?.decimals ?? 1;
  const formatted = val.toFixed(d);
  return `${opts?.prefix ?? ""}${formatted}${opts?.suffix ?? ""}`;
}

export function formatPct(val: number | null | undefined): string {
  return formatNumber(val, { suffix: "%", decimals: 1 });
}

export function formatPrice(val: number | null | undefined): string {
  return formatNumber(val, { prefix: "$", decimals: 2 });
}

export function parseStatus(status: string): {
  label: string;
  color: string;
  detail: string | null;
} {
  // Statuses are written by score_ai_analysis.py as emoji-prefixed strings:
  //   "🟢 Eligible"
  //   "🆕 New"
  //   "🏷️ -25% vs. 52w p/s"           (Discount)
  //   "❌ net_margin, fcf_margin"     (Excluded — joined red-flag names)
  //   "❌ Unprofitable Health Tech"   (Excluded — sector rule)
  // Match on the emoji prefix first, since the English words don't always appear.
  const s = status ?? "";
  // Strip any leading emoji(s) — including variation selectors like 🏷️ — to
  // surface the human-readable detail (e.g. "net_margin, fcf_margin").
  const trimmed = s.replace(/^(?:\p{Extended_Pictographic}\uFE0F?\s*)+/u, "").trim();

  if (s.startsWith("❌"))
    return { label: "Excluded", color: COLORS.red, detail: trimmed || null };
  if (s.startsWith("🏷️") || s.includes("Discount"))
    return { label: "Discount", color: COLORS.orange, detail: trimmed || null };
  if (s.startsWith("🆕") || s.includes("New"))
    return { label: "New", color: COLORS.yellow, detail: null };
  if (s.startsWith("🟢") || s.includes("Eligible"))
    return { label: "Eligible", color: COLORS.green, detail: null };
  return { label: s || "--", color: COLORS.textDim, detail: null };
}

export function parseEval(val: string | null): {
  passed: boolean | null;
  label: string;
  color: string;
} {
  if (!val) return { passed: null, label: "--", color: COLORS.textMuted };
  if (val.includes("\u2705"))
    return { passed: true, label: "PASS", color: COLORS.green };
  if (val.includes("\u274C"))
    return { passed: false, label: "FAIL", color: COLORS.red };
  return { passed: null, label: "--", color: COLORS.textMuted };
}

/**
 * Extract the rationale text from a bear_eval or bull_eval string.
 *
 * Verdicts are stored as e.g. "✅ (Rare disease pharma with 94% YoY rev growth)"
 * or "❌ Net margin declined significantly YoY" or just "✅" with no rationale.
 *
 * This function strips the leading emoji and optional parentheses,
 * returning just the rationale (or null if there isn't one).
 */
export function extractEvalRationale(val: string | null): string | null {
  if (!val) return null;
  // Strip the verdict emoji (✅ U+2705 or ❌ U+274C) from the start
  let text = val.replace(/^[\u2705\u274C]\s*/u, "").trim();
  if (!text) return null;
  // If wrapped in parentheses, strip them
  if (text.startsWith("(") && text.endsWith(")")) {
    text = text.slice(1, -1).trim();
  }
  return text || null;
}
