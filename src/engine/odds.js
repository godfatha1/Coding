import { addCard, handTotal, isPair } from './cards.js';
import { doubleAllowedOn } from './rules.js';

// Exact round-outcome distributions for every legal action.
//
// Model: the cards already on the table are removed from the shoe, then every
// later draw is taken from that depleted shoe held fixed (no further removal).
// At 4-8 decks this sits within a couple hundredths of a percent of a full
// combinatorial solve, and it keeps a hand solvable in well under a millisecond.
// Resplitting is not modelled; a split is valued as two independent hands.

const ACTION_ORDER = ['stand', 'hit', 'double', 'split', 'surrender'];

export function buildProbs(knownCards, decks) {
  const counts = new Array(11).fill(0);
  for (let v = 1; v <= 10; v += 1) counts[v] = (v === 10 ? 16 : 4) * decks;
  for (const c of knownCards) counts[c] -= 1;
  let total = 0;
  for (let v = 1; v <= 10; v += 1) {
    if (counts[v] < 0) counts[v] = 0;
    total += counts[v];
  }
  const probs = new Array(11).fill(0);
  for (let v = 1; v <= 10; v += 1) probs[v] = counts[v] / total;
  return probs;
}

function withoutCard(probs, excluded) {
  const out = new Array(11).fill(0);
  const remaining = 1 - probs[excluded];
  if (remaining <= 0) return probs;
  for (let v = 1; v <= 10; v += 1) out[v] = v === excluded ? 0 : probs[v] / remaining;
  return out;
}

// --- dealer ----------------------------------------------------------------

// Probability the dealer ends on each standing total, or busts. When the dealer
// peeks, the hole card that would make blackjack is removed from the first draw,
// so these are the odds the player actually faces at decision time.
export function dealerOutcomes(upcard, probs, rules) {
  const memo = new Map();
  const standOn = (total, soft) => total > 17 || (total === 17 && !(rules.hitSoft17 && soft));

  function resolve(total, soft) {
    const key = total * 2 + (soft ? 1 : 0);
    const cached = memo.get(key);
    if (cached) return cached;
    const out = { 17: 0, 18: 0, 19: 0, 20: 0, 21: 0, bust: 0 };
    if (standOn(total, soft)) {
      out[total] = 1;
    } else {
      for (let c = 1; c <= 10; c += 1) {
        const p = probs[c];
        if (!p) continue;
        const next = addCard(total, soft, c);
        if (next.bust) { out.bust += p; continue; }
        const sub = resolve(next.total, next.soft);
        for (const k of Object.keys(out)) out[k] += p * sub[k];
      }
    }
    memo.set(key, out);
    return out;
  }

  const start = upcard === 1 ? { total: 11, soft: true } : { total: upcard, soft: false };
  let holeProbs = probs;
  if (rules.peek) {
    if (upcard === 1) holeProbs = withoutCard(probs, 10);
    else if (upcard === 10) holeProbs = withoutCard(probs, 1);
  }
  const dist = { 17: 0, 18: 0, 19: 0, 20: 0, 21: 0, bust: 0 };
  for (let c = 1; c <= 10; c += 1) {
    const p = holeProbs[c];
    if (!p) continue;
    const next = addCard(start.total, start.soft, c);
    if (next.bust) { dist.bust += p; continue; }
    const sub = resolve(next.total, next.soft);
    for (const k of Object.keys(dist)) dist[k] += p * sub[k];
  }
  return dist;
}

// --- payout distributions --------------------------------------------------
// A distribution is a Map from payout (in units of the original bet) to probability.

const LOSS = new Map([[-1, 1]]);

function mixInto(target, dist, weight) {
  for (const [amount, p] of dist) target.set(amount, (target.get(amount) || 0) + p * weight);
  return target;
}

function scaleAmounts(dist, factor) {
  const out = new Map();
  for (const [amount, p] of dist) {
    const a = amount * factor;
    out.set(a, (out.get(a) || 0) + p);
  }
  return out;
}

function convolve(a, b) {
  const out = new Map();
  for (const [x, px] of a) {
    for (const [y, py] of b) {
      const s = x + y;
      out.set(s, (out.get(s) || 0) + px * py);
    }
  }
  return out;
}

