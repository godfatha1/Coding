import { normalizeRules, rulesSummary, RULE_PRESETS } from '../engine/rules.js';
import { handTotal, isPair, rankLabel } from '../engine/cards.js';
import { analyzeHand } from '../engine/odds.js';
import { lookupStrategy, chartFor, UPCARDS, codeToAction, rowLabel, rowRule } from '../engine/strategy.js';
import { dealScenario, parseScenario, scenarioLabel, HARD_TOTALS, SOFT_TOTALS, PAIR_RANKS } from '../engine/scenarios.js';
import { gradeCard, pickNext, retireCard, reviveCard, cardCounts, dueCount, isShaky } from '../engine/srs.js';
import { loadState, saveState, deckFor, ensureCard, clearProgress, exportJSON, importJSON } from './store.js';
import { pct1, pct0, signed, ACTION_KEY, ACTION_NAME, ACTION_IMPERATIVE, ACTION_GERUND, rowBar, rowNums, outcomeBlock, cardFace, cardBack } from './format.js';

const $ = (sel) => document.querySelector(sel);
const state = loadState();
let hand = null;          // { scenario, playerCards, upcard, ... }
let analysis = null;
let book = null;
let answered = null;      // { action, correct, lowConfidence }
let unsure = false;
let peeked = false;
let lastId = null;
let forcedId = null;
let view = 'drill';
let chartSelection = null;

const FILTERS = [
  { id: 'all', label: 'Everything' },
  { id: 'hard', label: 'Hard' },
  { id: 'soft', label: 'Soft' },
  { id: 'pairs', label: 'Pairs' },
  { id: 'shaky', label: 'Shaky' },
];

/* ---------------- theme ---------------- */

