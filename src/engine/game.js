import { handTotal, addCard } from './cards.js';
import { doubleAllowedOn } from './rules.js';
import { newShoe, drawFrom, pullRank, pullExcluding } from './shoe.js';

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

const newHand = (cards, bet, fromSplit = false) => ({
  cards: [...cards], bet, doubled: false, stood: false, bust: false, surrendered: false, fromSplit,
});

export const handDone = (h) => h.stood || h.bust || h.surrendered;

// With no shoe passed in, the round gets a private one. Counting hands over a
// persistent shoe instead, so the cards that leave it stay gone.
export function startRound({ playerCards, upcard, rules, bet = 1, rng = Math.random, shoe = null }) {
  const live = shoe || newShoe(rules.decks, rng);
  const taken = [];
  for (const c of [...playerCards, upcard]) { if (pullRank(live, c)) taken.push(c); }

  // Post-peek: the hole card is never the one that would make blackjack.
  const natural = upcard === 1 ? 10 : upcard === 10 ? 1 : null;
  const hole = natural === null ? drawFrom(live) : pullExcluding(live, natural, rng);
  const holeIndex = taken.length;
  taken.push(hole);

  return {
    rules,
    bet,
    shoe: live,
    ownShoe: !shoe,
    taken,
    holeIndex,
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

// Everything the player can see right now. The hole card sits in the taken list
// from the start but stays out of this until it is turned over.
export function exposedCards(game) {
  if (game.revealed) return [...game.taken];
  return game.taken.filter((_, i) => i !== game.holeIndex);
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

function draw(game) {
  const card = drawFrom(game.shoe);
  game.taken.push(card);
  return card;
}

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
