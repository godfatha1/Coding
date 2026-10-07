// A shoe that lives across hands, which is what makes a count mean anything.
//
// The drill still chooses which hand you are dealt, so those cards are pulled
// out of this shoe by rank rather than taken off the top. Everything else comes
// off the top in order. Either way the cards really do leave the shoe, so the
// count of what has been seen stays honest.

export function newShoe(decks, rng = Math.random) {
  const cards = [];
  for (let d = 0; d < decks; d += 1) {
    for (let v = 1; v <= 10; v += 1) {
      for (let n = v === 10 ? 16 : 4; n > 0; n -= 1) cards.push(v);
    }
  }
  for (let i = cards.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [cards[i], cards[j]] = [cards[j], cards[i]];
  }
  return { cards, decks, initial: decks * 52 };
}

export const cardsLeft = (shoe) => shoe.cards.length;
export const cardsDealt = (shoe) => shoe.initial - shoe.cards.length;
export const penetrationSoFar = (shoe) => cardsDealt(shoe) / shoe.initial;
export const needsShuffle = (shoe, penetration) => penetrationSoFar(shoe) >= penetration;

export function drawFrom(shoe) {
  return shoe.cards.pop();
}

// Take a named rank out of the shoe. Returns false when the shoe has none left,
// which is the caller's cue to shuffle.
//
// The copy removed is chosen at random, never the nearest one. Taking it off
// the top strips that rank out of exactly the cards about to be dealt: pulling
// the drill's two tens and a six that way dropped the player's bust rate on
// 16 against a ten from 62% to 39%, because the next card could no longer be
// one of the tens just removed.
export function pullRank(shoe, value, rng = Math.random) {
  const spots = [];
  for (let i = 0; i < shoe.cards.length; i += 1) if (shoe.cards[i] === value) spots.push(i);
  if (!spots.length) return false;
  shoe.cards.splice(spots[Math.floor(rng() * spots.length)], 1);
  return true;
}

// Draw a card that is not the given rank, for a hole card that cannot make
// blackjack. It has to be picked at random from every card that qualifies, not
// by skipping down from the top: skipping leaves the rejected cards sitting
// where they were, so they land on the very next draw instead. Doing that with
// aces against a ten upcard handed the player an ace 14.9% of the time against
// a true 7.8%.
export function pullExcluding(shoe, excluded, rng = Math.random) {
  const spots = [];
  for (let i = 0; i < shoe.cards.length; i += 1) if (shoe.cards[i] !== excluded) spots.push(i);
  if (!spots.length) return drawFrom(shoe);
  const at = spots[Math.floor(rng() * spots.length)];
  const card = shoe.cards[at];
  shoe.cards.splice(at, 1);
  return card;
}

export function canDeal(shoe, values) {
  const need = new Map();
  for (const v of values) need.set(v, (need.get(v) || 0) + 1);
  const have = new Map();
  for (const v of shoe.cards) have.set(v, (have.get(v) || 0) + 1);
  for (const [v, n] of need) if ((have.get(v) || 0) < n) return false;
  return true;
}