function applyTheme() {
  const t = state.settings.theme;
  if (t === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}

const upcardArticle = (up) => (up === 1 ? 'an ace' : `a ${up}`);

function buzz(ms) {
  if (!state.settings.haptics) return;
  try { navigator.vibrate?.(ms); } catch { /* not supported */ }
}

/* ---------------- deck + dealing ---------------- */

function filteredDeck() {
  const deck = deckFor(state);
  const f = state.settings.filter;
  if (f === 'all') return deck;
  if (f === 'shaky') {
    const shaky = deck.filter(isShaky);
    return shaky.length ? shaky : deck;
  }
  return deck.filter((c) => parseScenario(c.id).group === f);
}

function deal(id) {
  const deck = filteredDeck();
  let pick = null;
  if (id) pick = ensureCard(state, id);
  else pick = pickNext(deck, { step: state.step, lastId, sinceNew: state.sinceNew });
  if (!pick) { hand = null; renderDrill(); return; }
  state.sinceNew = pick.stage === 'new' ? 0 : state.sinceNew + 1;
  hand = dealScenario(pick.id);
  analysis = analyzeHand(hand.playerCards, hand.upcard, state.settings.rules);
  book = lookupStrategy(hand.playerCards, hand.upcard, state.settings.rules);
  answered = null;
  unsure = false;
  peeked = false;
  renderDrill();
}

function legalActions() {
  const out = ['hit', 'stand'];
  if (hand.playerCards.length === 2) out.push('double');
  if (isPair(hand.playerCards)) out.push('split');
  if (state.settings.rules.surrender) out.push('surrender');
  return out;
}

/* ---------------- answering ---------------- */

function answer(action, lowConfidence) {
  if (!hand || answered) return;
  const low = Boolean(lowConfidence || unsure || peeked);
  const correct = action === book.action;
  answered = { action, correct, lowConfidence: low };

  const card = ensureCard(state, hand.scenario.id);
  state.cards[card.id] = gradeCard(card, { correct, lowConfidence: low, step: state.step, settings: { retireStreak: state.settings.retireStreak } });
  state.step += 1;
  lastId = card.id;

  state.lifetime.answered += 1;
  if (correct) state.lifetime.correct += 1;
  if (low) state.lifetime.shaky += 1;
  state.session.answered += 1;
  if (correct) {
    state.session.correct += 1;
    state.session.streak += 1;
    state.lifetime.bestStreak = Math.max(state.lifetime.bestStreak, state.session.streak);
  } else {
    state.session.streak = 0;
  }
  state.session.recent = [...state.session.recent, correct ? (low ? 'soft' : 'hit') : 'miss'].slice(-10);

  saveState(state);
  buzz(correct ? 14 : [26, 50, 26]);
  renderDrill({ felt: false });
}

function retireCurrent() {
  if (!hand) return;
  const card = ensureCard(state, hand.scenario.id);
  state.cards[card.id] = retireCard(card, state.step);
  saveState(state);
  deal();
}

/* ---------------- drill view ---------------- */

function renderStrip() {
  const deck = deckFor(state);
  const counts = cardCounts(deck);
  const due = dueCount(deck, state.step);
  const acc = state.session.answered ? pct0(state.session.correct / state.session.answered) : '--';
  $('#strip').innerHTML = `
    <span><b class="num">${state.session.answered}</b> hands · <b class="num">${acc}</b> right</span>
    <span class="dots" aria-label="How the last ten answers went">${state.session.recent.slice(-10).map((r) => `<i class="${r}"></i>`).join('')}</span>
    <span class="num" title="Chart cells mastered${due ? `, ${due} due for review` : ''}">${counts.retired}/${counts.total}${due ? ` \u00b7 ${due} due` : ''}</span>`;
}

function renderFelt() {
  if (!hand) { $('#felt').innerHTML = '<p class="empty">Nothing left in this filter. Every hand here is mastered.</p>'; return; }
  const { total, soft } = handTotal(hand.playerCards);
  const pair = isPair(hand.playerCards);
  const handName = pair
    ? `Pair of ${rankLabel(hand.playerCards[0])}${hand.playerCards[0] === 10 ? 's' : "'s"}`
    : `${soft ? 'Soft' : 'Hard'} ${total}`;
  $('#felt').innerHTML = `
    <div class="hand-row">
      <div class="hand-label"><span class="eyebrow">Dealer shows</span><span class="hand-total num">${rankLabel(hand.upcard)}</span></div>
      <div class="cards">${cardFace(hand.dealerDisplay)}${cardBack}</div>
    </div>
    <div class="hand-row">
      <div class="hand-label"><span class="eyebrow">Your hand</span><span class="hand-total">${handName}</span></div>
      <div class="cards">${hand.playerDisplay.map(cardFace).join('')}</div>
    </div>`;
}

function oddsRows(highlight) {
  return `<div class="rows">${analysis.ranked.map((entry) => {
    const isBook = entry.action === book.action;
    const isYours = highlight && highlight === entry.action && !isBook;
    return `<div class="orow${isBook ? ' is-book' : ''} a-${entry.action}">
      <div class="orow-top">
        <span class="key">${ACTION_KEY[entry.action]}</span>
        <span>${ACTION_NAME[entry.action]}</span>
        ${isBook ? '<span class="tag book">Book</span>' : ''}
        ${isYours ? '<span class="tag yours">Yours</span>' : ''}
        <span class="orow-ev">${signed(entry.ev)}</span>
      </div>
      ${rowBar(entry)}
      <div class="orow-nums">${rowNums(entry)}</div>
    </div>`;
  }).join('')}</div>`;
}

function dealerLine() {
  const d = analysis.dealer;
  const parts = [`busts ${pct1(d.bust)}`];
  for (const t of [17, 18, 19, 20, 21]) parts.push(`${t}: ${pct1(d[t])}`);
  return `<p class="dealer-line">Dealer on ${rankLabel(hand.upcard)} — ${parts.join(' · ')}</p>`;
}

function whyLine() {
  const bookEntry = analysis.actions[book.action];
  const rival = analysis.ranked.find((r) => r.action !== book.action);
  if (!bookEntry || !rival) return '';
  const gap = bookEntry.ev - rival.ev;
  const lead = `${ACTION_IMPERATIVE[book.action]} returns <b>${signed(bookEntry.ev)}</b> per unit staked`;
  if (gap >= 0) {
    return `<p class="why">${lead}, ${signed(gap).replace('+', '')} better than ${ACTION_GERUND[rival.action]} at ${signed(rival.ev)}.</p>`;
  }
  return `<p class="why">${lead}. On this exact pair of cards ${ACTION_GERUND[rival.action]} edges ahead by ${Math.abs(gap).toFixed(3)} — a quirk of these particular cards that the chart rounds away. Play the book.</p>`;
}

function renderPending() {
  const legal = legalActions();
  const order = ['hit', 'stand', 'double', 'split', 'surrender'];
  const buttons = order.filter((a) => legal.includes(a)).map((a, i, arr) => {
    const wide = arr.length % 2 === 1 && i === arr.length - 1;
    return `<button class="action a-${a}${wide ? ' wide' : ''}" type="button" data-action="${a}">
      <span class="key">${ACTION_KEY[a]}</span>${ACTION_NAME[a]}</button>`;
  }).join('');

  $('#answer').innerHTML = `
    <div class="panel" style="display:grid;gap:11px">
      <button class="unsure" id="unsureBtn" type="button" aria-pressed="${unsure}">
        <span class="box" aria-hidden="true">${unsure ? '✓' : ''}</span>
        <span>Not sure about this one</span>
        <span class="hint">or hold a button</span>
      </button>
      <div class="actions">${buttons}</div>
      ${state.settings.oddsUpFront || peeked
        ? `<div class="odds-hero">${oddsRows(null)}</div>`
        : '<button class="btn" id="peekBtn" type="button">Peek at the odds (counts as unsure)</button>'}
    </div>`;
}

function renderAnswered() {
  const bookEntry = analysis.actions[book.action];
  const yours = analysis.actions[answered.action];
  const shown = bookEntry || yours;
  const card = state.cards[hand.scenario.id];
  const justRetired = card && card.stage === 'retired';
  const toGo = Math.max(0, state.settings.retireStreak - (card?.hcStreak || 0));

  $('#answer').innerHTML = `
    <div class="panel" style="display:grid;gap:12px">
      <div class="verdict ${answered.correct ? 'good' : 'bad'}">
        <span class="mark" aria-hidden="true">${answered.correct ? '✓' : '✕'}</span>
        <div class="verdict-text">
          <div class="verdict-head"><b>${rowLabel(book.section, book.key)}</b><span class="pipe">|</span>${rowRule(book.section, book.key, state.settings.rules)}</div>
          ${answered.correct ? '' : `<div class="verdict-sub">You said ${ACTION_NAME[answered.action].toLowerCase()}. Against ${upcardArticle(hand.upcard)} it is ${ACTION_NAME[book.action].toLowerCase()}.</div>`}
        </div>
      </div>

      <div class="btn-row">
        ${justRetired
          ? '<span class="note" style="flex:1 1 auto;align-self:center">Mastered. You will not see this hand again.</span>'
          : `<button class="btn" id="retireBtn" type="button">Too easy — retire</button>`}
        <button class="btn btn-primary" id="nextBtn" type="button">Next hand</button>
      </div>

      <div class="odds-hero">
        <div class="section-head" style="margin:0">
          <h2 class="section-title">If you ${ACTION_NAME[book.action].toLowerCase()}</h2>
          <span class="section-note">per unit staked ${signed(shown.ev)}</span>
        </div>
        ${outcomeBlock(shown)}
        ${whyLine()}
      </div>

      <details class="compare"${answered.correct ? '' : ' open'}>
        <summary>Every action, ranked</summary>
        ${oddsRows(answered.action)}
        ${dealerLine()}
      </details>

      ${justRetired || answered.lowConfidence || !answered.correct ? '' : `<p class="note">${toGo} more confident ${toGo === 1 ? 'answer' : 'answers'} and this hand retires itself.</p>`}
      ${answered.lowConfidence ? '<p class="note">Marked shaky — this one comes back soon.</p>' : ''}
    </div>`;
}

function renderDrill({ felt = true } = {}) {
  renderStrip();
  if (felt) renderFelt();
  if (!hand) { $('#answer').innerHTML = ''; return; }
  if (answered) renderAnswered(); else renderPending();
}

/* ---------------- chart view ---------------- */

function chartRows() {
  const rows = [];
  rows.push({ section: 'Hard totals' });
  for (const t of HARD_TOTALS) rows.push({ kind: 'h', key: t, label: String(t) });
  rows.push({ section: 'Soft hands' });
  for (const t of SOFT_TOTALS) rows.push({ kind: 's', key: t, label: `A,${t - 11}` });
  rows.push({ section: 'Pairs' });
  for (const r of PAIR_RANKS) rows.push({ kind: 'p', key: r, label: `${rankLabel(r)},${rankLabel(r)}` });
  return rows;
}

function renderChart() {
  const rules = state.settings.rules;
  const chart = chartFor(rules);
  const header = `<div class="rh"></div>` + UPCARDS.map((u) => `<div class="hd">${rankLabel(u)}</div>`).join('');
  const sectionKey = { h: 'hard', s: 'soft', p: 'pairs' };

  const body = chartRows().map((row) => {
    if (row.section) return `<div class="sec">${row.section}</div>${header}`;
    const cells = UPCARDS.map((up, col) => {
      const code = chart[sectionKey[row.kind]][row.key][col];
      const action = codeToAction(code, rules);
      const id = `${row.kind}${row.key}-${up}`;
      const card = state.cards[id];
      const mastered = state.settings.chartProgress && card?.stage === 'retired';
      const shaky = state.settings.chartProgress && card?.stage === 'learning';
      return `<button class="cell a-${action}" type="button" data-cell="${id}"
        data-mastered="${mastered}" data-shaky="${Boolean(shaky)}" data-dim="${mastered}"
        data-sel="${chartSelection === id}"
        aria-label="${scenarioLabel(id)}: ${ACTION_NAME[action]}">${code}</button>`;
    }).join('');
    return `<div class="rh" title="${row.kind === 's' ? `soft ${row.key}` : row.kind === 'p' ? 'pair' : `hard ${row.key}`}">${row.label}</div>${cells}`;
  }).join('');

  const counts = cardCounts(deckFor(state));
  $('#chartHost').innerHTML = `
    <div class="panel">
      <div class="section-head">
        <h2 class="section-title">Basic strategy</h2>
        <span class="section-note">${rulesSummary(rules)}</span>
      </div>
      <p class="note" style="margin:0 0 10px">Tap any cell for the odds behind it. ${state.settings.chartProgress ? `Faded cells are mastered (${counts.retired}); dashed outlines are hands still in rotation after a miss or a flag.` : ''}</p>
      <div class="chart-scroll"><div class="grid">${body}</div></div>
      <div class="legend" style="margin-top:12px">
        ${[['hit', 'H', 'Hit'], ['stand', 'S', 'Stand'], ['double', 'D', 'Double'], ['split', 'P', 'Split'], ['surrender', 'R', 'Surrender']]
          .map(([a, c, n]) => `<span class="a-${a}"><i></i>${c} &nbsp;${n}</span>`).join('')}
      </div>
      <p class="note" style="margin-top:10px">Two-card hands only, which is why hard 20 lives in the pairs section as 10,10. Lower-case suffixes carry the fallback: <b>Ds</b> double else stand, <b>Ph</b> split only with double-after-split, <b>Rh</b> surrender else hit, <b>Rs</b> surrender else stand, <b>Rp</b> surrender else split.</p>
      <div class="field" style="border:0;padding-bottom:0">
        <div class="field-head">
          <span class="name">Show my progress on the chart</span>
          <button class="switch" id="chartProgressSwitch" role="switch" aria-checked="${state.settings.chartProgress}" aria-label="Show progress on the chart"></button>
        </div>
      </div>
    </div>`;
}

function openCellSheet(id) {
  const scenario = parseScenario(id);
  const dealt = dealScenario(id, () => 0.5);
  const a = analyzeHand(dealt.playerCards, scenario.upcard, state.settings.rules);
  const s = lookupStrategy(dealt.playerCards, scenario.upcard, state.settings.rules);
  const card = state.cards[id];
  const stageLabel = { new: 'Not drilled yet', learning: 'In rotation', review: 'Reviewing', retired: 'Mastered' };
  const bookEntry = a.actions[s.action];

  openSheet(scenarioLabel(id), `
    <div class="verdict good" style="background:var(--surface-3)">
      <span class="mark" aria-hidden="true" style="background:var(--accent);color:var(--accent-ink)">${ACTION_KEY[s.action]}</span>
      <div class="verdict-text">
        <div class="verdict-head">${ACTION_IMPERATIVE[s.action]}</div>
        <div class="verdict-sub"><b>${rowLabel(s.section, s.key)}</b><span class="pipe">|</span>${rowRule(s.section, s.key, state.settings.rules)}</div>
      </div>
    </div>
    ${outcomeBlock(bookEntry)}
    <p class="note">${stageLabel[card?.stage || 'new']}${card?.reps ? ` · seen ${card.reps}x · missed ${card.wrong}x · flagged shaky ${card.shaky}x` : ''}</p>
    <div class="rows">${a.ranked.map((e) => `<div class="orow${e.action === s.action ? ' is-book' : ''} a-${e.action}">
      <div class="orow-top"><span class="key">${ACTION_KEY[e.action]}</span><span>${ACTION_NAME[e.action]}</span><span class="orow-ev">${signed(e.ev)}</span></div>
      ${rowBar(e)}
      <div class="orow-nums">${rowNums(e)}</div></div>`).join('')}</div>
    <div class="btn-row">
      <button class="btn" data-sheet-action="toggle" data-id="${id}">${card?.stage === 'retired' ? 'Bring back' : 'Retire'}</button>
      <button class="btn btn-primary" data-sheet-action="drill" data-id="${id}">Drill this hand</button>
    </div>`);
}

/* ---------------- progress view ---------------- */

function renderStats() {
  const deck = deckFor(state);
  const counts = cardCounts(deck);
  const lifeAcc = state.lifetime.answered ? state.lifetime.correct / state.lifetime.answered : null;

  const groups = [
    { id: 'hard', label: 'Hard totals' },
    { id: 'soft', label: 'Soft hands' },
    { id: 'pairs', label: 'Pairs' },
  ].map((g) => {
    const cards = deck.filter((c) => parseScenario(c.id).group === g.id);
    const reps = cards.reduce((a, c) => a + c.reps, 0);
    const wrong = cards.reduce((a, c) => a + c.wrong, 0);
    const retired = cards.filter((c) => c.stage === 'retired').length;
    return { ...g, reps, wrong, retired, total: cards.length, acc: reps ? (reps - wrong) / reps : null };
  });

  const stageBars = [
    { label: 'Mastered', n: counts.retired },
    { label: 'Reviewing', n: counts.review },
    { label: 'In rotation', n: counts.learning },
    { label: 'Untouched', n: counts.new },
  ];

  const trouble = deck
    .filter((c) => c.stage !== 'retired' && (c.wrong > 0 || c.shaky > 0))
    .sort((a, b) => (b.wrong * 2 + b.shaky) - (a.wrong * 2 + a.shaky))
    .slice(0, 8);

  $('#statsHost').innerHTML = `
    <div class="tiles">
      <div class="tile"><span class="cap">Mastered</span><span class="val num">${counts.retired}</span><span class="sub">of ${counts.total} chart cells</span></div>
      <div class="tile"><span class="cap">Lifetime accuracy</span><span class="val num">${lifeAcc === null ? '--' : pct0(lifeAcc)}</span><span class="sub">${state.lifetime.answered} hands played</span></div>
      <div class="tile"><span class="cap">Shaky hands</span><span class="val num">${counts.shaky}</span><span class="sub">missed or flagged</span></div>
      <div class="tile"><span class="cap">Best streak</span><span class="val num">${state.lifetime.bestStreak}</span><span class="sub">${state.session.streak} right now</span></div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Where the deck stands</h2><span class="section-note">${counts.total} cells</span></div>
      <div class="bars">${stageBars.map((b) => `<div class="bar-row">
        <span class="lbl">${b.label}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(b.n / counts.total * 100).toFixed(1)}%"></span></span>
        <span class="amt">${b.n}</span></div>`).join('')}</div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Accuracy by family</h2></div>
      <div class="bars">${groups.map((g) => `<div class="bar-row">
        <span class="lbl">${g.label}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${g.acc === null ? 0 : (g.acc * 100).toFixed(1)}%"></span></span>
        <span class="amt">${g.acc === null ? '--' : pct0(g.acc)}</span></div>`).join('')}</div>
      <p class="note" style="margin-top:10px">${groups.map((g) => `${g.label}: ${g.retired}/${g.total} mastered`).join(' · ')}</p>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Hands giving you trouble</h2>
        ${trouble.length ? '<span class="section-note"><button class="chip" id="drillShaky" type="button">Drill these</button></span>' : ''}</div>
      ${trouble.length ? `<div class="list">${trouble.map((c) => `<div class="list-row">
        <span>${scenarioLabel(c.id)}</span>
        <span class="meta">${c.wrong} missed · ${c.shaky} shaky</span>
        <button data-stats-drill="${c.id}" type="button">Drill</button></div>`).join('')}</div>`
        : '<p class="empty">Nothing flagged. Miss a hand or mark one shaky and it shows up here.</p>'}
    </div>`;
}

