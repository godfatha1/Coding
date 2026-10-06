import { DEFAULT_RULES, normalizeRules } from '../engine/rules.js';
import { buildScenarioIds, parseScenario } from '../engine/scenarios.js';
import { newCard } from '../engine/srs.js';

const KEY = 'hardsixteen.v1';

export const DEFAULT_SETTINGS = {
  rules: { ...DEFAULT_RULES },
  retireStreak: 3,
  filter: 'all',
  theme: 'system',
  haptics: true,
  oddsUpFront: false,
  chartProgress: true,
};

function freshState() {
  return {
    version: 1,
    settings: { ...DEFAULT_SETTINGS, rules: { ...DEFAULT_RULES } },
    step: 0,
    sinceNew: 0,
    cards: {},
    lifetime: { answered: 0, correct: 0, shaky: 0, bestStreak: 0 },
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
    settings: { ...base.settings, ...(saved.settings || {}), rules: normalizeRules(saved.settings?.rules) },
    lifetime: { ...base.lifetime, ...(saved.lifetime || {}) },
    session: { ...base.session },
    cards: {},
  };
  // Keep only cards that still name a real chart cell.
  for (const [id, card] of Object.entries(saved.cards || {})) {
    if (parseScenario(id) && card && typeof card === 'object') state.cards[id] = { ...newCard(id), ...card, id };
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
      cards, lifetime: state.lifetime,
    }));
    return true;
  } catch { return false; }
}

// The full deck, with saved progress filled in over the top.
export function deckFor(state) {
  return buildScenarioIds().map((id) => state.cards[id] || newCard(id));
}

export function ensureCard(state, id) {
  if (!state.cards[id]) state.cards[id] = newCard(id);
  return state.cards[id];
}

export function clearProgress(state) {
  state.cards = {};
  state.step = 0;
  state.sinceNew = 0;
  state.lifetime = { answered: 0, correct: 0, shaky: 0, bestStreak: 0 };
  state.session = { answered: 0, correct: 0, streak: 0, recent: [] };
  saveState(state);
}

export function exportJSON(state) {
  return JSON.stringify({
    version: 1, exported: new Date().toISOString(),
    settings: state.settings, step: state.step, sinceNew: state.sinceNew,
    cards: state.cards, lifetime: state.lifetime,
  }, null, 1);
}

export function importJSON(text) {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== 'object' || !parsed.cards) throw new Error('That is not a Hard Sixteen backup.');
  const base = freshState();
  const state = {
    ...base,
    settings: { ...base.settings, ...(parsed.settings || {}), rules: normalizeRules(parsed.settings?.rules) },
    step: Number(parsed.step) || 0,
    sinceNew: Number(parsed.sinceNew) || 0,
    lifetime: { ...base.lifetime, ...(parsed.lifetime || {}) },
    cards: {},
  };
  for (const [id, card] of Object.entries(parsed.cards)) {
    if (parseScenario(id) && card && typeof card === 'object') state.cards[id] = { ...newCard(id), ...card, id };
  }
  return state;
}
