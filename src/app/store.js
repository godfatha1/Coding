import { DEFAULT_RULES, normalizeRules } from '../engine/rules.js';
import { buildScenarioIds, parseScenario } from '../engine/scenarios.js';
import { newCard } from '../engine/srs.js';

const KEY = 'hardsixteen.v1';

export const DEFAULT_SETTINGS = {
  rules: { ...DEFAULT_RULES },
  retireStreak: 3,
  filter: 'all',
  mode: 'play',          // 'play' = name the action, 'hands' = name the hands, 'mix' = both
  table: false,          // play the hand out for money after the graded call
  bet: 25,
  counting: { on: false, system: 'hilo', show: false, every: 5, penetration: 0.75 },
  theme: 'system',
  haptics: true,
  oddsUpFront: false,
  chartProgress: true,
};

export function freshCount() {
  return { running: 0, seen: 0, beforeRound: 0, seenBefore: 0, sinceCheck: 0, checks: 0, correct: 0, error: 0, shoes: 0 };
}

export function freshBank(start) {
  return { start, balance: start, hands: 0, wagered: 0, won: 0, lost: 0, pushed: 0, peak: start, low: start, history: [start] };
}

// Only the most recent stretch of the curve is kept; the totals above are exact
// whatever happens to the history.
const HISTORY_CAP = 400;

export function recordRound(bank, result) {
  bank.hands += 1;
  bank.wagered += result.wagered;
  bank.balance = Math.round((bank.balance + result.delta) * 100) / 100;
  if (result.delta > 0) bank.won += 1;
  else if (result.delta < 0) bank.lost += 1;
  else bank.pushed += 1;
  bank.peak = Math.max(bank.peak, bank.balance);
  bank.low = Math.min(bank.low, bank.balance);
  bank.history.push(bank.balance);
  if (bank.history.length > HISTORY_CAP) bank.history.splice(0, bank.history.length - HISTORY_CAP);
}

function freshState() {
  return {
    version: 1,
    settings: { ...DEFAULT_SETTINGS, rules: { ...DEFAULT_RULES } },
    step: 0,
    sinceNew: 0,
    cards: {},
    ruleCards: {},
    lifetime: { answered: 0, correct: 0, shaky: 0, bestStreak: 0 },
    bank: freshBank(1000),
    count: freshCount(),
    shoe: null,
    session: { answered: 0, correct: 0, streak: 0, recent: [] },
  };
}

export function loadState() {
  const base = freshState();
  let saved = null;
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) saved = JSON.parse(raw);
  } catch { saved = null; }
  if (!saved || typeof saved !== 'object') return base;
  const state = {
    ...base,
    ...saved,
    settings: {
      ...base.settings,
      ...(saved.settings || {}),
      rules: normalizeRules(saved.settings?.rules),
      counting: { ...base.settings.counting, ...(saved.settings?.counting || {}) },
    },
    lifetime: { ...base.lifetime, ...(saved.lifetime || {}) },
    bank: saneBank(saved.bank, base.bank),
    count: { ...base.count, ...(saved.count || {}) },
    shoe: saneShoe(saved.shoe),
    session: { ...base.session },
    cards: {},
    ruleCards: {},
  };
  // Keep only cards that still name a real chart cell.
  for (const [id, card] of Object.entries(saved.cards || {})) {
    if (parseScenario(id) && card && typeof card === 'object') state.cards[id] = { ...newCard(id), ...card, id };
  }
  for (const [id, card] of Object.entries(saved.ruleCards || {})) {
    if (/^R[hsp]\d+$/.test(id) && card && typeof card === 'object') state.ruleCards[id] = { ...newCard(id), ...card, id };
  }
  state.step = Number(saved.step) || 0;
  state.sinceNew = Number(saved.sinceNew) || 0;
  return state;
}

