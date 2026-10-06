// Checks the odds engine against published blackjack figures.
import { analyzeHand, buildProbs, dealerOutcomes } from '../src/engine/odds.js';
import { DEFAULT_RULES, normalizeRules } from '../src/engine/rules.js';
import { handTotal } from '../src/engine/cards.js';

let failures = 0;
const pct = (x) => (x * 100).toFixed(2) + '%';
function check(name, actual, expected, tol) {
  const ok = Math.abs(actual - expected) <= tol;
  if (!ok) failures += 1;
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}: ${actual.toFixed(4)} (expected ~${expected} +/-${tol})`);
}
function assert(name, cond, note = '') {
  if (!cond) failures += 1;
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${note ? ' — ' + note : ''}`);
}

const S17 = normalizeRules({ decks: 6, hitSoft17: false, das: true, surrender: true });
const INFINITE = { ...DEFAULT_RULES, decks: 20000 };

// 1. Every distribution is a proper probability distribution.
{
  const d = dealerOutcomes(9, buildProbs([10, 6, 9], 6), S17);
  check('dealer distribution sums to 1', Object.values(d).reduce((a, b) => a + b, 0), 1, 1e-9);
  const a = analyzeHand([10, 6], 9, S17);
  for (const [name, entry] of Object.entries(a.actions)) {
    check(`${name} distribution sums to 1`, [...entry.dist.values()].reduce((x, y) => x + y, 0), 1, 1e-9);
    check(`${name} win+push+lose = 1`, entry.win + entry.push + entry.lose, 1, 1e-9);
  }
}

// 2. Dealer bust rates, 6 decks, S17, after the peek.
const bustTargets = { 2: 0.3536, 3: 0.3739, 4: 0.3978, 5: 0.4258, 6: 0.4219, 7: 0.2623, 8: 0.2386, 9: 0.2334, 10: 0.2345, 1: 0.1698 };
for (const up of [2, 3, 4, 5, 6, 7, 8, 9, 10, 1]) {
  const d = dealerOutcomes(up, buildProbs([up], 6), S17);
  check(`dealer ${up === 1 ? 'A' : up} busts`, d.bust, bustTargets[up], 0.012);
}

// 3. The classic infinite-deck dealer tables for upcards 6 and 7.
{
  const d6 = dealerOutcomes(6, buildProbs([], 20000), INFINITE);
  const want6 = { 17: 0.1654, 18: 0.1063, 19: 0.1063, 20: 0.1017, 21: 0.0972, bust: 0.4232 };
  for (const k of Object.keys(want6)) check(`infinite-deck dealer 6 -> ${k}`, d6[k], want6[k], 0.0015);
  const d7 = dealerOutcomes(7, buildProbs([], 20000), INFINITE);
  const want7 = { 17: 0.3686, 18: 0.1378, 19: 0.0786, 20: 0.0786, 21: 0.0741, bust: 0.2623 };
  for (const k of Object.keys(want7)) check(`infinite-deck dealer 7 -> ${k}`, d7[k], want7[k], 0.0015);
}

// 4. Stand values follow straight from the dealer table.
{
  const d6 = dealerOutcomes(6, buildProbs([], 20000), INFINITE);
  const a = analyzeHand([10, 10], 6, INFINITE);
  const expected = d6.bust + d6[17] + d6[18] + d6[19] - d6[21];
  check('stand 20 vs 6 matches dealer table', a.actions.stand.ev, expected, 1e-6);
  check('stand 20 vs 6 win chance', a.actions.stand.win, 0.8012, 0.002);
  check('stand 20 vs 6 push chance', a.actions.stand.push, 0.1017, 0.002);
}

// 5. Published expected values that do not shift much with card removal.
const evs = [
  ['stand', [10, 7], 2, -0.1530], ['stand', [10, 6], 10, -0.5404], ['stand', [10, 2], 4, -0.2105],
  ['stand', [10, 9], 6, 0.4956], ['hit', [10, 6], 10, -0.5398], ['hit', [10, 6], 9, -0.5096],
  ['hit', [10, 2], 4, -0.2120], ['double', [6, 5], 10, 0.1763], ['hit', [1, 7], 9, -0.1012],
  ['stand', [1, 7], 9, -0.1829],
];
for (const [action, hand, up, expected] of evs) {
  const a = analyzeHand(hand, up, S17);
  check(`${action} ${hand.join('+')} vs ${up === 1 ? 'A' : up}`, a.actions[action].ev, expected, 0.012);
}

// 6. Splitting eights against a ten is the least bad play, as the book says.
{
  const a = analyzeHand([8, 8], 10, S17);
  assert('split 8s vs 10 beats hit and stand', a.actions.split.ev > a.actions.hit.ev && a.actions.split.ev > a.actions.stand.ev,
    `split ${a.actions.split.ev.toFixed(3)} hit ${a.actions.hit.ev.toFixed(3)} stand ${a.actions.stand.ev.toFixed(3)}`);
  check('split 8s vs 10 EV', a.actions.split.ev, -0.48, 0.07);
}

// 7. Surrender is worth taking on 16 against a ten and not on 16 against a seven.
{
  const vs10 = analyzeHand([10, 6], 10, S17);
  const vs7 = analyzeHand([10, 6], 7, S17);
  assert('16 vs 10: surrender is best', vs10.best.action === 'surrender', `best = ${vs10.best.action}`);
  assert('16 vs 7: surrender is not best', vs7.best.action !== 'surrender', `best = ${vs7.best.action}`);
}

// 8. End to end: the return of perfect play over every opening deal.
// Published house edge for 6 decks, S17, DAS, late surrender is about 0.40%.
// This engine leaves out resplits, which costs the player a few hundredths.
{
  const decks = 6;
  const n = (v) => (v === 10 ? 16 : 4) * decks;
  const N = 52 * decks;
  let ev = 0;
  let weightSum = 0;
  for (let p1 = 1; p1 <= 10; p1 += 1) {
    for (let p2 = 1; p2 <= 10; p2 += 1) {
      for (let up = 1; up <= 10; up += 1) {
        const w = (n(p1) / N)
          * ((n(p2) - (p2 === p1 ? 1 : 0)) / (N - 1))
          * ((n(up) - (up === p1 ? 1 : 0) - (up === p2 ? 1 : 0)) / (N - 2));
        if (w <= 0) continue;
        weightSum += w;
        const a = analyzeHand([p1, p2], up, S17);
        const holeForBJ = up === 1 ? 10 : up === 10 ? 1 : null;
        const pBJ = holeForBJ ? a.probs[holeForBJ] : 0;
        ev += w * (handTotal([p1, p2]).total === 21
          ? (1 - pBJ) * S17.blackjackPays
          : pBJ * -1 + (1 - pBJ) * a.best.ev);
      }
    }
  }
  check('opening-deal weights sum to 1', weightSum, 1, 1e-9);
  console.log(`      return of perfect play: ${pct(ev)} (house edge ${pct(-ev)})`);
  check('house edge', -ev, 0.0042, 0.0015);
}

console.log(failures === 0 ? '\nAll odds checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
