import { normalizeRules, rulesSummary, RULE_PRESETS } from '../engine/rules.js';
import { handTotal, isPair, rankLabel, makeDisplayCard } from '../engine/cards.js';
import { analyzeHand } from '../engine/odds.js';
import { lookupStrategy, chartFor, UPCARDS, codeToAction, rowLabel, rowRule } from '../engine/strategy.js';
import { dealScenario, parseScenario, scenarioLabel, ruleGroups, HARD_TOTALS, SOFT_TOTALS, PAIR_RANKS } from '../engine/scenarios.js';
import { gradeCard, pickNext, retireCard, reviveCard, cardCounts, dueCount, isShaky } from '../engine/srs.js';
import { startRound, act, legalMoves, handDone, exposedCards } from '../engine/game.js';
import { newShoe, cardsLeft, needsShuffle, canDeal } from '../engine/shoe.js';
import { COUNT_SYSTEMS, getSystem, readySystems, countValue, startingCount, trueCount, decksRemaining, formatCount, formatTrue } from '../engine/counting.js';
import { loadState, saveState, deckFor, ruleDeckFor, ensureCard, ensureRuleCard, clearProgress, exportJSON, importJSON, freshBank, freshCount, recordRound } from './store.js';
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
let quiz = null;          // the reverse question: a rule, four groups, one right
let round = null;         // the hand being played out for money, in table mode
let countCheck = null;    // the running-count question between hands
let freshShoe = false;    // a shuffle happened before this hand

const MODES = [
  { id: 'play', label: 'Name the play' },
  { id: 'hands', label: 'Name the hands' },
  { id: 'mix', label: 'Mix' },
];

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

