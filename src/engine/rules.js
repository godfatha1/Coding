// Table rules. Everything the odds engine and the strategy charts need to know.
export const DEFAULT_RULES = {
  decks: 6,
  hitSoft17: false,   // false = S17 (dealer stands on all 17s), true = H17
  das: true,          // double after split allowed
  surrender: true,    // late surrender allowed
  doubleOn: 'any',    // 'any' | '9-11' | '10-11'
  peek: true,         // dealer peeks for blackjack before the player acts
  blackjackPays: 1.5,
};

export const RULE_PRESETS = [
  { id: 'vegas6s17', label: 'Vegas 6-deck, S17', rules: { decks: 6, hitSoft17: false, das: true, surrender: true } },
  { id: 'vegas6h17', label: 'Vegas 6-deck, H17', rules: { decks: 6, hitSoft17: true, das: true, surrender: true } },
  { id: 'strip8h17', label: '8-deck, H17, no surrender', rules: { decks: 8, hitSoft17: true, das: true, surrender: false } },
  { id: 'nodas', label: '6-deck, S17, no DAS', rules: { decks: 6, hitSoft17: false, das: false, surrender: true } },
];

export function normalizeRules(partial) {
  const r = { ...DEFAULT_RULES, ...(partial || {}) };
  r.decks = Math.min(8, Math.max(4, Number(r.decks) || 6));
  return r;
}

export function doubleAllowedOn(total, rules) {
  if (rules.doubleOn === '9-11') return total >= 9 && total <= 11;
  if (rules.doubleOn === '10-11') return total >= 10 && total <= 11;
  return true;
}

export function rulesSummary(rules) {
  const bits = [
    `${rules.decks} decks`,
    rules.hitSoft17 ? 'H17' : 'S17',
    rules.das ? 'DAS' : 'no DAS',
    rules.surrender ? 'late surrender' : 'no surrender',
  ];
  return bits.join(' · ');
}