export function saveState(state) {
  try {
    // Untouched cells are implied by the scenario list, so only store what changed.
    const cards = {};
    for (const [id, c] of Object.entries(state.cards)) {
      if (c.stage !== 'new' || c.reps > 0) cards[id] = c;
    }
    localStorage.setItem(KEY, JSON.stringify({
      version: 1, settings: state.settings, step: state.step, sinceNew: state.sinceNew,
      cards, ruleCards: state.ruleCards, lifetime: state.lifetime, bank: state.bank,
      count: state.count, shoe: state.shoe,
    }));
    return true;
  } catch { return false; }
}

// The full deck, with saved progress filled in over the top.
export function deckFor(state) {
  return buildScenarioIds().map((id) => state.cards[id] || newCard(id));
}

export function ruleDeckFor(state, groups) {
  return groups.map((g) => state.ruleCards[g.id] || newCard(g.id));
}

export function ensureRuleCard(state, id) {
  if (!state.ruleCards[id]) state.ruleCards[id] = newCard(id);
  return state.ruleCards[id];
}

export function ensureCard(state, id) {
  if (!state.cards[id]) state.cards[id] = newCard(id);
  return state.cards[id];
}

function saneBank(saved, fallback) {
  if (!saved || typeof saved !== 'object' || !Array.isArray(saved.history)) return fallback;
  const bank = { ...fallback, ...saved };
  bank.history = saved.history.filter((n) => Number.isFinite(n)).slice(-HISTORY_CAP);
  if (!bank.history.length) bank.history = [bank.start];
  return bank;
}

// The shoe is kept across reloads so the count cannot be wiped by refreshing.
function saneShoe(saved) {
  if (!saved || !Array.isArray(saved.cards) || !saved.cards.length) return null;
  const cards = saved.cards.filter((c) => Number.isInteger(c) && c >= 1 && c <= 10);
  if (!cards.length) return null;
  return { cards, decks: Number(saved.decks) || 6, initial: Number(saved.initial) || cards.length };
}

export function clearProgress(state) {
  state.cards = {};
  state.ruleCards = {};
  state.step = 0;
  state.sinceNew = 0;
  state.lifetime = { answered: 0, correct: 0, shaky: 0, bestStreak: 0 };
  state.bank = freshBank(state.bank?.start ?? 1000);
  state.count = freshCount();
  state.shoe = null;
  state.session = { answered: 0, correct: 0, streak: 0, recent: [] };
  saveState(state);
}

export function exportJSON(state) {
  return JSON.stringify({
    version: 1, exported: new Date().toISOString(),
    settings: state.settings, step: state.step, sinceNew: state.sinceNew,
    cards: state.cards, ruleCards: state.ruleCards, lifetime: state.lifetime, bank: state.bank,
    count: state.count,
  }, null, 1);
}

export function importJSON(text) {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== 'object' || !parsed.cards) throw new Error('That is not a Hard Sixteen backup.');
  const base = freshState();
  const state = {
    ...base,
    settings: {
      ...base.settings,
      ...(parsed.settings || {}),
      rules: normalizeRules(parsed.settings?.rules),
      counting: { ...base.settings.counting, ...(parsed.settings?.counting || {}) },
    },
    step: Number(parsed.step) || 0,
    sinceNew: Number(parsed.sinceNew) || 0,
    lifetime: { ...base.lifetime, ...(parsed.lifetime || {}) },
    bank: saneBank(parsed.bank, base.bank),
    count: { ...base.count, ...(parsed.count || {}) },
    shoe: null,
    cards: {},
    ruleCards: {},
  };
  for (const [id, card] of Object.entries(parsed.cards)) {
    if (parseScenario(id) && card && typeof card === 'object') state.cards[id] = { ...newCard(id), ...card, id };
  }
  for (const [id, card] of Object.entries(parsed.ruleCards || {})) {
    if (/^R[hsp]\d+$/.test(id) && card && typeof card === 'object') state.ruleCards[id] = { ...newCard(id), ...card, id };
  }
  return state;
}