/* ---------------- settings view ---------------- */

function renderSettings() {
  const s = state.settings;
  const r = s.rules;
  const counts = cardCounts(deckFor(state));
  const opt = (id, label, on) => `<button class="chip" type="button" data-opt="${id}" aria-pressed="${on}">${label}</button>`;

  $('#settingsHost').innerHTML = `
    <div class="panel">
      <div class="section-head"><h2 class="section-title">Table rules</h2><span class="section-note">${rulesSummary(r)}</span></div>
      <div class="field">
        <div class="field-head"><span class="name">Decks</span></div>
        <div class="chips">${[4, 6, 8].map((d) => opt(`decks:${d}`, `${d} decks`, r.decks === d)).join('')}</div>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Dealer on soft 17</span></div>
        <div class="chips">${opt('h17:0', 'Stands (S17)', !r.hitSoft17)}${opt('h17:1', 'Hits (H17)', r.hitSoft17)}</div>
        <p class="field-desc">H17 moves five cells: 11 vs A, 15 vs A, 17 vs A, soft 18 vs 2 and soft 19 vs 6.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Double after split</span>
          <button class="switch" role="switch" data-opt="das" aria-checked="${r.das}" aria-label="Double after split"></button></div>
        <p class="field-desc">Off turns 2,2 and 3,3 against a 2 or 3, 4,4 against 5-6, and 6,6 against a 2 into hits.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Late surrender</span>
          <button class="switch" role="switch" data-opt="surrender" aria-checked="${r.surrender}" aria-label="Late surrender"></button></div>
        <p class="field-desc">Off removes the R cells from the chart and from the buttons.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Presets</span></div>
        <div class="chips">${RULE_PRESETS.map((p) => opt(`preset:${p.id}`, p.label, false)).join('')}</div>
      </div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Drill</h2></div>
      <div class="field">
        <div class="field-head"><span class="name">Hands to drill</span></div>
        <div class="chips">${FILTERS.map((f) => opt(`filter:${f.id}`, f.label, s.filter === f.id)).join('')}</div>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Retire after</span></div>
        <div class="chips">${[2, 3, 4, 5].map((n) => opt(`retire:${n}`, `${n} in a row`, s.retireStreak === n)).join('')}</div>
        <p class="field-desc">Confident correct answers in a row before a hand is retired for good. A shaky or wrong answer resets the count.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Show odds before I answer</span>
          <button class="switch" role="switch" data-opt="oddsUpFront" aria-checked="${s.oddsUpFront}" aria-label="Show odds before answering"></button></div>
        <p class="field-desc">Study mode. Every hand you answer this way is logged as shaky.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Vibrate on answer</span>
          <button class="switch" role="switch" data-opt="haptics" aria-checked="${s.haptics}" aria-label="Vibrate on answer"></button></div>
      </div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Appearance</h2></div>
      <div class="chips">${['system', 'light', 'dark'].map((t) => opt(`theme:${t}`, t[0].toUpperCase() + t.slice(1), s.theme === t)).join('')}</div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Your progress</h2><span class="section-note">${counts.retired} mastered · ${counts.total - counts.new - counts.retired} in rotation</span></div>
      <div class="field">
        <div class="field-head"><span class="name">Backup</span></div>
        <p class="field-desc">Progress lives in this browser only. Copy this out before clearing site data or switching phones.</p>
        <textarea class="io" id="ioBox" spellcheck="false" aria-label="Backup data"></textarea>
        <div class="btn-row">
          <button class="btn" id="copyBtn" type="button">Copy backup</button>
          <button class="btn" id="importBtn" type="button">Restore from box</button>
        </div>
        <p class="note" id="ioNote"></p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Start over</span></div>
        <p class="field-desc">Clears every mastered hand, every count and every streak.</p>
        <div id="resetZone"><button class="btn danger" id="resetBtn" type="button">Reset all progress</button></div>
      </div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">How the numbers are worked out</h2></div>
      <p class="field-desc">Win, push and lose are exact for the hand on the table: the cards you can see are removed from a ${r.decks}-deck shoe, the dealer's distribution is solved after the peek for blackjack, and the player's draws are solved by recursion over every finishing hand. Splits are valued as two independent hands without resplits, so a splittable pair is worth a hair more in a real game than the figure shown. Expected value is per unit of your original bet, which is why a double can read beyond +1 or below -1.</p>
      <p class="field-desc">The graded answer always comes from the printed 4-8 deck chart, not from this engine, so what you drill is what the book says.</p>
    </div>`;

  $('#ioBox').value = exportJSON(state);
}

