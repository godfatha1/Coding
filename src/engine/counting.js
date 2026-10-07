// Card counting.
//
// A count system is just a value per rank plus a little bookkeeping, so the
// engine is generic and adding a system is data rather than code. Only Hi-Lo is
// turned on; the rest are here with their real tag values so the training modes
// planned around them have something to build on.
//
// Where this is heading, roughly in order:
//   1. running count            <- built
//   2. true count conversion    (running / decks left, asked separately)
//   3. bet ramp by true count   (how much to put out at each count)
//   4. deviations               (Illustrious 18 and Fab 4, chart cells that move)
//   5. level-two systems        (Omega II, Zen: two-point tags, slower but sharper)
//   6. side counts              (aces kept apart from the main count)

export const COUNT_SYSTEMS = {
  hilo: {
    id: 'hilo',
    name: 'Hi-Lo',
    level: 1,
    ready: true,
    balanced: true,
    blurb: 'Low cards +1, tens and aces −1. The one almost everybody learns first.',
    values: { 1: -1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 0, 8: 0, 9: 0, 10: -1 },
  },
  ko: {
    id: 'ko',
    name: 'Knock-Out',
    level: 1,
    ready: false,
    balanced: false,
    blurb: 'Hi-Lo with the seven counted as low, so there is no true count to work out.',
    values: { 1: -1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 0, 9: 0, 10: -1 },
    initial: (decks) => -4 * (decks - 1),
  },
  hiopt1: {
    id: 'hiopt1',
    name: 'Hi-Opt I',
    level: 1,
    ready: false,
    balanced: true,
    blurb: 'Aces and deuces sit out, which sharpens play but wants a side count for betting.',
    values: { 1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 1, 7: 0, 8: 0, 9: 0, 10: -1 },
  },
  omega2: {
    id: 'omega2',
    name: 'Omega II',
    level: 2,
    ready: false,
    balanced: true,
    blurb: 'Two-point tags on the middle cards. More accurate, much harder to hold.',
    values: { 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 1, 8: 0, 9: -1, 10: -2 },
  },
  zen: {
    id: 'zen',
    name: 'Zen Count',
    level: 2,
    ready: false,
    balanced: true,
    blurb: 'Two-point tags with the ace counted at −1, so no side count is needed.',
    values: { 1: -1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 1, 8: 0, 9: 0, 10: -2 },
  },
};

export const readySystems = () => Object.values(COUNT_SYSTEMS).filter((s) => s.ready);
export const getSystem = (id) => (COUNT_SYSTEMS[id]?.ready ? COUNT_SYSTEMS[id] : COUNT_SYSTEMS.hilo);

export const countValue = (system, card) => system.values[card] ?? 0;

export function startingCount(system, decks) {
  return system.balanced ? 0 : system.initial(decks);
}

export function runningCount(system, cards, decks) {
  let n = startingCount(system, decks);
  for (const c of cards) n += countValue(system, c);
  return n;
}

// A balanced count has to be divided by the decks still to come. Half-deck
// resolution is what people actually estimate at a table, so that is the
// granularity offered rather than pretending to count cards.
export function decksRemaining(cardsLeft, { round = 0.5 } = {}) {
  const decks = cardsLeft / 52;
  if (!round) return decks;
  return Math.max(round, Math.round(decks / round) * round);
}

export function trueCount(system, running, cardsLeft) {
  if (!system.balanced) return null;
  return running / decksRemaining(cardsLeft);
}

export const formatCount = (n) => (n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : '0');
export const formatTrue = (n) => (n === null ? '—' : `${n > 0.049 ? '+' : n < -0.049 ? '−' : ''}${Math.abs(n).toFixed(1)}`);

// A balanced system's tags must cancel over a whole deck, or the true count
// means nothing. This is the check every system in the table has to pass.
export function tagsBalance(system) {
  let sum = 0;
  for (let v = 1; v <= 10; v += 1) sum += countValue(system, v) * (v === 10 ? 16 : 4);
  return sum;
}
