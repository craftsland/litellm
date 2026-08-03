import { ComplexityTier, KeywordTierRule } from "./KeywordTierRules";

/**
 * Stored shape of a keyword tier rule inside `complexity_router_config`. The UI's
 * KeywordTierRule carries an extra `id` used only as a React key, so it is stripped on the
 * way out and synthesized on the way back in. Both the create form and the edit modal go
 * through here so the two directions cannot drift.
 */
export interface StoredKeywordTierRule {
  keywords: string[];
  tier: ComplexityTier;
}

const TIERS: ReadonlySet<string> = new Set<ComplexityTier>(["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]);

const asKeywords = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((keyword): keyword is string => typeof keyword === "string").map((keyword) => keyword.trim())
    : [];

/**
 * Drop the React-only id and trim keywords, leaving one entry per rule. A rule left empty stays
 * empty rather than disappearing, so getKeywordTierRulesError can name the row it came from.
 */
export const serializeKeywordTierRules = (rules: KeywordTierRule[]): StoredKeywordTierRule[] =>
  rules.map((rule) => ({ keywords: asKeywords(rule.keywords).filter(Boolean), tier: rule.tier }));

export const hydrateKeywordTierRules = (value: unknown): KeywordTierRule[] => {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry, index) => {
    if (typeof entry !== "object" || entry === null) return [];
    const record = entry as Record<string, unknown>;
    const keywords = asKeywords(record.keywords).filter(Boolean);
    const tier = record.tier;
    if (keywords.length === 0 || typeof tier !== "string" || !TIERS.has(tier)) return [];
    return [{ id: `stored-${index}`, keywords, tier: tier as ComplexityTier }];
  });
};
