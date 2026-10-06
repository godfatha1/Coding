export const pct1 = (x) => `${(x * 100).toFixed(1)}%`;
export const pct0 = (x) => `${Math.round(x * 100)}%`;

export function signed(x, digits = 3) {
  const v = Number(x);
  const sign = v > 0.0005 ? '+' : v < -0.0005 ? '−' : '';
  return `${sign}${Math.abs(v).toFixed(digits)}`;
}

export const ACTION_KEY = { hit: 'H', stand: 'S', double: 'D', split: 'P', surrender: 'R' };
export const ACTION_NAME = { hit: 'Hit', stand: 'Stand', double: 'Double', split: 'Split', surrender: 'Surrender' };
export const ACTION_IMPERATIVE = { hit: 'Hit', stand: 'Stand', double: 'Double down', split: 'Split', surrender: 'Surrender' };
export const ACTION_GERUND = { hit: 'hitting', stand: 'standing', double: 'doubling', split: 'splitting', surrender: 'surrendering' };

// Win / push / lose as a stacked bar. Segments sit 2px apart on the surface so
// a thin push slice still reads as its own band.
export function wplBar(entry, cls = '') {
  const seg = (k, v) => (v > 0.0005 ? `<span class="${k}" style="flex:${v.toFixed(5)}"></span>` : '');
  return `<div class="wpl ${cls}" role="img" aria-label="Wins ${pct1(entry.win)}, pushes ${pct1(entry.push)}, loses ${pct1(entry.lose)}">`
    + seg('w', entry.win) + seg('p', entry.push) + seg('l', entry.lose) + '</div>';
}

// Surrender has no draw and no dealer, so a win/push/lose split misreads as a
// total loss. It gets its own neutral band and wording everywhere it appears.
export const isFlatLoss = (entry) => entry.action === 'surrender';

export function rowBar(entry) {
  if (isFlatLoss(entry)) return '<div class="wpl mini"><span class="p" style="flex:1"></span></div>';
  return wplBar(entry, 'mini');
}

export function rowNums(entry) {
  if (isFlatLoss(entry)) return 'half your bet back, every time';
  return `${pct1(entry.win)} win \u00b7 ${pct1(entry.push)} push \u00b7 ${pct1(entry.lose)} lose`;
}

export function outcomeBlock(entry) {
  if (isFlatLoss(entry)) {
    return '<p class="why" style="margin:0">Half your bet comes back and the hand ends \u2014 no draw, no dealer, no swing.</p>'
      + '<div class="wpl"><span class="p" style="flex:1"></span></div>';
  }
  return `<div class="odds-figures">
      <div class="figure win"><span class="cap"><i></i>Win</span><span class="val num">${pct1(entry.win)}</span></div>
      <div class="figure push"><span class="cap"><i></i>Push</span><span class="val num">${pct1(entry.push)}</span></div>
      <div class="figure lose"><span class="cap"><i></i>Lose</span><span class="val num">${pct1(entry.lose)}</span></div>
    </div>${wplBar(entry)}`;
}

export function cardFace(card) {
  return `<div class="card${card.red ? ' red' : ''}" aria-label="${card.rank} ${card.suit}">`
    + `<span class="r">${card.rank}</span><span class="s">${card.suit}</span>`
    + `<span class="big" aria-hidden="true">${card.suit}</span></div>`;
}

export const cardBack = '<div class="card back" aria-label="Dealer hole card, face down"></div>';
