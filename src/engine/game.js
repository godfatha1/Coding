import { handTotal, addCard } from './cards.js';
import { doubleAllowedOn } from './rules.js';

// Playing the hand out for money.
//
// Cards come from a real shoe of the configured size with the three cards you
// can already see removed, shuffled properly. The shoe is built fresh each
// round: which hand you are dealt is chosen by the drill, not by the shoe, so
// carrying a shoe between hands would only look like continuity.
//
// The dealer never holds blackjack. The app's odds are all quoted after the
// dealer peeks, and the drill never deals the player a natural either, so
// leaving blackjack out of both sides keeps the money consistent with every
// number on screen instead of quietly running worse than them.

export function createShoe(decks, exclude = [], rng = Math.random) {
  const cards = [];
  for (let d = 0; d < decks; d += 1) {
    for (let v = 1; v <= 10; v += 1) {
      for (let n = v === 10 ? 16 : 4; n > 0; n -= 1) cards.push(v);
    }
  }
  for (const c of exclude) {
    const i = cards.indexOf(c);
    if (i >= 0) cards.splice(i, 1);
  }
  for (let i = cards.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [cards[i], cards[j]] = [cards[j], cards[i]];
  }
  return cards;
}

const newHand = (cards, bet, fromSplit = false) => ({
  cards: [...cards], bet, doubled: false, stood: false, bust: false, surrendered: false, fromSplit,
});

export const handDone = (h) => h.stood || h.bust || h.surrendered;

export function startRound({ playerCards, upcard, rules, bet = 1, rng = Math.random }) {
  const shoe = createShoe(rules.decks, [...playerCards, upcard], rng);
  const natural = upcard === 1 ? 10 : upcard === 10 ? 1 : null;
  let at = shoe.length - 1;
  while (natural !== null && at >= 0 && shoe[at] === natural) at -= 1;
  const hole = shoe.splice(at, 1)[0];

  return {
    rules,
    bet,
    shoe,
    hole,
    upcard,
    dealerCards: [upcard],
    revealed: false,
    hands: [newHand(playerCards, bet)],
    active: 0,
    stage: 'player',
    result: null,
  };
}

export function activeHand(game) {
  return game.hands[game.active] || null;
}

export function legalMoves(game) {
  if (game.stage !== 'player') return [];
  const h = activeHand(game);
  if (!h || handDone(h)) return [];
  const moves = ['hit', 'stand'];
  const fresh = h.cards.length === 2;
  const { total } = handTotal(h.cards);
  if (fresh && (!h.fromSplit || game.rules.das) && doubleAllowedOn(total, game.rules)) moves.push('double');
  if (fresh && h.cards[0] === h.cards[1] && game.hands.length === 1) moves.push('split');
  if (fresh && game.hands.length === 1 && !h.fromSplit && game.rules.surrender) moves.push('surrender');
  return moves;
}

const draw = (game) => game.shoe.pop();

function advance(game) {
  while (game.active < game.hands.length && handDone(game.hands[game.active])) game.active += 1;
  if (game.active >= game.hands.length) finishRound(game);
}

function finishRound(game) {
  game.stage = 'dealer';
  game.revealed = true;
  game.dealerCards.push(game.hole);

  if (game.hands.some((h) => !h.bust && !h.surrendered)) {
    for (;;) {
      const { total, soft } = handTotal(game.dealerCards);
      if (total > 21) break;
      if (total > 17 || (total === 17 && !(game.rules.hitSoft17 && soft))) break;
      game.dealerCards.push(draw(game));
    }
  }

  const dealerTotal = handTotal(game.dealerCards).total;
  const dealerBust = dealerTotal > 21;
  let delta = 0;
  let wagered = 0;

  const perHand = game.hands.map((h) => {
    const total = handTotal(h.cards).total;
    wagered += h.bet;
    let payout;
    let outcome;
    if (h.surrendered) { payout = -h.bet / 2; outcome = 'surrendered'; }
    else if (h.bust) { payout = -h.bet; outcome = 'bust'; }
    else if (dealerBust) { payout = h.bet; outcome = 'win'; }
    else if (total > dealerTotal) { payout = h.bet; outcome = 'win'; }
    else if (total === dealerTotal) { payout = 0; outcome = 'push'; }
    else { payout = -h.bet; outcome = 'lose'; }
    delta += payout;
    return { total, payout, outcome, bust: h.bust, surrendered: h.surrendered, doubled: h.doubled, bet: h.bet };
  });

  game.stage = 'done';
  game.result = { delta, wagered, perHand, dealerTotal, dealerBust };
}

export function act(game, action) {
  if (game.stage !== 'player') return game;
  const h = activeHand(game);
  if (!h || !legalMoves(game).includes(action)) return game;

  if (action === 'hit') {
    h.cards.push(draw(game));
    if (handTotal(h.cards).total > 21) h.bust = true;
  } else if (action === 'stand') {
    h.stood = true;
  } else if (action === 'double') {
    h.bet *= 2;
    h.doubled = true;
    h.cards.push(draw(game));
    if (handTotal(h.cards).total > 21) h.bust = true;
    else h.stood = true;
  } else if (action === 'surrender') {
    h.surrendered = true;
  } else if (action === 'split') {
    const rank = h.cards[0];
    const second = newHand([rank], h.bet, true);
    h.cards = [rank];
    h.fromSplit = true;
    game.hands.push(second);
    for (const hand of game.hands) {
      hand.cards.push(draw(game));
      // Split aces take one card each and that is the hand.
      if (rank === 1) hand.stood = true;
    }
  }

  advance(game);
  return game;
}