const money = (n) => `${n < 0 ? '\u2212' : ''}$${Math.abs(Math.round(n * 100) / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
const moneySigned = (n) => `${n > 0 ? '+' : n < 0 ? '\u2212' : ''}$${Math.abs(Math.round(n * 100) / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}`;

// Drawn cards keep the suit they were first shown with, so a redraw of the
// screen does not reshuffle the table.
const countingOn = () => state.settings.table && state.settings.counting.on;
const countSystem = () => getSystem(state.settings.counting.system);

// Reshuffle when the shoe is past its penetration, when the deck count changes,
// or when it cannot supply the hand the drill wants. Returns true on a shuffle.
function ensureShoe(need) {
  const { decks } = state.settings.rules;
  const c = state.settings.counting;
  const stale = !state.shoe || state.shoe.decks !== decks
    || needsShuffle(state.shoe, c.penetration) || !canDeal(state.shoe, need);
  if (!stale) return false;
  state.shoe = newShoe(decks);
  const base = startingCount(countSystem(), decks);
  Object.assign(state.count, { running: base, beforeRound: base, seen: 0, seenBefore: 0, shoes: state.count.shoes + 1 });
  return true;
}

// The running count is rebuilt from the round each time rather than nudged, so
// turning the hole card or splitting a hand cannot double-count anything.
function syncCount() {
  if (!countingOn() || !round) return;
  const system = countSystem();
  const seen = exposedCards(round);
  let tally = 0;
  for (const card of seen) tally += countValue(system, card);
  state.count.running = state.count.beforeRound + tally;
  state.count.seen = state.count.seenBefore + seen.length;
}

function syncDisplay() {
  if (!round) return;
  round.display = round.display || { dealer: [], hands: [] };
  // A split rewrites a hand in place without changing its length, so match on
  // the card itself rather than trusting the count.
  const sync = (shown, cards) => {
    cards.forEach((v, i) => { if (!shown[i] || shown[i].value !== v) shown[i] = makeDisplayCard(v); });
    shown.length = cards.length;
  };
  sync(round.display.dealer, round.dealerCards);
  round.hands.forEach((h, i) => {
    round.display.hands[i] = round.display.hands[i] || [];
    sync(round.display.hands[i], h.cards);
  });
  round.display.hands.length = round.hands.length;
}

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

const shuffled = (list) => list.map((v) => [Math.random(), v]).sort((a, b) => a[0] - b[0]).map(([, v]) => v);

function wantsRuleQuestion() {
  const mode = state.settings.mode;
  if (mode === 'hands') return true;
  if (mode === 'play') return false;
  return Math.random() < 0.35;   // in Mix, roughly one hand in three
}

// Show a rule and ask which hands it covers. The three wrong choices are drawn
// from the same family where possible, so the answer is not given away by shape.
function dealRule() {
  round = null;
  const groups = ruleGroups(state.settings.rules);
  const deck = ruleDeckFor(state, groups);
  const pick = pickNext(deck, { step: state.step, lastId, sinceNew: state.sinceNew, workingSet: 6, newEvery: 4 });
  if (!pick) { quiz = null; hand = null; renderDrill(); return; }
  const group = groups.find((g) => g.id === pick.id);
  const others = groups.filter((g) => g.id !== group.id);
  const family = group.rows[0].section;
  const near = shuffled(others.filter((g) => g.rows[0].section === family));
  const far = shuffled(others.filter((g) => g.rows[0].section !== family));
  const options = shuffled([group, ...[...near, ...far].slice(0, 3)]);

  quiz = { group, options, cardId: pick.id, chosen: null };
  hand = null;
  answered = null;
  unsure = false;
  peeked = false;
  lastId = pick.id;
  state.sinceNew = pick.stage === 'new' ? 0 : state.sinceNew + 1;
  renderDrill();
}

function deal(id) {
  // Ask for the count before dealing, while the shoe still holds what you saw.
  if (!id && countingOn() && state.count.seen > 0 && state.count.sinceCheck >= state.settings.counting.every) {
    countCheck = { guess: 0, answered: false };
    hand = null;
    quiz = null;
    round = null;
    renderDrill();
    return;
  }
  countCheck = null;
  if (!id && wantsRuleQuestion()) { dealRule(); return; }
  quiz = null;
  const deck = filteredDeck();
  let pick = null;
  if (id) pick = ensureCard(state, id);
  else pick = pickNext(deck, { step: state.step, lastId, sinceNew: state.sinceNew });
  if (!pick) { hand = null; renderDrill(); return; }
  state.sinceNew = pick.stage === 'new' ? 0 : state.sinceNew + 1;
  hand = dealScenario(pick.id);
  analysis = analyzeHand(hand.playerCards, hand.upcard, state.settings.rules);
  book = lookupStrategy(hand.playerCards, hand.upcard, state.settings.rules);
  if (state.settings.table) {
    const counting = countingOn();
    freshShoe = counting ? ensureShoe([...hand.playerCards, hand.upcard]) : false;
    round = startRound({
      playerCards: hand.playerCards, upcard: hand.upcard, rules: state.settings.rules,
      bet: state.settings.bet, shoe: counting ? state.shoe : null,
    });
    if (counting) {
      state.count.beforeRound = state.count.running;
      state.count.seenBefore = state.count.seen;
      syncCount();
    }
  } else {
    round = null;
  }
  if (round) { round.display = { dealer: [hand.dealerDisplay], hands: [[...hand.playerDisplay]] }; syncDisplay(); }
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

function tallyAnswer(correct, low) {
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
}

function answer(action, lowConfidence) {
  if (!hand || answered) return;
  const low = Boolean(lowConfidence || unsure || peeked);
  const correct = action === book.action;
  answered = { action, correct, lowConfidence: low };

  const card = ensureCard(state, hand.scenario.id);
  state.cards[card.id] = gradeCard(card, { correct, lowConfidence: low, step: state.step, settings: { retireStreak: state.settings.retireStreak } });
  state.step += 1;
  lastId = card.id;

  tallyAnswer(correct, low);
  // The graded call is also the opening move of the round.
  if (round) advanceRound(action);
  saveState(state);
  buzz(correct ? 14 : [26, 50, 26]);
  renderDrill({ felt: Boolean(round) });
}

function answerRule(index, lowConfidence) {
  if (!quiz || quiz.chosen !== null) return;
  const low = Boolean(lowConfidence || unsure);
  const correct = quiz.options[index].id === quiz.group.id;
  quiz.chosen = index;
  quiz.correct = correct;
  quiz.lowConfidence = low;

  const card = ensureRuleCard(state, quiz.cardId);
  state.ruleCards[card.id] = gradeCard(card, { correct, lowConfidence: low, step: state.step, settings: { retireStreak: state.settings.retireStreak } });
  state.step += 1;
  tallyAnswer(correct, low);
  saveState(state);
  buzz(correct ? 14 : [26, 50, 26]);
  renderDrill({ felt: false });
}

function advanceRound(action) {
  act(round, action);
  syncDisplay();
  syncCount();
  if (round.stage === 'done' && !round.banked) {
    round.banked = true;
    recordRound(state.bank, round.result);
    if (countingOn()) state.count.sinceCheck += 1;
  }
}

function playMove(action) {
  if (!round || round.stage !== 'player') return;
  advanceRound(action);
  saveState(state);
  buzz(round.stage === 'done' ? 16 : 10);
  renderDrill();
}

function retireCurrent() {
  if (quiz) {
    const card = ensureRuleCard(state, quiz.cardId);
    state.ruleCards[card.id] = retireCard(card, state.step);
  } else if (hand) {
    const card = ensureCard(state, hand.scenario.id);
    state.cards[card.id] = retireCard(card, state.step);
  } else return;
  saveState(state);
  deal();
}

/* ---------------- drill view ---------------- */

function renderStrip() {
  const deck = quiz ? ruleDeckFor(state, ruleGroups(state.settings.rules)) : deckFor(state);
  const counts = cardCounts(deck);
  const due = dueCount(deck, state.step);
  const acc = state.session.answered ? pct0(state.session.correct / state.session.answered) : '--';
  const noun = quiz ? 'rules' : 'chart cells';
  $('#strip').innerHTML = `
    <span><b class="num">${state.session.answered}</b> hands · <b class="num">${acc}</b> right</span>
    <span class="dots" aria-label="How the last ten answers went">${state.session.recent.slice(-10).map((r) => `<i class="${r}"></i>`).join('')}</span>
    <span class="num" title="${noun} mastered${due ? `, ${due} due for review` : ''}">${counts.retired}/${counts.total}${due ? ` \u00b7 ${due} due` : ''}</span>`;
  const bankbar = $('#bankbar');
  bankbar.hidden = !state.settings.table;
  if (state.settings.table) {
    const b = state.bank;
    const net = b.balance - b.start;
    bankbar.className = `bankbar ${net > 0 ? 'up' : net < 0 ? 'down' : ''}`;
    bankbar.innerHTML = `<span class="bank-now num">${money(b.balance)}</span>
      <span class="bank-net num">${moneySigned(net)}</span>
      <span class="bank-sub num">${b.hands} ${b.hands === 1 ? 'hand' : 'hands'} \u00b7 ${money(state.settings.bet)} a hand</span>`;
  }
  $('#modes').innerHTML = MODES.map((m) =>
    `<button class="seg" type="button" data-mode="${m.id}" aria-pressed="${state.settings.mode === m.id}">${m.label}</button>`).join('');
}

function settleText() {
  const r = round.result;
  const alive = r.perHand.some((h) => !h.bust && !h.surrendered);
  const dealer = !alive ? 'Dealer never had to play'
    : r.dealerBust ? `Dealer busted on ${r.dealerTotal}`
    : `Dealer ${r.dealerTotal}`;
  const hands = r.perHand.map((h) => {
    if (h.surrendered) return 'you took half back';
    if (h.bust) return `your ${h.total} busted`;
    if (h.outcome === 'win') return `your ${h.total} won`;
    if (h.outcome === 'push') return `your ${h.total} pushed`;
    return `your ${h.total} lost`;
  });
  return `${dealer} · ${hands.join(' · ')}`;
}

function renderTableFelt() {
  syncDisplay();
  const d = round.display;
  const dealerHead = round.revealed ? String(handTotal(round.dealerCards).total) : rankLabel(round.upcard);
  const dealerCards = round.revealed ? d.dealer.map(cardFace).join('') : cardFace(d.dealer[0]) + cardBack;
  const many = round.hands.length > 1;

  const hands = round.hands.map((h, i) => {
    const { total, soft } = handTotal(h.cards);
    const live = round.stage === 'player' && i === round.active;
    const where = h.bust ? 'Bust' : h.surrendered ? 'Gave up' : `${soft ? 'Soft ' : ''}${total}`;
    return `<div class="hand-row${live ? ' live' : ''}">
      <div class="hand-label">
        <span class="eyebrow">${many ? `Hand ${i + 1}` : 'Your hand'}</span>
        <span class="stake${h.doubled ? ' doubled' : ''}">${money(h.bet)}</span>
        <span class="hand-total">${where}</span>
      </div>
      <div class="cards">${d.hands[i].map(cardFace).join('')}</div>
    </div>`;
  }).join('');

  $('#felt').innerHTML = `
    ${freshShoe ? '<p class="shuffle">Fresh shoe \u2014 the count starts over.</p>' : ''}
    <div class="hand-row">
      <div class="hand-label"><span class="eyebrow">Dealer</span><span class="hand-total num">${dealerHead}</span></div>
      <div class="cards">${dealerCards}</div>
    </div>
    ${hands}`;
}

function renderFelt() {
  if (!hand) { $('#felt').innerHTML = '<p class="empty">Nothing left in this filter. Every hand here is mastered.</p>'; return; }
  if (round) { renderTableFelt(); return; }
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

const shortRowLabel = (row) =>
  (row.section === 'pairs' ? rowLabel('pairs', row.key) : `${row.section === 'hard' ? 'H' : 'S'}${row.key}`);

// The rows a rule covers, drawn as the strip of chart they actually are.
function miniGrid(rows) {
  const rules = state.settings.rules;
  const chart = chartFor(rules);
  const header = '<div class="rh"></div>' + UPCARDS.map((u) => `<div class="hd">${rankLabel(u)}</div>`).join('');
  const body = rows.map((row) => {
    const cells = UPCARDS.map((up, col) => {
      const code = chart[row.section][row.key][col];
      return `<div class="cell a-${codeToAction(code, rules)}">${code}</div>`;
    }).join('');
    return `<div class="rh">${shortRowLabel(row)}</div>${cells}`;
  }).join('');
  return `<div class="chart-scroll"><div class="grid">${header}${body}</div></div>`;
}

function renderRuleFelt() {
  $('#felt').innerHTML = `
    <div class="rule-prompt">
      <span class="eyebrow">The rule</span>
      <p class="rule-text">${quiz.group.rule}</p>
      ${quiz.chosen === null ? '<p class="rule-ask">Which hands does this cover?</p>' : ''}
    </div>`;
}

function renderRulePending() {
  $('#answer').innerHTML = `
    <div class="panel" style="display:grid;gap:11px">
      <button class="unsure" id="unsureBtn" type="button" aria-pressed="${unsure}">
        <span class="box" aria-hidden="true">${unsure ? '\u2713' : ''}</span>
        <span>Not sure about this one</span>
        <span class="hint">or hold a button</span>
      </button>
      <div class="options">${quiz.options.map((g, i) =>
        `<button class="option" type="button" data-opt="${i}"><span class="key">${i + 1}</span>${g.label}</button>`).join('')}</div>
    </div>`;
}

function renderRuleAnswered() {
  const card = state.ruleCards[quiz.cardId];
  const justRetired = card && card.stage === 'retired';
  const toGo = Math.max(0, state.settings.retireStreak - (card?.hcStreak || 0));
  const chosen = quiz.options[quiz.chosen];
  $('#answer').innerHTML = `
    <div class="panel" style="display:grid;gap:12px">
      <div class="verdict ${quiz.correct ? 'good' : 'bad'}">
        <span class="mark" aria-hidden="true">${quiz.correct ? '\u2713' : '\u2715'}</span>
        <div class="verdict-text">
          <div class="verdict-head"><b>${quiz.group.label}</b></div>
          ${quiz.correct ? '' : `<div class="verdict-sub">You picked ${chosen.label} \u2014 that one is \u201c${chosen.rule.replace(/\.$/, '')}\u201d.</div>`}
        </div>
      </div>

      <div class="btn-row">
        ${justRetired
          ? '<span class="note" style="flex:1 1 auto;align-self:center">Mastered. You will not see this rule again.</span>'
          : '<button class="btn" id="retireBtn" type="button">Too easy \u2014 retire</button>'}
        <button class="btn btn-primary" id="nextBtn" type="button">Next</button>
      </div>

      <div>
        <div class="section-head" style="margin:0 0 8px"><h2 class="section-title">On the chart</h2></div>
        ${miniGrid(quiz.group.rows)}
      </div>

      ${justRetired || quiz.lowConfidence || !quiz.correct ? '' : `<p class="note">${toGo} more confident ${toGo === 1 ? 'answer' : 'answers'} and this rule retires itself.</p>`}
      ${quiz.lowConfidence ? '<p class="note">Marked shaky \u2014 this one comes back soon.</p>' : ''}
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

// In table mode the round has to finish before the next hand is offered, so the
// live controls take the place of those buttons until the bet is settled.
function tableBlock(justRetired) {
  const buttons = `<div class="btn-row">
    ${justRetired
      ? '<span class="note" style="flex:1 1 auto;align-self:center">Mastered. You will not see this hand again.</span>'
      : '<button class="btn" id="retireBtn" type="button">Too easy — retire</button>'}
    <button class="btn btn-primary" id="nextBtn" type="button">Next hand</button>
  </div>`;
  if (!round) return buttons;

  if (round.stage === 'player') {
    const moves = legalMoves(round);
    const live = ['hit', 'stand', 'double', 'split', 'surrender'].filter((a) => moves.includes(a));
    return `<div class="live">
      <div class="live-head"><span class="eyebrow">Play it out</span>
        <span class="live-note">${round.hands.length > 1 ? `Hand ${round.active + 1} of ${round.hands.length}` : 'Finish the hand'}</span></div>
      <div class="actions">${live.map((a, i) => `<button class="action a-${a}${live.length % 2 === 1 && i === live.length - 1 ? ' wide' : ''}" type="button" data-play="${a}">
          <span class="key">${ACTION_KEY[a]}</span>${ACTION_NAME[a]}</button>`).join('')}</div>
    </div>`;
  }

  const delta = round.result.delta;
  const tone = delta > 0 ? 'good' : delta < 0 ? 'bad' : 'flat';
  return `<div class="settle ${tone}">
      <div class="settle-top"><span class="settle-amount">${moneySigned(delta)}</span>
        <span class="settle-bank">Bank ${money(state.bank.balance)}</span></div>
      <p class="settle-detail">${settleText()}</p>
    </div>
    ${buttons}`;
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

      ${tableBlock(justRetired)}

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

function answerCountCheck() {
  if (!countCheck || countCheck.answered) return;
  const actual = state.count.running;
  const off = Math.abs(countCheck.guess - actual);
  countCheck.answered = true;
  countCheck.actual = actual;
  countCheck.correct = off === 0;
  state.count.checks += 1;
  if (off === 0) state.count.correct += 1;
  state.count.error += off;
  state.count.sinceCheck = 0;
  saveState(state);
  buzz(off === 0 ? 14 : [26, 50, 26]);
  renderDrill();
}

function renderCountFelt() {
  const c = countCheck;
  $('#felt').innerHTML = `
    <div class="rule-prompt">
      <span class="eyebrow">Count check</span>
      <p class="rule-text">${c.answered
        ? `The running count is ${formatCount(c.actual)}.`
        : 'What is the running count?'}</p>
      ${c.answered ? '' : `<p class="count-guess num">${formatCount(c.guess)}</p>`}
    </div>`;
}

function renderCountCheckPanel() {
  const c = countCheck;
  if (!c.answered) {
    return `<div class="panel" style="display:grid;gap:11px">
      <div class="stepper">${[-5, -1, 1, 5].map((n) =>
        `<button class="step" type="button" data-step="${n}">${n > 0 ? '+' : '\u2212'}${Math.abs(n)}</button>`).join('')}</div>
      <button class="btn btn-primary" id="countCheckBtn" type="button">Check</button>
      <p class="note">${state.count.seen} cards seen \u00b7 ${countSystem().name} \u00b7 ${state.settings.rules.decks} decks</p>
    </div>`;
  }
  const left = cardsLeft(state.shoe);
  const tc = trueCount(countSystem(), c.actual, left);
  return `<div class="panel" style="display:grid;gap:12px">
      <div class="verdict ${c.correct ? 'good' : 'bad'}">
        <span class="mark" aria-hidden="true">${c.correct ? '\u2713' : '\u2715'}</span>
        <div class="verdict-text">
          <div class="verdict-head">${c.correct ? `Right, ${formatCount(c.actual)}` : `You said ${formatCount(c.guess)}, it is ${formatCount(c.actual)}`}</div>
          <div class="verdict-sub">True count ${formatTrue(tc)} with ${decksRemaining(left).toFixed(1)} decks left</div>
        </div>
      </div>
      <button class="btn btn-primary" id="nextBtn" type="button">Deal</button>
      <p class="note">${state.count.seen} cards seen this shoe \u00b7 ${state.count.checks} ${state.count.checks === 1 ? 'check' : 'checks'}, ${state.count.checks ? pct0(state.count.correct / state.count.checks) : '--'} exact</p>
    </div>`;
}

function renderCountBar() {
  const bar = $('#countbar');
  bar.hidden = !countingOn();
  if (!countingOn()) return;
  // Never show the answer while the question is on screen, Show or not.
  const show = state.settings.counting.show && !(countCheck && !countCheck.answered);
  const left = state.shoe ? cardsLeft(state.shoe) : state.settings.rules.decks * 52;
  const tc = trueCount(countSystem(), state.count.running, left);
  bar.innerHTML = `
    <span class="count-cell"><span class="cap">Running</span><b class="num">${show ? formatCount(state.count.running) : '\u2022\u2022'}</b></span>
    <span class="count-cell"><span class="cap">True</span><b class="num">${show ? formatTrue(tc) : '\u2022\u2022'}</b></span>
    <span class="count-cell"><span class="cap">Decks left</span><b class="num">${decksRemaining(left).toFixed(1)}</b></span>
    <button class="peek" id="countPeek" type="button" aria-pressed="${show}">${show ? 'Hide' : 'Show'}</button>`;
}

function renderDrill({ felt = true } = {}) {
  renderStrip();
  renderCountBar();
  if (countCheck) {
    renderCountFelt();
    $('#answer').innerHTML = renderCountCheckPanel();
    return;
  }
  if (quiz) {
    renderRuleFelt();
    if (quiz.chosen === null) renderRulePending(); else renderRuleAnswered();
    return;
  }
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

// The bankroll over time. One series, so no legend: the heading names it.
function bankChart() {
  const b = state.bank;
  const h = b.history;
  if (h.length < 2) return '<p class="empty">Turn on Play the hand out in Settings and the curve starts here.</p>';

  const W = 340, H = 142, L = 48, R = 54, T = 16, B = 20;
  const lo = Math.min(...h, b.start);
  const hi = Math.max(...h, b.start);
  const pad = (hi - lo) * 0.1 || Math.max(1, b.start * 0.02);
  const [y0, y1] = [lo - pad, hi + pad];
  const x = (i) => L + (i / (h.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);

  const net = b.balance - b.start;
  const tone = net >= 0 ? 'var(--win)' : 'var(--lose)';
  const pts = h.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const endX = x(h.length - 1);
  const endY = y(b.balance);
  const startY = y(b.start);
  // Only label the start line when it will not sit on top of the high or low label.
  const roomy = Math.abs(startY - y(hi)) > 13 && Math.abs(startY - y(lo)) > 13;

  return `<svg viewBox="0 0 ${W} ${H}" class="bank-chart" role="img"
      aria-label="Bankroll over ${b.hands} hands, from ${money(b.start)} to ${money(b.balance)}">
    <line x1="${L}" y1="${startY.toFixed(1)}" x2="${W - R}" y2="${startY.toFixed(1)}" stroke="var(--line-strong)" stroke-width="1"/>
    <polygon points="${L},${(H - B).toFixed(1)} ${pts} ${endX.toFixed(1)},${(H - B).toFixed(1)}" fill="${tone}" opacity="0.1"/>
    <polyline points="${pts}" fill="none" stroke="${tone}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${endX.toFixed(1)}" cy="${endY.toFixed(1)}" r="4" fill="${tone}" stroke="var(--surface)" stroke-width="2"/>
    <text x="${L - 6}" y="${(y(hi) + 3.5).toFixed(1)}" class="ax" text-anchor="end">${money(hi)}</text>
    <text x="${L - 6}" y="${(y(lo) + 3.5).toFixed(1)}" class="ax" text-anchor="end">${money(lo)}</text>
    ${roomy ? `<text x="${L - 6}" y="${(startY + 3.5).toFixed(1)}" class="ax dim" text-anchor="end">${money(b.start)}</text>` : ''}
    <text x="${(endX + 7).toFixed(1)}" y="${(endY + 3.5).toFixed(1)}" class="ax end">${money(b.balance)}</text>
  </svg>`;
}

function renderStats() {
  const deck = deckFor(state);
  const counts = cardCounts(deck);
  const ruleCounts = cardCounts(ruleDeckFor(state, ruleGroups(state.settings.rules)));
  const bank = state.bank;
  const net = bank.balance - bank.start;
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
      <div class="section-head"><h2 class="section-title">Bankroll</h2>
        <span class="section-note">${bank.hands ? `${bank.hands} ${bank.hands === 1 ? 'hand' : 'hands'} played` : 'not started'}</span></div>
      ${bankChart()}
      <div class="tiles" style="margin-top:12px">
        <div class="tile"><span class="cap">Net</span><span class="val num" style="color:${net > 0 ? 'var(--win)' : net < 0 ? 'var(--lose)' : 'inherit'}">${moneySigned(net)}</span><span class="sub">from ${money(bank.start)}</span></div>
        <div class="tile"><span class="cap">Per hand</span><span class="val num">${bank.hands ? moneySigned(net / bank.hands) : '--'}</span><span class="sub">average swing</span></div>
        <div class="tile"><span class="cap">Return</span><span class="val num">${bank.wagered ? `${net < 0 ? '\u2212' : net > 0 ? '+' : ''}${Math.abs(net / bank.wagered * 100).toFixed(1)}%` : '--'}</span><span class="sub">on ${money(bank.wagered)} wagered</span></div>
        <div class="tile"><span class="cap">Won</span><span class="val num">${bank.hands ? pct0(bank.won / bank.hands) : '--'}</span><span class="sub">${bank.won}W ${bank.pushed}P ${bank.lost}L</span></div>
      </div>
      <p class="note" style="margin-top:10px">High ${money(bank.peak)} · low ${money(bank.low)}. Reset the bankroll in Settings.</p>
    </div>

    ${state.count.checks || state.settings.counting.on ? `<div class="panel">
      <div class="section-head"><h2 class="section-title">Counting</h2>
        <span class="section-note">${countSystem().name} \u00b7 ${state.count.shoes} ${state.count.shoes === 1 ? 'shoe' : 'shoes'}</span></div>
      <div class="tiles">
        <div class="tile"><span class="cap">Exact</span><span class="val num">${state.count.checks ? pct0(state.count.correct / state.count.checks) : '--'}</span><span class="sub">of ${state.count.checks} ${state.count.checks === 1 ? 'check' : 'checks'}</span></div>
        <div class="tile"><span class="cap">Average miss</span><span class="val num">${state.count.checks ? (state.count.error / state.count.checks).toFixed(1) : '--'}</span><span class="sub">cards out either way</span></div>
      </div>
      <p class="note" style="margin-top:10px">${state.settings.counting.on
        ? `Running ${formatCount(state.count.running)} with ${state.count.seen} cards seen this shoe.`
        : 'Counting is off. Turn it on in Settings to keep a shoe going.'}</p>
    </div>` : ''}

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Where the deck stands</h2><span class="section-note">${counts.total} cells</span></div>
      <div class="bars">${stageBars.map((b) => `<div class="bar-row">
        <span class="lbl">${b.label}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(b.n / counts.total * 100).toFixed(1)}%"></span></span>
        <span class="amt">${b.n}</span></div>`).join('')}</div>
      <p class="note" style="margin-top:10px">Name the hands runs its own deck: ${ruleCounts.retired} of ${ruleCounts.total} rules mastered.</p>
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
      <div class="section-head"><h2 class="section-title">Money</h2>
        <span class="section-note">${s.table ? `${money(state.bank.balance)} in the bank` : 'off'}</span></div>
      <div class="field">
        <div class="field-head"><span class="name">Play the hand out</span>
          <button class="switch" role="switch" data-opt="table" aria-checked="${s.table}" aria-label="Play the hand out"></button></div>
        <p class="field-desc">After the graded call, finish the hand against the dealer and settle the bet. Cards come from a real ${r.decks}-deck shoe with the cards you can see taken out.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Bet a hand</span></div>
        <div class="chips">${[5, 10, 25, 100].map((n) => opt(`bet:${n}`, money(n), s.bet === n)).join('')}</div>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Bankroll</span></div>
        <p class="field-desc">${state.bank.hands
          ? `${money(state.bank.balance)} after ${state.bank.hands} ${state.bank.hands === 1 ? 'hand' : 'hands'}, ${moneySigned(state.bank.balance - state.bank.start)} on ${money(state.bank.wagered)} wagered.`
          : 'No hands played yet.'}</p>
        <div class="chips">${[500, 1000, 5000].map((n) => opt(`bankstart:${n}`, money(n), state.bank.start === n)).join('')}</div>
        <div id="bankZone"><button class="btn danger" id="bankResetBtn" type="button">Reset bankroll</button></div>
        <p class="field-desc">Resetting the bankroll clears the money and the curve. It leaves your mastered hands alone.</p>
      </div>
    </div>

    <div class="panel">
      <div class="section-head"><h2 class="section-title">Counting</h2>
        <span class="section-note">${s.counting.on ? countSystem().name : 'off'}</span></div>
      <div class="field">
        <div class="field-head"><span class="name">Count the shoe</span>
          <button class="switch" role="switch" data-opt="counting" aria-checked="${s.counting.on}" aria-label="Count the shoe"></button></div>
        <p class="field-desc">Keeps one shoe going across hands instead of a fresh one each time, and asks you for the running count now and then. Needs Play the hand out, which it turns on for you.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">System</span></div>
        <div class="chips">${readySystems().map((sys) => opt(`csystem:${sys.id}`, sys.name, s.counting.system === sys.id)).join('')}</div>
        <p class="field-desc">${countSystem().blurb}</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Ask me every</span></div>
        <div class="chips">${[3, 5, 10, 25].map((n) => opt(`cevery:${n}`, `${n} hands`, s.counting.every === n)).join('')}</div>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Deal out</span></div>
        <div class="chips">${[0.5, 0.67, 0.75, 0.85].map((n) => opt(`cpen:${n}`, `${Math.round(n * 100)}%`, s.counting.penetration === n)).join('')}</div>
        <p class="field-desc">How deep the shoe goes before it is shuffled. Deeper is better for a counter and is the first thing a casino takes away.</p>
      </div>
      <div class="field">
        <div class="field-head"><span class="name">Show the count as I go</span>
          <button class="switch" role="switch" data-opt="cshow" aria-checked="${s.counting.show}" aria-label="Show the count"></button></div>
        <p class="field-desc">Leave this off to practise. The bar above the table hides the numbers until you tap Show.</p>
      </div>
      <p class="field-desc">Next up: converting to a true count on its own, a betting ramp, and the chart cells that move with the count. ${Object.values(COUNT_SYSTEMS).filter((x) => !x.ready).length} further systems are already written down and waiting on those.</p>
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
  if (quiz) { deal(); return; }
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

$('#countbar').addEventListener('click', (e) => {
  if (!e.target.closest('#countPeek')) return;
  state.settings.counting.show = !state.settings.counting.show;
  saveState(state);
  renderCountBar();
});

$('#modes').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-mode]');
  if (!btn || btn.dataset.mode === state.settings.mode) return;
  state.settings.mode = btn.dataset.mode;
  saveState(state);
  deal();
});

// Drill: a tap answers, a long hold answers with low confidence.
let holdTimer = null;
let holdFired = false;

$('#answer').addEventListener('pointerdown', (e) => {
  const btn = e.target.closest('.action, .option');
  if (!btn || btn.disabled || btn.dataset.play) return;
  holdFired = false;
  holdTimer = setTimeout(() => {
    holdFired = true;
    buzz(18);
    unsure = true;
    if (btn.dataset.opt !== undefined) answerRule(Number(btn.dataset.opt), true);
    else answer(btn.dataset.action, true);
  }, 420);
});
const clearHold = () => { if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } };
$('#answer').addEventListener('pointerup', clearHold);
$('#answer').addEventListener('pointercancel', clearHold);
$('#answer').addEventListener('pointerleave', clearHold);

$('#answer').addEventListener('click', (e) => {
  clearHold();
  if (holdFired) { holdFired = false; return; }
  const live = e.target.closest('[data-play]');
  if (live) { playMove(live.dataset.play); return; }
  const act = e.target.closest('.action');
  if (act) { answer(act.dataset.action, false); return; }
  const opt = e.target.closest('.option');
  if (opt) { answerRule(Number(opt.dataset.opt), false); return; }
  if (e.target.closest('#unsureBtn')) { unsure = !unsure; if (quiz) renderRulePending(); else renderPending(); return; }
  if (e.target.closest('#peekBtn')) { peeked = true; renderPending(); return; }
  const step = e.target.closest('[data-step]');
  if (step && countCheck && !countCheck.answered) {
    countCheck.guess += Number(step.dataset.step);
    renderCountFelt();
    return;
  }
  if (e.target.closest('#countCheckBtn')) { answerCountCheck(); return; }
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
    else if (kind === 'table') {
      state.settings.table = !state.settings.table;
      if (!state.settings.table) state.settings.counting.on = false;
      deal();
    }
    else if (kind === 'bet') {
      state.settings.bet = Number(value);
      // A hand that has not been acted on yet takes the new stake.
      if (round && !answered) { round.bet = state.settings.bet; round.hands[0].bet = state.settings.bet; }
    }
    else if (kind === 'bankstart') { state.bank = freshBank(Number(value)); }
    else if (kind === 'counting') {
      state.settings.counting.on = !state.settings.counting.on;
      if (state.settings.counting.on) state.settings.table = true;
      state.count = freshCount();
      state.shoe = null;
      deal();
    } else if (kind === 'csystem') { state.settings.counting.system = value; state.count = freshCount(); state.shoe = null; deal(); }
    else if (kind === 'cevery') state.settings.counting.every = Number(value);
    else if (kind === 'cpen') state.settings.counting.penetration = Number(value);
    else if (kind === 'cshow') state.settings.counting.show = !state.settings.counting.show;
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
  if (e.target.closest('#bankResetBtn')) {
    $('#bankZone').innerHTML = '<div class="btn-row"><button class="btn" id="bankCancel" type="button">Keep it</button>'
      + '<button class="btn danger" id="bankConfirm" type="button">Reset the bankroll</button></div>';
    return;
  }
  if (e.target.closest('#bankCancel')) { renderSettings(); return; }
  if (e.target.closest('#bankConfirm')) {
    state.bank = freshBank(state.bank.start);
    saveState(state);
    renderSettings();
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
  if (quiz) {
    if (quiz.chosen !== null) {
      if (k === 'enter' || k === ' ' || k === 'n') { e.preventDefault(); deal(); }
      return;
    }
    if (k === 'u') { unsure = !unsure; renderRulePending(); return; }
    const n = Number(k);
    if (n >= 1 && n <= quiz.options.length) { e.preventDefault(); answerRule(n - 1, e.shiftKey); }
    return;
  }
  if (countCheck) {
    if (countCheck.answered) {
      if (k === 'enter' || k === ' ' || k === 'n') { e.preventDefault(); deal(); }
      return;
    }
    if (e.key === 'ArrowUp' || e.key === '+' || e.key === '=') { e.preventDefault(); countCheck.guess += 1; renderCountFelt(); return; }
    if (e.key === 'ArrowDown' || e.key === '-') { e.preventDefault(); countCheck.guess -= 1; renderCountFelt(); return; }
    if (k === 'enter' || k === ' ') { e.preventDefault(); answerCountCheck(); }
    return;
  }
  const keyToAction = { h: 'hit', s: 'stand', d: 'double', p: 'split', r: 'surrender' };
  if (round && round.stage === 'player' && answered) {
    if (keyToAction[k] && legalMoves(round).includes(keyToAction[k])) { e.preventDefault(); playMove(keyToAction[k]); }
    return;
  }
  if (answered) {
    if (k === 'enter' || k === ' ' || k === 'n') { e.preventDefault(); deal(); }
    return;
  }
  const map = keyToAction;
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
