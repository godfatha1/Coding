// Basic strategy for a 4-8 deck shoe, the chart every odds book prints.
// Rows read left to right across dealer upcards 2,3,4,5,6,7,8,9,10,A.
//
// Codes: H hit · S stand · D double, else hit · Ds double, else stand
//        P split · Ph split if double-after-split is allowed, else hit
//        Rh surrender, else hit · Rs surrender, else stand · Rp surrender, else split

export const UPCARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 1];

const row = (s) => s.trim().split(/\s+/);

const HARD_S17 = {
  5:  row('H  H  H  H  H  H  H  H  H  H'),
  6:  row('H  H  H  H  H  H  H  H  H  H'),
  7:  row('H  H  H  H  H  H  H  H  H  H'),
  8:  row('H  H  H  H  H  H  H  H  H  H'),
  9:  row('H  D  D  D  D  H  H  H  H  H'),
  10: row('D  D  D  D  D  D  D  D  H  H'),
  11: row('D  D  D  D  D  D  D  D  D  H'),
  12: row('H  H  S  S  S  H  H  H  H  H'),
  13: row('S  S  S  S  S  H  H  H  H  H'),
  14: row('S  S  S  S  S  H  H  H  H  H'),
  15: row('S  S  S  S  S  H  H  H  Rh H'),
  16: row('S  S  S  S  S  H  H  Rh Rh Rh'),
  17: row('S  S  S  S  S  S  S  S  S  S'),
  18: row('S  S  S  S  S  S  S  S  S  S'),
  19: row('S  S  S  S  S  S  S  S  S  S'),
  20: row('S  S  S  S  S  S  S  S  S  S'),
  21: row('S  S  S  S  S  S  S  S  S  S'),
};

const SOFT_S17 = {
  13: row('H  H  H  D  D  H  H  H  H  H'),
  14: row('H  H  H  D  D  H  H  H  H  H'),
  15: row('H  H  D  D  D  H  H  H  H  H'),
  16: row('H  H  D  D  D  H  H  H  H  H'),
  17: row('H  D  D  D  D  H  H  H  H  H'),
  18: row('S  Ds Ds Ds Ds S  S  H  H  H'),
  19: row('S  S  S  S  S  S  S  S  S  S'),
  20: row('S  S  S  S  S  S  S  S  S  S'),
  21: row('S  S  S  S  S  S  S  S  S  S'),
};

const PAIRS_S17 = {
  1:  row('P  P  P  P  P  P  P  P  P  P'),
  2:  row('Ph Ph P  P  P  P  H  H  H  H'),
  3:  row('Ph Ph P  P  P  P  H  H  H  H'),
  4:  row('H  H  H  Ph Ph H  H  H  H  H'),
  5:  row('D  D  D  D  D  D  D  D  H  H'),
  6:  row('Ph P  P  P  P  H  H  H  H  H'),
  7:  row('P  P  P  P  P  P  H  H  H  H'),
  8:  row('P  P  P  P  P  P  P  P  P  P'),
  9:  row('P  P  P  P  P  S  P  P  S  S'),
  10: row('S  S  S  S  S  S  S  S  S  S'),
};

// H17 moves five cells plus the 8,8 surrender. Everything else is identical.
const HARD_H17 = {
  ...HARD_S17,
  11: row('D  D  D  D  D  D  D  D  D  D'),
  15: row('S  S  S  S  S  H  H  H  Rh Rh'),
  17: row('S  S  S  S  S  S  S  S  S  Rs'),
};
const SOFT_H17 = {
  ...SOFT_S17,
  18: row('Ds Ds Ds Ds Ds S  S  H  H  H'),
  19: row('S  S  S  S  Ds S  S  S  S  S'),
};
const PAIRS_H17 = {
  ...PAIRS_S17,
  8: row('P  P  P  P  P  P  P  P  P  Rp'),
};

export const CHARTS = {
  s17: { hard: HARD_S17, soft: SOFT_S17, pairs: PAIRS_S17 },
  h17: { hard: HARD_H17, soft: SOFT_H17, pairs: PAIRS_H17 },
};

export const ACTION_LABELS = {
  hit: 'Hit', stand: 'Stand', double: 'Double', split: 'Split', surrender: 'Surrender',
};
export const ACTION_SHORT = { hit: 'H', stand: 'S', double: 'D', split: 'P', surrender: 'R' };

export function chartFor(rules) {
  return CHARTS[rules.hitSoft17 ? 'h17' : 's17'];
}

export function upcardIndex(upcard) {
  const i = UPCARDS.indexOf(upcard);
  if (i < 0) throw new Error(`not a dealer upcard: ${upcard}`);
  return i;
}

// Resolve a chart code into the action to take under the rules in play.
export function codeToAction(code, rules) {
  switch (code) {
    case 'H': return 'hit';
    case 'S': return 'stand';
    case 'D': return 'double';
    case 'Ds': return 'double';
    case 'P': return 'split';
    case 'Ph': return rules.das ? 'split' : 'hit';
    case 'Rh': return rules.surrender ? 'surrender' : 'hit';
    case 'Rs': return rules.surrender ? 'surrender' : 'stand';
    case 'Rp': return rules.surrender ? 'surrender' : 'split';
    default: throw new Error(`unknown strategy code: ${code}`);
  }
}