/* ---------------- sheet ---------------- */

function openSheet(title, body) {
  $('#sheetRoot').innerHTML = `
    <div class="sheet-wrap" role="dialog" aria-modal="true" aria-label="${title}">
      <button class="sheet-scrim" id="sheetScrim" aria-label="Close"></button>
      <div class="sheet">
        <div class="sheet-head"><h2>${title}</h2>
          <button class="sheet-close" id="sheetClose" type="button" aria-label="Close">✕</button></div>
        ${body}
      </div>
    </div>`;
}
const closeSheet = () => { $('#sheetRoot').innerHTML = ''; };

/* ---------------- routing ---------------- */

function setView(next) {
  view = next;
  for (const section of document.querySelectorAll('.pane-inner')) {
    section.hidden = section.id !== `view-${next}`;
  }
  for (const tab of document.querySelectorAll('.tab')) {
    tab.setAttribute('aria-selected', String(tab.dataset.view === next));
  }
  $('#pane').scrollTop = 0;
  if (next === 'chart') renderChart();
  if (next === 'stats') renderStats();
  if (next === 'settings') renderSettings();
  if (next === 'drill') renderDrill();
}

function refreshRulesChip() {
  const r = state.settings.rules;
  $('#rulesChip').textContent = `${r.decks}D · ${r.hitSoft17 ? 'H17' : 'S17'}${r.surrender ? ' · LS' : ''}`;
  $('#rulesChip').title = rulesSummary(r);
}