export function distEV(dist) {
  let ev = 0;
  for (const [amount, p] of dist) ev += amount * p;
  return ev;
}

export function distOutcomes(dist) {
  let win = 0;
  let push = 0;
  let lose = 0;
  for (const [amount, p] of dist) {
    if (amount > 1e-12) win += p;
    else if (amount < -1e-12) lose += p;
    else push += p;
  }
  return { win, push, lose };
}

// --- the solver ------------------------------------------------------------

function createSolver(probs, dealer, rules) {
  const standCache = new Map();
  const bestCache = new Map();

  function standDist(total) {
    const cached = standCache.get(total);
    if (cached) return cached;
    let win = dealer.bust;
    let push = 0;
    let lose = 0;
    for (let d = 17; d <= 21; d += 1) {
      const p = dealer[d];
      if (!p) continue;
      if (total > d) win += p;
      else if (total === d) push += p;
      else lose += p;
    }
    const dist = new Map();
    if (win) dist.set(1, win);
    if (push) dist.set(0, push);
    if (lose) dist.set(-1, lose);
    standCache.set(total, dist);
    return dist;
  }

  // Best of hit / stand from here on. Doubling and splitting are first-decision
  // only, so they never appear inside this recursion.
  function bestDist(total, soft) {
    const key = total * 2 + (soft ? 1 : 0);
    const cached = bestCache.get(key);
    if (cached) return cached;
    const stand = standDist(total);
    const hit = hitDist(total, soft);
    const chosen = distEV(hit) > distEV(stand) ? hit : stand;
    bestCache.set(key, chosen);
    return chosen;
  }

  function hitDist(total, soft) {
    const out = new Map();
    for (let c = 1; c <= 10; c += 1) {
      const p = probs[c];
      if (!p) continue;
      const next = addCard(total, soft, c);
      mixInto(out, next.bust ? LOSS : bestDist(next.total, next.soft), p);
    }
    return out;
  }

  function doubleDist(total, soft) {
    const out = new Map();
    for (let c = 1; c <= 10; c += 1) {
      const p = probs[c];
      if (!p) continue;
      const next = addCard(total, soft, c);
      mixInto(out, next.bust ? LOSS : standDist(next.total), p);
    }
    return scaleAmounts(out, 2);
  }

  function splitHandDist(rank) {
    const out = new Map();
    for (let c = 1; c <= 10; c += 1) {
      const p = probs[c];
      if (!p) continue;
      const next = addCard(rank === 1 ? 11 : rank, rank === 1, c);
      if (rank === 1) {
        // Split aces take one card each and stand. A 21 here pays even money.
        mixInto(out, standDist(next.total), p);
        continue;
      }
      let chosen = bestDist(next.total, next.soft);
      if (rules.das && doubleAllowedOn(next.total, rules)) {
        const dbl = doubleDist(next.total, next.soft);
        if (distEV(dbl) > distEV(chosen)) chosen = dbl;
      }
      mixInto(out, chosen, p);
    }
    return out;
  }

  return { standDist, hitDist, doubleDist, splitHandDist };
}

// --- public API ------------------------------------------------------------

export function analyzeHand(playerCards, upcard, rules) {
  const probs = buildProbs([...playerCards, upcard], rules.decks);
  const dealer = dealerOutcomes(upcard, probs, rules);
  const solver = createSolver(probs, dealer, rules);
  const { total, soft } = handTotal(playerCards);

  const dists = {
    stand: solver.standDist(total),
    hit: solver.hitDist(total, soft),
  };
  if (playerCards.length === 2 && doubleAllowedOn(total, rules)) {
    dists.double = solver.doubleDist(total, soft);
  }
  if (isPair(playerCards)) {
    const one = solver.splitHandDist(playerCards[0]);
    dists.split = convolve(one, one);
  }
  if (rules.surrender && playerCards.length === 2) {
    dists.surrender = new Map([[-0.5, 1]]);
  }

  const actions = {};
  for (const name of ACTION_ORDER) {
    const dist = dists[name];
    if (!dist) continue;
    actions[name] = { action: name, ev: distEV(dist), ...distOutcomes(dist), dist };
  }
  const ranked = Object.values(actions).sort((a, b) => b.ev - a.ev);
  return { actions, ranked, best: ranked[0], dealer, total, soft, probs };
}
