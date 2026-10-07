import { makeDisplayCard, rankLabel } from './cards.js';
import { UPCARDS, rowRule, rowLabel } from './strategy.js';

// Every two-card decision a 4-8 deck chart covers, as a drillable deck.
// Ids look like h16-9 (hard 16 vs 9), s18-1 (soft 18 vs ace), p10-5 (tens vs 5).

export const HARD_TOTALS = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19];
export const SOFT_TOTALS = [13, 14, 15, 16, 17, 18, 19, 20];
export const PAIR_RANKS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

export const GROUPS = [
  { id: 'hard', label: 'Hard totals' },
  { id: 'soft', label: 'Soft hands' },
  { id: 'pairs', label: 'Pairs' },
];

export function buildScenarioIds() {
  const ids = [];
  for (const up of UPCARDS) {
    for (const t of HARD_TOTALS) ids.push(`h${t}-${up}`);
    for (const t of SOFT_TOTALS) ids.push(`s${t}-${up}`);
    for (const r of PAIR_RANKS) ids.push(`p${r}-${up}`);
  }
  return ids;
}

export function parseScenario(id) {
  const m = /^([hsp])(\d+)-(\d+)$/.exec(id);
  if (!m) return null;
  const kind = m[1];
  const key = Number(m[2]);
  const upcard = Number(m[3]);
  return { id, kind, key, upcard, group: kind === 'h' ? 'hard' : kind === 's' ? 'soft' : 'pairs' };
}

// All two-card hands a scenario can be dealt as. Hard totals exclude pairs,
// which get their own rows, so a hard 16 is 10+6 or 9+7 but never 8+8.
export function compositionsFor(scenario) {
  if (scenario.kind === 'p') return [[scenario.key, scenario.key]];
  if (scenario.kind === 's') return [[1, scenario.key - 11]];
  const out = [];
  for (let a = 2; a <= 10; a += 1) {
    const b = scenario.key - a;
    if (b < a || b > 10 || a === b) continue;
    out.push([a, b]);
  }
  return out;
}

export function scenarioLabel(id) {
  const s = typeof id === 'string' ? parseScenario(id) : id;
  if (!s) return id;
  const up = `vs ${rankLabel(s.upcard)}`;
  if (s.kind === 'p') return `${rankLabel(s.key)},${rankLabel(s.key)} ${up}`;
  if (s.kind === 's') return `Soft ${s.key} ${up}`;
  return `Hard ${s.key} ${up}`;
}

export function scenarioHandLabel(id) {
  const s = typeof id === 'string' ? parseScenario(id) : id;
  if (!s) return id;
  if (s.kind === 'p') return `${rankLabel(s.key)},${rankLabel(s.key)}`;
  if (s.kind === 's') return `A,${s.key - 11}`;
  return `Hard ${s.key}`;
}

// Deal a scenario as real cards, picking one of its compositions and shuffling
// the order so the same total does not always look the same.
export function dealScenario(id, rng = Math.random) {
  const scenario = parseScenario(id);
  if (!scenario) throw new Error(`not a scenario id: ${id}`);
  const options = compositionsFor(scenario);
  const values = [...options[Math.floor(rng() * options.length)]];
  if (rng() < 0.5) values.reverse();
  return {
    scenario,
    playerCards: values,
    upcard: scenario.upcard,
    playerDisplay: values.map((v) => makeDisplayCard(v, rng)),
    dealerDisplay: makeDisplayCard(scenario.upcard, rng),
  };
}

// --- rule groups -----------------------------------------------------------
// The reverse drill: rows that share a rule are one answer. "Always hit" is
// hard 5 through 8, and a pair of fives sits with hard 10 because the chart
// reads it as one.

function runsOfNumbers(keys) {
  const runs = [];
  for (const k of [...keys].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && k === last[last.length - 1] + 1) last.push(k);
    else runs.push([k]);
  }
  return runs;
}

export function groupLabel(rows) {
  const parts = [];
  for (const section of ['hard', 'soft', 'pairs']) {
    const keys = rows.filter((r) => r.section === section).map((r) => r.key);
    if (!keys.length) continue;
    if (section === 'pairs') {
      parts.push(...keys.sort((a, b) => (a === 1 ? -1 : b === 1 ? 1 : a - b)).map((k) => rowLabel('pairs', k)));
      continue;
    }
    const name = section === 'hard' ? 'Hard' : 'Soft';
    for (const run of runsOfNumbers(keys)) {
      parts.push(run.length > 1 ? `${name} ${run[0]}–${run[run.length - 1]}` : `${name} ${run[0]}`);
    }
  }
  return parts.join(' · ');
}

export function ruleGroups(rules) {
  const rows = [
    ...HARD_TOTALS.map((key) => ({ section: 'hard', key })),
    ...SOFT_TOTALS.map((key) => ({ section: 'soft', key })),
    ...PAIR_RANKS.map((key) => ({ section: 'pairs', key })),
  ];
  const byRule = new Map();
  for (const row of rows) {
    const rule = rowRule(row.section, row.key, rules);
    if (!byRule.has(rule)) byRule.set(rule, []);
    byRule.get(rule).push(row);
  }
  return [...byRule.entries()].map(([rule, group]) => ({
    id: `R${group[0].section[0]}${group[0].key}`,
    rule,
    rows: group,
    label: groupLabel(group),
  }));
}