function rulesChanged() {
  saveState(state);
  refreshRulesChip();
  if (hand) {
    analysis = analyzeHand(hand.playerCards, hand.upcard, state.settings.rules);
    book = lookupStrategy(hand.playerCards, hand.upcard, state.settings.rules);
    if (answered) answered.correct = answered.action === book.action;
  }
  if (view === 'drill') renderDrill({ felt: false });
  if (view === 'chart') renderChart();
  if (view === 'settings') renderSettings();
}

/* ---------------- events ---------------- */

$('#tabbar').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (tab) setView(tab.dataset.view);
});

$('#themeBtn').addEventListener('click', () => {
  const order = ['system', 'light', 'dark'];
  state.settings.theme = order[(order.indexOf(state.settings.theme) + 1) % order.length];
  applyTheme();
  saveState(state);
  if (view === 'settings') renderSettings();
});

$('#rulesChip').addEventListener('click', () => setView('settings'));

// Drill: a tap answers, a long hold answers with low confidence.
let holdTimer = null;
let holdFired = false;

$('#answer').addEventListener('pointerdown', (e) => {
  const btn = e.target.closest('.action');
  if (!btn || btn.disabled) return;
  holdFired = false;
  holdTimer = setTimeout(() => {
    holdFired = true;
    buzz(18);
    unsure = true;
    answer(btn.dataset.action, true);
  }, 420);
});
const clearHold = () => { if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } };
$('#answer').addEventListener('pointerup', clearHold);
$('#answer').addEventListener('pointercancel', clearHold);
$('#answer').addEventListener('pointerleave', clearHold);

