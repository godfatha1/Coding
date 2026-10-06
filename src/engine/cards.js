// Cards are plain integers 1-10. 1 is an ace, 10 covers ten/jack/queen/king.
export const SUITS = [
  { id: 's', glyph: '♠', red: false },
  { id: 'h', glyph: '♥', red: true },
  { id: 'd', glyph: '♦', red: true },
  { id: 'c', glyph: '♣', red: false },
];

export const TEN_RANKS = ['10', 'J', 'Q', 'K'];

export function rankLabel(value) {
  if (value === 1) return 'A';
  if (value === 10) return '10';
  return String(value);
}

// The total a dealer or player would call out, with the ace worth 11 when it fits.
export function handTotal(cards) {
  let total = 0;
  let aces = 0;
  for (const c of cards) {
    total += c;
    if (c === 1) aces += 1;
  }
  let soft = false;
  if (aces > 0 && total + 10 <= 21) {
    total += 10;
    soft = true;
  }
  return { total, soft };
}

export function isPair(cards) {
  return cards.length === 2 && cards[0] === cards[1];
}

export function isBlackjack(cards) {
  return cards.length === 2 && handTotal(cards).total === 21;
}

// Add one card to a (total, soft) state. Returns bust:true when it busts.
export function addCard(total, soft, card) {
  let t = total;
  let s = soft;
  if (card === 1) {
    if (t + 11 <= 21) { t += 11; s = true; } else { t += 1; }
  } else {
    t += card;
  }
  if (t > 21 && s) { t -= 10; s = false; }
  return t > 21 ? { total: t, soft: false, bust: true } : { total: t, soft: s, bust: false };
}

export function describeHand(cards) {
  const { total, soft } = handTotal(cards);
  if (isPair(cards)) return `pair of ${rankLabel(cards[0])}${cards[0] === 10 ? 's' : "'s"}`;
  if (soft) return `soft ${total}`;
  return `hard ${total}`;
}

// A display card carries a suit and a face rank so the same value looks like a real card.
export function makeDisplayCard(value, rng = Math.random) {
  const suit = SUITS[Math.floor(rng() * SUITS.length)];
  const rank = value === 10 ? TEN_RANKS[Math.floor(rng() * TEN_RANKS.length)] : rankLabel(value);
  return { value, rank, suit: suit.glyph, red: suit.red };
}
