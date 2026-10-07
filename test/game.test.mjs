// The money game, checked against the odds engine that is already verified.
import { startRound, act, legalMoves, handDone } from '../src/engine/game.js';
import { newShoe, pullRank, cardsLeft } from '../src/engine/shoe.js';
import { normalizeRules } from '../src/engine/rules.js';
import { analyzeHand } from '../src/engine/odds.js';
import { handTotal } from '../src/engine/cards.js';
import { lookupStrategy } from '../src/engine/strategy.js';

let failures = 0;
const say = (ok, msg) => { if (!ok) failures += 1; console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); };
const near = (name, actual, expected, tol) => say(Math.abs(actual - expected) <= tol,
  `${name}: ${actual.toFixed(4)} (expected ~${expected.toFixed(4)} +/-${tol})`);

const RULES = normalizeRules({ decks: 8, hitSoft17: false, das: true, surrender: true });

// 1. The shoe is a real shoe with the visible cards taken out.
{
  const shoe = newShoe(8);
  for (const c of [10, 6, 9]) pullRank(shoe, c);
  say(cardsLeft(shoe) === 8 * 52 - 3, `an 8-deck shoe less three cards holds ${cardsLeft(shoe)} cards`);
  const counts = new Array(11).fill(0);
  for (const c of shoe.cards) counts[c] += 1;
  say(counts[10] === 8 * 16 - 1, 'one ten is gone');
  say(counts[6] === 8 * 4 - 1 && counts[9] === 8 * 4 - 1, 'one six and one nine are gone');
  say(counts[2] === 8 * 4, 'untouched ranks are whole');
  const sorted = [...shoe.cards].every((c, i, a) => i === 0 || a[i - 1] <= c);
  say(!sorted, 'the shoe is shuffled');
}

// 2. The dealer is never dealt a blackjack, against either card that could make one.
{
  let naturals = 0;
  for (let i = 0; i < 4000; i += 1) {
    for (const up of [1, 10]) {
      const g = startRound({ playerCards: [9, 7], upcard: up, rules: RULES, bet: 1 });
      if (handTotal([up, g.hole]).total === 21) naturals += 1;
    }
  }
  say(naturals === 0, 'no dealer blackjack in 8000 deals against an ace or a ten');
}

// 3. Settlement.
{
  const stand = () => { const g = startRound({ playerCards: [10, 10], upcard: 6, rules: RULES, bet: 10 }); act(g, 'stand'); return g; };
  let wins = 0; let pushes = 0; let losses = 0;
  for (let i = 0; i < 3000; i += 1) {
    const r = stand().result;
    if (r.delta > 0) wins += 1; else if (r.delta === 0) pushes += 1; else losses += 1;
    if (Math.abs(r.delta) !== 10 && r.delta !== 0) { say(false, `a flat bet settles at the stake, saw ${r.delta}`); break; }
  }
  say(wins > 0 && pushes > 0 && losses > 0, `twenty against a six wins, pushes and loses (${wins}/${pushes}/${losses})`);

  const g = startRound({ playerCards: [6, 5], upcard: 6, rules: RULES, bet: 10 });
  act(g, 'double');
  say(g.hands[0].bet === 20 && g.hands[0].cards.length === 3, 'doubling doubles the stake and takes exactly one card');
  say(Math.abs(g.result.delta) === 20 || g.result.delta === 0, `a doubled hand settles at twice the stake (${g.result.delta})`);

  const s = startRound({ playerCards: [10, 6], upcard: 10, rules: RULES, bet: 10 });
  act(s, 'surrender');
  say(s.result.delta === -5, `surrender gives back half the stake (${s.result.delta})`);
}