$('#answer').addEventListener('click', (e) => {
  clearHold();
  if (holdFired) { holdFired = false; return; }
  const act = e.target.closest('.action');
  if (act) { answer(act.dataset.action, false); return; }
  if (e.target.closest('#unsureBtn')) { unsure = !unsure; renderPending(); return; }
  if (e.target.closest('#peekBtn')) { peeked = true; renderPending(); return; }
  if (e.target.closest('#retireBtn')) { retireCurrent(); return; }
  if (e.target.closest('#nextBtn')) { deal(forcedId); forcedId = null; }
});

$('#chartHost').addEventListener('click', (e) => {
  const cell = e.target.closest('.cell');
  if (cell) { chartSelection = cell.dataset.cell; renderChart(); openCellSheet(cell.dataset.cell); return; }
  if (e.target.closest('#chartProgressSwitch')) {
    state.settings.chartProgress = !state.settings.chartProgress;
    saveState(state);
    renderChart();
  }
});

$('#sheetRoot').addEventListener('click', (e) => {
  if (e.target.closest('#sheetScrim') || e.target.closest('#sheetClose')) { closeSheet(); return; }
  const btn = e.target.closest('[data-sheet-action]');
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.sheetAction === 'toggle') {
    const card = ensureCard(state, id);
    state.cards[id] = card.stage === 'retired' ? reviveCard(card, state.step) : retireCard(card, state.step);
    saveState(state);
    closeSheet();
    renderChart();
  } else {
    closeSheet();
    setView('drill');
    deal(id);
  }
});

