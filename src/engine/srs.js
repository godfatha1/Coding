// Spaced repetition over chart cells.
//
// Intervals are counted in hands, not days, because this is a drill: a hand due
// in two means it comes back inside the next couple of hands. Three correct
// answers in a row with confidence retires a cell for good — the point of the
// app is to stop showing you hands you already own.

export const SRS_DEFAULTS = {
  retireStreak: 3,
  maxInterval: 400,
  minEase: 1.3,
  maxEase: 3.0,
};

export const STAGES = ['new', 'learning', 'review', 'retired'];

export function newCard(id) {
  return {
    id,
    stage: 'new',
    ease: 2.4,
    interval: 0,
    due: 0,
    reps: 0,
    wrong: 0,
    shaky: 0,       // answers marked low confidence
    hcStreak: 0,    // correct answers in a row, with confidence
    lastSeen: null,
    retiredBy: null,
  };
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function gradeCard(card, { correct, lowConfidence, step, settings = {} }) {
  const cfg = { ...SRS_DEFAULTS, ...settings };
  const next = { ...card };
  next.reps += 1;
  next.lastSeen = step;

  if (!correct) {
    next.wrong += 1;
    next.hcStreak = 0;
    next.ease = clamp(next.ease - 0.25, cfg.minEase, cfg.maxEase);
    next.interval = 1;
    next.stage = 'learning';
  } else if (lowConfidence) {
    next.shaky += 1;
    next.hcStreak = 0;
    next.ease = clamp(next.ease - 0.1, cfg.minEase, cfg.maxEase);
    next.interval = next.stage === 'new' ? 2 : clamp(Math.round(Math.max(next.interval, 2) * 1.15), 2, 12);
    next.stage = 'learning';
  } else {
    next.hcStreak += 1;
    next.ease = clamp(next.ease + 0.1, cfg.minEase, cfg.maxEase);
    next.interval = next.stage === 'new'
      ? 4
      : clamp(Math.round(Math.max(next.interval, 2) * next.ease), 3, cfg.maxInterval);
    next.stage = next.hcStreak >= cfg.retireStreak ? 'retired' : 'review';
    if (next.stage === 'retired') next.retiredBy = 'mastered';
  }

  next.due = step + next.interval;
  return next;
}

export function retireCard(card, step) {
  return { ...card, stage: 'retired', retiredBy: 'manual', lastSeen: step, due: step + SRS_DEFAULTS.maxInterval };
}

export function reviveCard(card, step) {
  return { ...card, stage: 'review', retiredBy: null, hcStreak: 0, interval: 6, due: step + 6 };
}

export function isShaky(card) {
  return card.stage !== 'retired' && (card.wrong > 0 || card.shaky > 0);
}

export function cardCounts(cards) {
  const counts = { new: 0, learning: 0, review: 0, retired: 0, shaky: 0, total: 0 };
  for (const c of cards) {
    counts[c.stage] += 1;
    counts.total += 1;
    if (isShaky(c)) counts.shaky += 1;
  }
  return counts;
}

export function dueCount(cards, step) {
  let n = 0;
  for (const c of cards) if (c.stage === 'learning' || c.stage === 'review') { if (c.due <= step) n += 1; }
  return n;
}

// Pick the next hand to show. Overdue cells come first, oldest debt first, but
// fresh cells keep entering: while the working set has room, and otherwise every
// few hands, so a long stretch of shaky hands never blocks the rest of the chart.
export function pickNext(cards, { step, lastId = null, sinceNew = 0, workingSet = 16, newEvery = 8, rng = Math.random } = {}) {
  const live = cards.filter((c) => c.stage !== 'retired');
  if (!live.length) return null;

  const pickFrom = (pool) => {
    if (!pool.length) return null;
    let choices = pool;
    if (pool.length > 1 && lastId) {
      const without = pool.filter((c) => c.id !== lastId);
      if (without.length) choices = without;
    }
    return choices[Math.floor(rng() * choices.length)];
  };

  const fresh = live.filter((c) => c.stage === 'new');
  const active = live.length - fresh.length;
  const due = live.filter((c) => c.stage !== 'new' && c.due <= step);

  if (fresh.length && (active < workingSet || sinceNew >= newEvery || !due.length)) {
    const chosen = pickFrom(fresh);
    if (chosen) return chosen;
  }

  if (due.length) {
    const oldest = Math.min(...due.map((c) => c.due));
    const chosen = pickFrom(due.filter((c) => c.due === oldest));
    if (chosen) return chosen;
  }

  const soonest = Math.min(...live.map((c) => c.due));
  return pickFrom(live.filter((c) => c.due === soonest)) || live[0];
}
