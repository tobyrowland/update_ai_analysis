import { Company } from "./types";

/**
 * Company-name based deduplication.
 * Ported from build_portfolio.py — same logic, same thresholds.
 *
 * When the same company is listed on multiple exchanges (e.g. Sezzle on
 * NASDAQ as SEZL and on Xetra as A3EGAB), we want a single row in the
 * Example Agent view, preferring the US/ADR listing.
 */

const FUZZY_THRESHOLD = 0.8;

const US_EXCHANGES = new Set([
  "NYSE",
  "NASDAQ",
  "AMEX",
  "NYSEARCA",
  "BATS",
  "ARCA",
]);

// Match the Python regex for corporate suffixes to strip
const CORPORATE_SUFFIXES =
  /\b(inc|incorporated|corp|corporation|ltd|limited|llc|plc|sa|s\.a\.|se|s\.e\.|nv|n\.v\.|ag|a\.g\.|co|company|group|holdings|holding|enterprises|international|intl|technologies|technology|tech|systems|solutions|therapeutics|pharmaceuticals|pharma|biosciences|biopharma|medical|healthcare|class\s*[a-z]|cl\s*[a-z]|adr)\b/gi;

function normaliseCompany(name: string): string {
  let s = name.trim().toUpperCase();
  s = s.replace(CORPORATE_SUFFIXES, "");
  s = s.replace(/[^\w\s]/g, " ");
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

/**
 * Ratcliff-Obershelp similarity (what Python's SequenceMatcher.ratio() uses).
 * Returns a value in [0, 1].
 */
function sequenceRatio(a: string, b: string): number {
  if (!a && !b) return 1;
  if (!a || !b) return 0;

  const matching = matchingBlocks(a, b);
  const total = a.length + b.length;
  return total === 0 ? 0 : (2 * matching) / total;
}

function matchingBlocks(a: string, b: string): number {
  // Compute the length of all matching substrings (approximation of
  // SequenceMatcher's get_matching_blocks total).
  let matches = 0;
  const stack: Array<[number, number, number, number]> = [
    [0, a.length, 0, b.length],
  ];

  while (stack.length > 0) {
    const [alo, ahi, blo, bhi] = stack.pop()!;
    const [i, j, k] = findLongestMatch(a, alo, ahi, b, blo, bhi);
    if (k > 0) {
      matches += k;
      if (alo < i && blo < j) stack.push([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) stack.push([i + k, ahi, j + k, bhi]);
    }
  }
  return matches;
}

function findLongestMatch(
  a: string,
  alo: number,
  ahi: number,
  b: string,
  blo: number,
  bhi: number,
): [number, number, number] {
  // Returns [besti, bestj, bestk] — longest matching substring.
  let besti = alo;
  let bestj = blo;
  let bestk = 0;
  let j2len: Map<number, number> = new Map();

  // Build b2j: index of each char in b
  const b2j = new Map<string, number[]>();
  for (let j = blo; j < bhi; j++) {
    const ch = b[j];
    if (!b2j.has(ch)) b2j.set(ch, []);
    b2j.get(ch)!.push(j);
  }

  for (let i = alo; i < ahi; i++) {
    const newj2len = new Map<number, number>();
    const indices = b2j.get(a[i]) || [];
    for (const j of indices) {
      if (j < blo) continue;
      if (j >= bhi) break;
      const k = (j2len.get(j - 1) || 0) + 1;
      newj2len.set(j, k);
      if (k > bestk) {
        besti = i - k + 1;
        bestj = j - k + 1;
        bestk = k;
      }
    }
    j2len = newj2len;
  }

  return [besti, bestj, bestk];
}

function namesMatch(a: string, b: string): boolean {
  const normA = normaliseCompany(a);
  const normB = normaliseCompany(b);

  if (normA === normB) return true;

  if (normA && normB) {
    const [shorter, longer] =
      normA.length <= normB.length ? [normA, normB] : [normB, normA];
    if (longer.startsWith(shorter) && shorter.length >= 3) {
      return true;
    }
  }

  return sequenceRatio(normA, normB) >= FUZZY_THRESHOLD;
}

function isUsExchange(exchange: string | null | undefined): boolean {
  if (!exchange) return false;
  return US_EXCHANGES.has(exchange.trim().toUpperCase());
}

function pickBest(candidates: Company[]): Company {
  if (candidates.length === 1) return candidates[0];

  const usCandidates = candidates.filter((c) => isUsExchange(c.exchange));
  const pool = usCandidates.length > 0 ? usCandidates : candidates;

  return pool.reduce((best, c) => {
    const bestScore = best.composite_score ?? 0;
    const cScore = c.composite_score ?? 0;
    return cScore > bestScore ? c : best;
  });
}

/**
 * Group companies by fuzzy-matched name and return one representative
 * per group (preferring US/ADR listings, breaking ties on composite_score).
 */
export function deduplicateByCompany(entries: Company[]): Company[] {
  const groups: Company[][] = [];
  const assigned = new Array(entries.length).fill(false);

  for (let i = 0; i < entries.length; i++) {
    if (assigned[i]) continue;
    const group: Company[] = [entries[i]];
    assigned[i] = true;
    const iName = entries[i].company_name || "";
    for (let j = i + 1; j < entries.length; j++) {
      if (assigned[j]) continue;
      const jName = entries[j].company_name || "";
      if (namesMatch(iName, jName)) {
        group.push(entries[j]);
        assigned[j] = true;
      }
    }
    groups.push(group);
  }

  return groups.map(pickBest);
}