// 4. Splits.
{
  const g = startRound({ playerCards: [8, 8], upcard: 6, rules: RULES, bet: 10 });
  act(g, 'split');
  say(g.hands.length === 2, 'splitting makes a second hand');
  say(g.hands.every((h) => h.cards.length === 2 && h.cards[0] === 8), 'each split hand keeps an eight and draws one');
  say(g.hands.every((h) => h.bet === 10), 'the second hand is backed by its own stake');

  const aces = startRound({ playerCards: [1, 1], upcard: 6, rules: RULES, bet: 10 });
  act(aces, 'split');
  say(aces.stage === 'done', 'split aces take one card each and the round resolves');
  say(aces.hands.every((h) => h.cards.length === 2), 'split aces draw exactly one card');

  const resplit = startRound({ playerCards: [8, 8], upcard: 6, rules: RULES, bet: 10 });
  act(resplit, 'split');
  say(!legalMoves(resplit).includes('split'), 'no resplitting, which is what the odds assume');
  say(!legalMoves(resplit).includes('surrender'), 'no surrender after a split');
}

// 5. The dealer stands pat when every player hand is already dead.
{
  let checked = 0;
  for (let i = 0; i < 2000 && checked < 50; i += 1) {
    const g = startRound({ playerCards: [10, 6], upcard: 6, rules: RULES, bet: 1 });
    while (g.stage === 'player' && !handDone(g.hands[0])) act(g, 'hit');
    if (g.hands[0].bust) {
      checked += 1;
      if (g.dealerCards.length !== 2) { say(false, 'the dealer drew to a busted player'); break; }
    }
  }
  say(checked > 0, `the dealer stops at two cards when the player busts (${checked} rounds)`);
}

// 5b. The next card off the shoe is a fair draw, whatever the upcard.
// Pulling the drill's cards or the hole card by scanning from the top skews
// this badly, and the EV checks below only barely notice.
{
  const trials = 120000;
  for (const up of [10, 1, 6]) {
    const seen = new Array(11).fill(0);
    for (let i = 0; i < trials; i += 1) {
      const g = startRound({ playerCards: [9, 7], upcard: up, rules: RULES, bet: 1 });
      act(g, 'hit');
      seen[g.hands[0].cards[2]] += 1;
    }
    // Three cards are already out; the hole card is one more of unknown rank.
    const pool = 8 * 52 - 4;
    const worst = [];
    for (let v = 1; v <= 10; v += 1) {
      const count = (v === 10 ? 16 : 4) * 8 - (v === 9 || v === 7 ? 1 : 0) - (v === up ? 1 : 0);
      const expected = count / pool;
      const actual = seen[v] / trials;
      worst.push({ v, off: Math.abs(actual - expected) * 100, actual: actual * 100, expected: expected * 100 });
    }
    worst.sort((a, b) => b.off - a.off);
    const w = worst[0];
    say(w.off < 0.45, `against ${up === 1 ? 'an ace' : `a ${up}`} every rank is drawn at its true share (worst: ${w.v} at ${w.actual.toFixed(2)}% vs ${w.expected.toFixed(2)}%)`);
  }
}

// 6. Played out many times, a hand returns what the odds engine says it should.
{
  const trials = 120000;
  const cases = [
    { hand: [10, 6], up: 10, action: 'stand' },
    { hand: [10, 6], up: 10, action: 'hit' },
    { hand: [6, 5], up: 6, action: 'double' },
    { hand: [1, 7], up: 3, action: 'double' },
    { hand: [8, 8], up: 6, action: 'split' },
    { hand: [9, 9], up: 7, action: 'stand' },
  ];
  for (const c of cases) {
    const expected = analyzeHand(c.hand, c.up, RULES).actions[c.action].ev;
    let total = 0;
    for (let i = 0; i < trials; i += 1) {
      const g = startRound({ playerCards: c.hand, upcard: c.up, rules: RULES, bet: 1 });
      act(g, c.action);
      // Finish any hand the opening action left live, playing it the book way.
      while (g.stage === 'player') {
        const h = g.hands[g.active];
        const move = lookupStrategy(h.cards, c.up, RULES).action;
        act(g, legalMoves(g).includes(move) ? move : (move === 'double' ? 'hit' : 'stand'));
      }
      total += g.result.delta;
    }
    near(`${c.action} ${c.hand.join('+')} vs ${c.up === 1 ? 'A' : c.up}`, total / trials, expected, 0.014);
  }
}

console.log(failures === 0 ? '\nAll game checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
