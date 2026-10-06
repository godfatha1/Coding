// Cross-checks the printed charts against what the odds engine computes.
// For each chart row the engine is asked about every two-card hand that row
// covers, weighted by how often the shoe deals it, so a total-based chart cell
// is compared with a total-based expectation.
import { analyzeHand } from '../src/engine/odds.js';
import { normalizeRules } from '../src/engine/rules.js';
import { CHARTS, UPCARDS, codeToAction, lookupStrategy, chartFor } from '../src/engine/strategy.js';

let failures = 0;
const say = (ok, msg) => { if (!ok) failures += 1; console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); };
const upLabel = (u) => (u === 1 ? 'A' : String(u));

// 1. Every row has one entry per upcard and only known codes.
{
  const known = new Set(['H', 'S', 'D', 'Ds', 'P', 'Ph', 'Rh', 'Rs', 'Rp']);
  let bad = 0;
  for (const [name, chart] of Object.entries(CHARTS)) {
    for (const [section, rows] of Object.entries(chart)) {
      for (const [key, cells] of Object.entries(rows)) {
        if (cells.length !== 10) { console.log(`FAIL  ${name}.${section}.${key} has ${cells.length} cells`); bad += 1; }
        for (const c of cells) if (!known.has(c)) { console.log(`FAIL  ${name}.${section}.${key} has code ${c}`); bad += 1; }
      }
    }
  }
  say(bad === 0, 'chart rows are well formed');
}

// 2. Rule toggles resolve the conditional codes the way the book intends.
{
  const noSurrender = normalizeRules({ surrender: false });
  const noDas = normalizeRules({ das: false });
  say(codeToAction('Rh', noSurrender) === 'hit', 'without surrender, 16 vs 10 becomes a hit');
  say(codeToAction('Rp', noSurrender) === 'split', 'without surrender, 8,8 vs A stays a split');
  say(codeToAction('Ph', noDas) === 'hit', 'without DAS, 2,2 vs 3 becomes a hit');
  say(codeToAction('Ph', normalizeRules({ das: true })) === 'split', 'with DAS, 2,2 vs 3 is a split');
  say(lookupStrategy([5, 5], 6, normalizeRules({})).action === 'double', 'a pair of fives doubles against a 6');
  say(lookupStrategy([5, 5], 10, normalizeRules({})).action === 'hit', 'a pair of fives hits against a 10');
  say(lookupStrategy([1, 7], 9, normalizeRules({})).action === 'hit', 'soft 18 hits against a 9');
  say(lookupStrategy([1, 7], 2, normalizeRules({ hitSoft17: false })).action === 'stand', 'S17: soft 18 stands against a 2');
  say(lookupStrategy([1, 7], 2, normalizeRules({ hitSoft17: true })).action === 'double', 'H17: soft 18 doubles against a 2');
}

// 3. Chart cells against engine optimum, averaged over the hands each row covers.
const decks = 6;
const n = (v) => (v === 10 ? 16 : 4) * decks;

function handsFor(section, key) {
  if (section === 'pairs') return [[key, key]];
  if (section === 'soft') return [[1, key - 11]];
  const out = [];
  for (let a = 2; a <= 10; a += 1) {
    const b = key - a;
    if (b < a || b > 10) continue;
    if (a === b) continue;       // pairs are their own row
    out.push([a, b]);
  }
  return out;
}

for (const variant of ['s17', 'h17']) {
  const rules = normalizeRules({ decks, hitSoft17: variant === 'h17', das: true, surrender: true });
  const chart = chartFor(rules);
  const disagreements = [];
  let cells = 0;

  for (const section of ['hard', 'soft', 'pairs']) {
    for (const key of Object.keys(chart[section]).map(Number)) {
      if (section === 'hard' && (key < 5 || key > 19)) continue;  // 20 is a pair, 21 is a blackjack
      const hands = handsFor(section, key);
      if (!hands.length) continue;
      for (let col = 0; col < UPCARDS.length; col += 1) {
        const up = UPCARDS[col];
        const totals = new Map();
        let wsum = 0;
        for (const hand of hands) {
          const w = n(hand[0]) * (n(hand[1]) - (hand[0] === hand[1] ? 1 : 0)) * (hand[0] === hand[1] ? 1 : 2);
          wsum += w;
          const a = analyzeHand(hand, up, rules);
          for (const [name, entry] of Object.entries(a.actions)) {
            totals.set(name, (totals.get(name) || 0) + w * entry.ev);
          }
        }
        const ranked = [...totals.entries()].map(([k, v]) => [k, v / wsum]).sort((x, y) => y[1] - x[1]);
        const bookAction = codeToAction(chart[section][key][col], rules);
        cells += 1;
        if (ranked[0][0] !== bookAction) {
          const bookEv = ranked.find((r) => r[0] === bookAction)[1];
          disagreements.push({
            cell: `${section} ${key} vs ${upLabel(up)}`,
            book: bookAction, engine: ranked[0][0], gap: ranked[0][1] - bookEv,
          });
        }
      }
    }
  }

  const material = disagreements.filter((d) => d.gap > 0.004);
  console.log(`\n${variant.toUpperCase()}: ${cells} cells checked, ${disagreements.length} differ from the engine optimum`);
  for (const d of disagreements) {
    console.log(`      ${d.cell}: book ${d.book}, engine ${d.engine} (+${d.gap.toFixed(4)} EV)${d.gap > 0.004 ? '  <-- material' : ''}`);
  }
  say(material.length === 0, `${variant.toUpperCase()}: no cell where the engine beats the book by more than 0.004 EV`);
}

console.log(failures === 0 ? '\nAll strategy checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
