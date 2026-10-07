// Counting: the tag tables, the shoe that carries between hands, and the one
// invariant that catches almost everything — a balanced count run over a whole
// shoe has to come back to where it started.
import { COUNT_SYSTEMS, getSystem, countValue, runningCount, startingCount, trueCount, decksRemaining, tagsBalance, readySystems, formatCount, formatTrue } from '../src/engine/counting.js';
import { newShoe, cardsLeft, cardsDealt, pullRank, canDeal, needsShuffle, penetrationSoFar, drawFrom } from '../src/engine/shoe.js';
import { startRound, act, exposedCards, legalMoves } from '../src/engine/game.js';
import { normalizeRules } from '../src/engine/rules.js';
import { lookupStrategy } from '../src/engine/strategy.js';

let failures = 0;
const say = (ok, msg) => { if (!ok) failures += 1; console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); };

// 1. Every tag table is what it claims to be.
for (const sys of Object.values(COUNT_SYSTEMS)) {
  const sum = tagsBalance(sys);
  if (sys.balanced) say(sum === 0, `${sys.name} is balanced (tags sum to ${sum} per deck)`);
  else say(sum !== 0, `${sys.name} is unbalanced (tags sum to ${sum} per deck)`);
  const ranks = Object.keys(sys.values).map(Number).sort((a, b) => a - b);
  say(ranks.join() === '1,2,3,4,5,6,7,8,9,10', `${sys.name} gives every rank a tag`);
  if (!sys.balanced) say(typeof sys.initial === 'function', `${sys.name} says where its count starts`);
}
say(readySystems().length >= 1 && readySystems().every((s) => s.ready), `${readySystems().length} system ready to drill, ${Object.keys(COUNT_SYSTEMS).length - readySystems().length} written down for later`);
say(getSystem('omega2').id === 'hilo', 'a system that is not ready falls back to Hi-Lo');
say(getSystem('nonsense').id === 'hilo', 'an unknown system falls back to Hi-Lo');

// 2. Hi-Lo on known hands.
{
  const hilo = COUNT_SYSTEMS.hilo;
  say(runningCount(hilo, [2, 3, 4, 5, 6], 6) === 5, 'five low cards make +5');
  say(runningCount(hilo, [10, 10, 1], 6) === -3, 'two tens and an ace make −3');
  say(runningCount(hilo, [7, 8, 9], 6) === 0, 'sevens through nines do nothing');
  say(startingCount(hilo, 6) === 0, 'a balanced count starts at zero');
  say(COUNT_SYSTEMS.ko.initial(6) === -20, 'Knock-Out starts at −20 on six decks');
}

// 3. True count and decks remaining.
{
  const hilo = COUNT_SYSTEMS.hilo;
  say(decksRemaining(312) === 6, '312 cards is six decks');
  say(decksRemaining(260) === 5, '260 cards is five decks');
  say(decksRemaining(286) === 5.5, '286 cards rounds to five and a half');
  say(decksRemaining(10) === 0.5, 'a nearly empty shoe never divides by less than half a deck');
  say(trueCount(hilo, 12, 312) === 2, 'running +12 over six decks is a true +2');
  say(trueCount(COUNT_SYSTEMS.ko, 5, 312) === null, 'an unbalanced count has no true count');
  say(formatCount(0) === '0' && formatCount(3) === '+3' && formatCount(-3) === '−3', 'counts read with a sign');
  say(formatTrue(null) === '—', 'a missing true count shows a dash');
}

// 4. The shoe.
{
  const shoe = newShoe(8);
  say(cardsLeft(shoe) === 416, 'eight decks is 416 cards');
  say(pullRank(shoe, 10) && cardsLeft(shoe) === 415, 'pulling a rank takes exactly one card');
  const tens = shoe.cards.filter((c) => c === 10).length;
  say(tens === 8 * 16 - 1, `one ten is gone, ${tens} left`);
  say(canDeal(shoe, [1, 1, 1, 1]) && !canDeal(shoe, new Array(33).fill(1)), 'the shoe knows what it can still supply');
  say(!needsShuffle(shoe, 0.75), 'a full shoe does not need shuffling');
  for (let i = 0; i < 320; i += 1) drawFrom(shoe);
  say(needsShuffle(shoe, 0.75), `past the cut card it does (${(penetrationSoFar(shoe) * 100).toFixed(0)}% dealt)`);
  say(cardsDealt(shoe) === 321, 'dealt and remaining always add back to the shoe');
}

// 5. The hole card is not counted until it is turned over.
{
  const rules = normalizeRules({ decks: 6 });
  const shoe = newShoe(6);
  const g = startRound({ playerCards: [10, 6], upcard: 9, rules, bet: 1, shoe });
  const seen = exposedCards(g);
  say(seen.length === 3, 'three cards are visible at the deal');
  say(g.taken.length === 4, 'four cards have left the shoe, counting the hole');
  const holeShowing = seen.length !== new Set([...seen]).size ? null : seen.includes(g.hole);
  say(holeShowing !== true || g.taken.filter((c) => c === g.hole).length > 1, 'the hole card is not among them');
  while (g.stage === 'player') act(g, 'stand');
  say(exposedCards(g).length === g.taken.length, 'once the hand is over every card is visible');
}

// 6. The invariant: deal a whole shoe out and a balanced count returns to zero.
for (const decks of [6, 8]) {
  const rules = normalizeRules({ decks });
  const hilo = COUNT_SYSTEMS.hilo;
  const shoe = newShoe(decks);
  let running = 0;
  let hands = 0;
  while (cardsLeft(shoe) > 30) {
    const a = 2 + Math.floor(Math.random() * 9);
    const b = 2 + Math.floor(Math.random() * 9);
    const up = 1 + Math.floor(Math.random() * 10);
    if (!canDeal(shoe, [a, b, up])) break;
    const before = running;
    const g = startRound({ playerCards: [a, b], upcard: up, rules, bet: 1, shoe });
    const sync = () => {
      let tally = 0;
      for (const c of exposedCards(g)) tally += countValue(hilo, c);
      running = before + tally;
    };
    sync();
    while (g.stage === 'player') {
      const h = g.hands[g.active];
      const want = lookupStrategy(h.cards, up, rules).action;
      act(g, legalMoves(g).includes(want) ? want : (want === 'double' ? 'hit' : 'stand'));
      sync();
    }
    hands += 1;
  }
  // Whatever is left in the shoe has to be the exact mirror of what was seen.
  let inShoe = 0;
  for (const c of shoe.cards) inShoe += countValue(hilo, c);
  say(running + inShoe === 0, `${decks} decks, ${hands} hands: seen ${running >= 0 ? '+' : ''}${running} and unseen ${inShoe >= 0 ? '+' : ''}${inShoe} cancel exactly`);
}

console.log(failures === 0 ? '\nAll counting checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