$('#statsHost').addEventListener('click', (e) => {
  const one = e.target.closest('[data-stats-drill]');
  if (one) { setView('drill'); deal(one.dataset.statsDrill); return; }
  if (e.target.closest('#drillShaky')) {
    state.settings.filter = 'shaky';
    saveState(state);
    setView('drill');
    deal();
  }
});

$('#settingsHost').addEventListener('click', (e) => {
  const opt = e.target.closest('[data-opt]');
  if (opt) {
    const [kind, value] = opt.dataset.opt.split(':');
    const r = state.settings.rules;
    if (kind === 'decks') r.decks = Number(value);
    else if (kind === 'h17') r.hitSoft17 = value === '1';
    else if (kind === 'das') r.das = !r.das;
    else if (kind === 'surrender') r.surrender = !r.surrender;
    else if (kind === 'preset') {
      const preset = RULE_PRESETS.find((p) => p.id === value);
      if (preset) state.settings.rules = normalizeRules({ ...r, ...preset.rules });
    } else if (kind === 'filter') { state.settings.filter = value; deal(); }
    else if (kind === 'retire') state.settings.retireStreak = Number(value);
    else if (kind === 'theme') { state.settings.theme = value; applyTheme(); }
    else if (kind === 'oddsUpFront') state.settings.oddsUpFront = !state.settings.oddsUpFront;
    else if (kind === 'haptics') state.settings.haptics = !state.settings.haptics;
    state.settings.rules = normalizeRules(state.settings.rules);
    rulesChanged();
    renderSettings();
    return;
  }
  if (e.target.closest('#copyBtn')) {
    const text = $('#ioBox').value;
    navigator.clipboard?.writeText(text).then(
      () => { $('#ioNote').textContent = 'Backup copied to the clipboard.'; },
      () => { $('#ioBox').select(); $('#ioNote').textContent = 'Copy blocked here — the text is selected, copy it by hand.'; },
    );
    return;
  }
  if (e.target.closest('#importBtn')) {
    try {
      const next = importJSON($('#ioBox').value);
      Object.assign(state, next, { session: { answered: 0, correct: 0, streak: 0, recent: [] } });
      saveState(state);
      applyTheme();
      refreshRulesChip();
      renderSettings();
      $('#ioNote').textContent = 'Progress restored.';
      deal();
    } catch (err) {
      $('#ioNote').textContent = `Could not read that: ${err.message}`;
    }
    return;
  }
  if (e.target.closest('#resetBtn')) {
    $('#resetZone').innerHTML = '<p class="field-desc">This cannot be undone. Copy your backup first if you want it.</p>'
      + '<div class="btn-row"><button class="btn" id="resetCancel" type="button">Keep my progress</button>'
      + '<button class="btn danger" id="resetConfirm" type="button">Yes, erase it all</button></div>';
    return;
  }
  if (e.target.closest('#resetCancel')) { renderSettings(); return; }
  if (e.target.closest('#resetConfirm')) {
    clearProgress(state);
    renderSettings();
    deal();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.matches('textarea, input')) return;
  if ($('#sheetRoot').firstElementChild && e.key === 'Escape') { closeSheet(); return; }
  if (view !== 'drill') return;
  const k = e.key.toLowerCase();
  if (answered) {
    if (k === 'enter' || k === ' ' || k === 'n') { e.preventDefault(); deal(); }
    return;
  }
  const map = { h: 'hit', s: 'stand', d: 'double', p: 'split', r: 'surrender' };
  if (k === 'u') { unsure = !unsure; renderPending(); return; }
  const action = map[k];
  if (action && legalActions().includes(action)) {
    e.preventDefault();
    answer(action, e.shiftKey);
  }
});

/* ---------------- boot ---------------- */

applyTheme();
refreshRulesChip();
setView('drill');
deal();