// The action to take if the first choice is off the table (3+ cards, no DAS).
export function fallbackAction(code) {
  if (code === 'D') return 'hit';
  if (code === 'Ds') return 'stand';
  if (code === 'Ph') return 'hit';
  if (code === 'Rh') return 'hit';
  if (code === 'Rs') return 'stand';
  if (code === 'Rp') return 'split';
  return null;
}

// Look the hand up the way a chart does: pairs first, then soft, then hard.
export function lookupCode(cards, upcard, rules) {
  const chart = chartFor(rules);
  const col = upcardIndex(upcard);
  const isPairHand = cards.length === 2 && cards[0] === cards[1];
  let total = 0;
  let aces = 0;
  for (const c of cards) { total += c; if (c === 1) aces += 1; }
  const soft = aces > 0 && total + 10 <= 21;
  const best = soft ? total + 10 : total;

  if (isPairHand && chart.pairs[cards[0]]) {
    const code = chart.pairs[cards[0]][col];
    // A pair of fives is read off the hard-ten row, so label it that way.
    return { code, section: cards[0] === 5 ? 'hard' : 'pairs', key: cards[0] === 5 ? 10 : cards[0] };
  }
  if (soft && chart.soft[best]) return { code: chart.soft[best][col], section: 'soft', key: best };
  const row = chart.hard[Math.min(21, Math.max(5, best))];
  return { code: row[col], section: 'hard', key: Math.min(21, Math.max(5, best)) };
}

export function lookupStrategy(cards, upcard, rules) {
  const { code, section, key } = lookupCode(cards, upcard, rules);
  return { code, section, key, action: codeToAction(code, rules) };
}

// --- row rules -------------------------------------------------------------
// A chart cell on its own teaches you one hand. The rule for the whole row —
// "Stand 6 or less. Otherwise hit." — is the thing worth carrying to the table,
// so it is derived from the row rather than written out by hand, which keeps it
// honest when the rule toggles move cells around.

const VERB = { hit: 'hit', stand: 'stand', double: 'double', split: 'split', surrender: 'surrender' };
const VERB_CAP = { hit: 'Hit', stand: 'Stand', double: 'Double', split: 'Split', surrender: 'Surrender' };
const UP_LABEL = (u) => (u === 1 ? 'A' : String(u));

export function rowLabel(section, key) {
  if (section === 'pairs') return `${UP_LABEL(key)},${UP_LABEL(key)}`;
  if (section === 'soft') return `Soft ${key}`;
  return `Hard ${key}`;
}

function runsOf(indices) {
  const runs = [];
  for (const i of indices) {
    const last = runs[runs.length - 1];
    if (last && i === last[last.length - 1] + 1) last.push(i);
    else runs.push([i]);
  }
  return runs;
}

function phraseRun(run) {
  const labels = run.map((i) => UP_LABEL(UPCARDS[i]));
  if (run[0] === 0 && run.length >= 2) return `${labels[labels.length - 1]} or less`;
  if (run[run.length - 1] === UPCARDS.length - 1 && run.length >= 3) return `${labels[0]} or higher`;
  if (run.length <= 3) return labels.join(', ');
  return `${labels[0]} through ${labels[labels.length - 1]}`;
}

function describeSet(indices) {
  if (indices.length === 1) return `against ${againstOne(indices[0])}`;
  const runs = runsOf(indices);
  // A scattered handful reads better as a flat list than as runs joined by "and".
  if (runs.length > 1 && indices.length <= 4) return indices.map((i) => UP_LABEL(UPCARDS[i])).join(', ');
  const phrases = runs.map(phraseRun);
  if (phrases.length === 1) return phrases[0];
  return `${phrases.slice(0, -1).join(', ')} and ${phrases[phrases.length - 1]}`;
}

function againstOne(index) {
  const up = UPCARDS[index];
  return up === 1 ? 'an ace' : `a ${up}`;
}

export function rowRule(section, key, rules) {
  const row = chartFor(rules)[section][key];
  const actions = row.map((code) => codeToAction(code, rules));

  const groups = new Map();
  actions.forEach((action, i) => {
    if (!groups.has(action)) groups.set(action, []);
    groups.get(action).push(i);
  });
  if (groups.size === 1) return `Always ${VERB[actions[0]]}.`;

  // Whichever action is left unsaid decides how long the rule runs. Try each
  // one as the "otherwise" and keep the shortest: fewest stretches of upcards
  // to spell out, then fewest upcards, then hit as the natural default.
  const preference = ['hit', 'stand', 'double', 'split', 'surrender'];
  const cost = (action) => {
    const rest = [...groups.entries()].filter(([a]) => a !== action);
    return [
      rest.reduce((n, [, idx]) => n + runsOf(idx).length, 0),
      rest.reduce((n, [, idx]) => n + idx.length, 0),
      preference.indexOf(action),
    ];
  };
  const majority = [...groups.keys()].sort((a, b) => {
    const [x, y] = [cost(a), cost(b)];
    return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
  })[0];

  const rest = [...groups.entries()].filter(([a]) => a !== majority).sort((a, b) => a[1][0] - b[1][0]);
  if (rest.length === 1 && rest[0][1].length === 1) {
    return `Always ${VERB[majority]}, except ${VERB[rest[0][0]]} ${describeSet(rest[0][1])}.`;
  }
  return `${rest.map(([a, idx]) => `${VERB_CAP[a]} ${describeSet(idx)}.`).join(' ')} Otherwise ${VERB[majority]}.`;
}
