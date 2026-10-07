// Drives the built page in Chromium: plays hands, walks every tab, and checks
// that progress survives a reload. Writes screenshots next to the report.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const shots = process.env.SHOT_DIR || join(root, 'dist/shots');
mkdirSync(shots, { recursive: true });
const url = pathToFileURL(join(root, 'dist/hard-sixteen.html')).href;

let failures = 0;
const say = (ok, msg) => { if (!ok) failures += 1; console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: 'dark', ignoreHTTPSErrors: true });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(url);
await page.waitForSelector('.action');

// --- a hand is on the table ---
say(await page.locator('#felt .card').count() >= 3, 'the deal shows the dealer upcard, a hole card and two player cards');
say(await page.locator('#felt .card.back').count() === 1, 'the dealer hole card is face down');
const handName = await page.locator('#felt .hand-total').last().textContent();
say(/Hard|Soft|Pair/.test(handName), `the hand is named (${handName})`);

// --- answering shows the odds ---
await page.locator('.action').first().click();
await page.waitForSelector('.odds-figures');
const figures = await page.locator('.odds-figures .val').allTextContents();
say(figures.length === 3 && figures.every((f) => /%$/.test(f)), `win / push / lose all shown (${figures.join(' ')})`);
const sum = figures.reduce((a, f) => a + parseFloat(f), 0);
say(Math.abs(sum - 100) < 0.25, `win + push + lose = ${sum.toFixed(1)}%`);
say(await page.locator('.verdict').count() === 1, 'a verdict is shown');
say((await page.locator('.orow').count()) >= 3, 'every action is listed with its own odds');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'dark-answered.png'), fullPage: true });

// --- the comparison table carries a book marker ---
say(await page.locator('.tag.book').count() === 1, 'the book play is marked');

// --- next hand ---
await page.locator('#nextBtn').click();
await page.waitForSelector('.action');
say(await page.locator('.odds-figures').count() === 0, 'the odds are hidden again on the next hand');

// --- not sure, then answer ---
await page.locator('#unsureBtn').click();
say(await page.locator('#unsureBtn').getAttribute('aria-pressed') === 'true', 'the not-sure toggle arms');
await page.locator('.action').nth(1).click();
await page.waitForSelector('.odds-figures');
say((await page.locator('.note').allTextContents()).some((t) => /shaky/i.test(t)), 'a shaky answer says so');

// --- play twenty more hands, alternating actions ---
for (let i = 0; i < 20; i += 1) {
  await page.locator('#nextBtn').click();
  await page.waitForSelector('.action');
  const buttons = page.locator('.action');
  await buttons.nth(i % (await buttons.count())).click();
  await page.waitForSelector('#nextBtn');
}
const strip = await page.locator('#strip').textContent();
say(/22 hands/.test(strip), `the session counter tracks every hand (${strip.trim().split('·')[0].trim()})`);

// --- retire the hand in front of me ---
const beforeRetire = Number((await page.locator('#strip').textContent()).match(/(\d+)\/330/)[1]);
await page.locator('#retireBtn').click();
await page.waitForSelector('.action');
const afterRetire = Number((await page.locator('#strip').textContent()).match(/(\d+)\/330/)[1]);
say(afterRetire === beforeRetire + 1, `retiring a hand adds it to the mastered count (${beforeRetire} to ${afterRetire})`);

// --- the chart ---
await page.locator('.tab[data-view="chart"]').click();
await page.waitForSelector('.grid .cell');
const cells = await page.locator('.grid .cell').count();
say(cells === 330, `the chart renders every drillable cell (${cells})`);
say(await page.locator('.grid .cell[data-mastered="true"]').count() >= 1, 'mastered cells are marked on the chart');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'dark-chart.png'), fullPage: true });
await page.locator('.grid .cell').nth(42).click();
await page.waitForSelector('.sheet');
say((await page.locator('.sheet .odds-figures .val').count()) === 3, 'a chart cell opens its odds');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'dark-cell.png') });
await page.locator('#sheetClose').click();
say(await page.locator('.sheet').count() === 0, 'the sheet closes');

// --- progress ---
await page.locator('.tab[data-view="stats"]').click();
await page.waitForSelector('.tiles');
say(await page.locator('.tiles').first().locator('.tile').count() === 4, 'four progress tiles');
const tileVals = await page.locator('.tiles').first().locator('.tile .val').allTextContents();
say(tileVals.every((v) => v.length > 0), `tiles carry values (${tileVals.join(' / ')})`);
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'dark-progress.png'), fullPage: true });

// --- settings, and a rules change that moves the chart ---
await page.locator('.tab[data-view="settings"]').click();
await page.waitForSelector('#ioBox');
await page.locator('[data-opt="h17:1"]').click();
say((await page.locator('#rulesChip').textContent()).includes('H17'), 'switching to H17 updates the header');
await page.locator('.tab[data-view="chart"]').click();
await page.waitForSelector('.grid .cell');
const h11vsA = await page.locator('.grid .cell[data-cell="h11-1"]').textContent();
say(h11vsA === 'D', `H17 doubles hard 11 against an ace (chart shows ${h11vsA})`);
await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="h17:0"]').click();
await page.locator('.tab[data-view="chart"]').click();
say(await page.locator('.grid .cell[data-cell="h11-1"]').textContent() === 'H', 'S17 hits hard 11 against an ace');

// --- light mode ---
await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="theme:light"]').click();
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('.action');
await page.locator('.action').first().click();
await page.waitForSelector('.odds-figures');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'light-answered.png'), fullPage: true });
await page.locator('.tab[data-view="chart"]').click();
await page.waitForSelector('.grid .cell');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'light-chart.png'), fullPage: true });

// --- progress survives a reload ---
await page.reload();
await page.waitForSelector('.action');
const stripAfter = await page.locator('#strip').textContent();
say(/\/330/.test(stripAfter), 'the deck count is restored after a reload');
const restored = Number(stripAfter.match(/(\d+)\/330/)[1]);
say(restored === afterRetire, `mastered hands survive a reload (${restored})`);

// --- name the hands ---
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('.action');
await page.locator('.seg[data-mode="hands"]').click();
await page.waitForSelector('.option');
say(await page.locator('.option').count() === 4, 'the reverse drill offers four choices');
const ruleText = await page.locator('.rule-text').innerText();
say(/\.$/.test(ruleText), `a rule is posed as the question ("${ruleText}")`);
const labels = await page.locator('.option').allInnerTexts();
say(new Set(labels).size === 4, 'the four choices are all different');

// Find and click the right one by matching the rule shown after answering.
await page.locator('.option').first().click();
await page.waitForSelector('#answer .verdict');
say(await page.locator('#answer .grid .cell').count() >= 10, 'the answer shows the rows on the chart');
say(await page.locator('#nextBtn').count() === 1 && await page.locator('#retireBtn').count() === 1,
  'retire and next are offered on a rule question too');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'reverse.png'), fullPage: true });

// Scoring and the rule deck both move.
const beforeRules = await page.locator('#strip').textContent();
say(/\/\d+/.test(beforeRules), `the strip counts the rule deck (${beforeRules.trim().split('·').pop().trim()})`);

const handsBefore = Number((await page.locator('#strip').textContent()).match(/(\d+) hands/)[1]);
for (let i = 0; i < 8; i += 1) {
  await page.locator('#nextBtn').click();
  await page.waitForSelector('.option');
  await page.locator('.option').nth(i % 4).click();
  await page.waitForSelector('#nextBtn');
}
const handsAfter = Number((await page.locator('#strip').textContent()).match(/(\d+) hands/)[1]);
say(handsAfter === handsBefore + 8, `rule answers count toward the session (${handsBefore} to ${handsAfter})`);

// The mode sticks across a reload.
await page.reload();
await page.waitForSelector('.option, .action');
say(await page.locator('.seg[data-mode="hands"]').getAttribute('aria-pressed') === 'true', 'the chosen drill style survives a reload');

// Mix deals both kinds.
await page.locator('.seg[data-mode="mix"]').click();
let sawPlay = false;
let sawRule = false;
for (let i = 0; i < 40 && !(sawPlay && sawRule); i += 1) {
  if (await page.locator('.option').count()) { sawRule = true; await page.locator('.option').first().click(); }
  else { sawPlay = true; await page.locator('.action').first().click(); }
  await page.waitForSelector('#nextBtn');
  await page.locator('#nextBtn').click();
  await page.waitForSelector('.option, .action');
}
say(sawPlay && sawRule, 'mix deals both kinds of question');

await page.locator('.seg[data-mode="play"]').click();
await page.waitForSelector('.action');

// --- playing the hand out for money ---
await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="table"]').click();
await page.locator('[data-opt="bet:10"]').click();
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('#answer .action');
say(!(await page.locator('#bankbar').isHidden()), 'the bankroll shows once money is on');
say(/\$1,000/.test(await page.locator('#bankbar').innerText()), 'it opens at the starting bankroll');

// One hand, start to finish.
await page.locator('#answer .action').first().click();
await page.waitForSelector('#answer .live, #answer .settle');
say(await page.locator('#nextBtn').count() === 0 || await page.locator('#answer .settle').count() === 1,
  'the next hand is withheld until the bet is settled');
let guard = 0;
while (await page.locator('#answer .live').count() && guard++ < 14) {
  const stand = page.locator('[data-play="stand"]');
  if (await stand.count()) await stand.click(); else await page.locator('[data-play]').first().click();
  await page.waitForTimeout(20);
}
await page.waitForSelector('.settle');
say(await page.locator('.settle').count() === 1, 'the bet settles');
const settleText = await page.locator('.settle').innerText();
say(/\$\d/.test(settleText), `the settle names an amount (${settleText.split('\n')[0]})`);
say(await page.locator('.settle').innerText().then((t) => /Dealer|never had to play/.test(t)), 'the settle says what the dealer did');
say(await page.locator('#nextBtn').count() === 1, 'next hand returns once settled');
say(await page.locator('.odds-figures').count() === 1, 'the odds are still there under the money');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'table.png'), fullPage: true });

// The bank moves by the stake, never by some other number.
const bankAfterOne = Number((await page.locator('#bankbar').innerText()).match(/\$([\d,]+)/)[1].replace(/,/g, ''));
say([980, 990, 1000, 1010, 1020].includes(bankAfterOne), `a $10 hand moves the bank by a multiple of the stake (${bankAfterOne})`);

for (let i = 0; i < 14; i += 1) {
  await page.locator('#nextBtn').click();
  await page.waitForSelector('#answer .action');
  await page.locator('#answer .action').first().click();
  await page.waitForSelector('#answer .live, #answer .settle');
  let g2 = 0;
  while (await page.locator('#answer .live').count() && g2++ < 14) {
    const stand = page.locator('[data-play="stand"]');
    if (await stand.count()) await stand.click(); else await page.locator('[data-play]').first().click();
    await page.waitForTimeout(20);
  }
  await page.waitForSelector('.settle');
}
say(/15 hands/.test(await page.locator('#bankbar').innerText()), 'the bankroll counts every settled hand');

await page.locator('.tab[data-view="stats"]').click();
await page.waitForSelector('.bank-chart');
const curve = (await page.locator('.bank-chart polyline').getAttribute('points')).trim().split(/\s+/);
say(curve.length === 16, `the curve plots the opening bankroll and every hand (${curve.length} points)`);
const finite = curve.every((pt) => pt.split(',').every((n) => Number.isFinite(Number(n))));
say(finite, 'every point on the curve is a real coordinate');
say((await page.locator('.bank-chart text').allTextContents()).every((t) => /^\$|^\u2212\$/.test(t)), 'chart labels are all money');
await page.waitForTimeout(300);
await page.screenshot({ path: join(shots, 'bankroll.png'), fullPage: true });

// A split rewrites a hand in place; the faces on screen must follow it.
await page.locator('.tab[data-view="chart"]').click();
await page.waitForSelector('.grid .cell');
await page.locator('.cell[data-cell="p8-6"]').click();
await page.waitForSelector('.sheet');
await page.locator('[data-sheet-action="drill"]').click();
await page.waitForSelector('#answer .action[data-action="split"]');
await page.locator('#answer .action[data-action="split"]').click();
await page.waitForSelector('#answer .live, #answer .settle');
const rows = await page.locator('#felt .hand-row').count();
say(rows === 3, `a split puts two hands on the table (${rows - 1})`);
const shownTotals = await page.locator('#felt .hand-row').evaluateAll((els) => els.slice(1).map((el) => ({
  stated: Number(el.querySelector('.hand-total').textContent.replace(/\D/g, '')),
  ranks: [...el.querySelectorAll('.card .r')].map((r) => r.textContent),
})));
const faceValue = (r) => (r === 'A' ? 11 : ['J', 'Q', 'K', '10'].includes(r) ? 10 : Number(r));
const facesMatch = shownTotals.every((h) => h.ranks.reduce((a, r) => a + faceValue(r), 0) === h.stated);
say(facesMatch, `the cards on screen add up to the totals shown (${shownTotals.map((h) => `${h.ranks.join('+')}=${h.stated}`).join(' , ')})`);
const stakes = await page.locator('#felt .stake').allTextContents();
say(stakes.length === 2 && stakes.every((t) => t === stakes[0]), `each split hand carries its own stake (${stakes.join(' / ')})`);
const moves = await page.locator('[data-play]').evaluateAll((els) => els.map((e) => e.dataset.play));
say(!moves.includes('split') && !moves.includes('surrender'), `no resplit or surrender after a split (${moves.join(', ')})`);
await page.waitForTimeout(300);
await page.screenshot({ path: join(shots, 'table-split.png'), fullPage: true });
let g3 = 0;
while (await page.locator('#answer .live').count() && g3++ < 14) {
  const stand = page.locator('[data-play="stand"]');
  if (await stand.count()) await stand.click(); else await page.locator('[data-play]').first().click();
  await page.waitForTimeout(20);
}
await page.waitForSelector('.settle');

// Resetting the bankroll leaves the drill alone.
const masteredBefore = await page.locator('.tile .val').first().innerText();
await page.locator('.tab[data-view="settings"]').click();
await page.locator('#bankResetBtn').click();
await page.locator('#bankConfirm').click();
await page.locator('.tab[data-view="stats"]').click();
await page.waitForSelector('.tiles');
say(await page.locator('.tile .val').first().innerText() === masteredBefore, 'resetting the bankroll keeps your mastered hands');
say(/\$0/.test(await page.locator('.tile').nth(4).innerText()), `the net is back to zero after a reset (${(await page.locator('.tile').nth(4).innerText()).split('\n')[1]})`);

await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="table"]').click();
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('#answer .action');
say(await page.locator('#bankbar').isHidden(), 'turning money off hides the bankroll again');

// --- counting the shoe ---
await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="counting"]').click();
say(await page.locator('[data-opt="table"]').getAttribute('aria-checked') === 'true', 'counting switches the money game on with it');
await page.locator('[data-opt="cevery:3"]').click();
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('#answer .action');
say(!(await page.locator('#countbar').isHidden()), 'the count bar appears');
say(/\u2022\u2022/.test(await page.locator('#countbar').innerText()), 'the count is hidden until you ask for it');
await page.locator('#countPeek').click();
const barShown = await page.locator('#countbar').innerText();
say(!/\u2022\u2022/.test(barShown), `tapping Show reveals it (${barShown.replace(/\n/g, ' ')})`);

const playOut = async () => {
  await page.locator('#answer .action').first().click();
  await page.waitForSelector('#answer .live, #answer .settle');
  let g = 0;
  while (await page.locator('#answer .live').count() && g++ < 14) {
    const stand = page.locator('[data-play="stand"]');
    if (await stand.count()) await stand.click(); else await page.locator('[data-play]').first().click();
    await page.waitForTimeout(15);
  }
  await page.waitForSelector('.settle');
};

// The count must equal the Hi-Lo value of every card showing on a fresh shoe.
const hiloOf = (ranks) => ranks.reduce((n, r) => {
  const v = r === 'A' ? 1 : ['J', 'Q', 'K', '10'].includes(r) ? 10 : Number(r);
  return n + (v >= 2 && v <= 6 ? 1 : v === 1 || v === 10 ? -1 : 0);
}, 0);
await playOut();
const faceUp = await page.locator('#felt .card:not(.back) .r').allTextContents();
const barCount = Number((await page.locator('#countbar').innerText()).match(/Running\s*([+\u2212]?\d+)/i)[1].replace('\u2212', '-'));
say(barCount === hiloOf(faceUp), `the running count matches the cards on the table (${faceUp.join('+')} = ${hiloOf(faceUp)}, bar says ${barCount})`);

// After three hands it asks for the count.
await page.locator('#nextBtn').click();
await page.waitForSelector('#answer .action');
await playOut();
await page.locator('#nextBtn').click();
await page.waitForSelector('#answer .action');
await playOut();
const truth = Number((await page.locator('#countbar').innerText()).match(/Running\s*([+\u2212]?\d+)/i)[1].replace('\u2212', '-'));
await page.locator('#nextBtn').click();
await page.waitForSelector('.stepper, #answer .action');
say(await page.locator('.stepper').count() === 1, 'it stops to ask for the running count');
say(/\u2022\u2022/.test(await page.locator('#countbar').innerText()), 'the bar hides the answer while it is asking');
for (let i = 0; i < Math.abs(truth); i += 1) await page.locator(`[data-step="${truth > 0 ? 1 : -1}"]`).click();
await page.waitForTimeout(300);
await page.screenshot({ path: join(shots, 'count-check.png'), fullPage: true });
await page.locator('#countCheckBtn').click();
await page.waitForSelector('#answer .verdict');
say(await page.locator('#answer .verdict.good').count() === 1, `answering with the true count is marked right (${truth})`);
say(/True count/.test(await page.locator('#answer .verdict-sub').innerText()), 'the true count is shown with it');
await page.locator('#nextBtn').click();
await page.waitForSelector('#answer .action');
say(await page.locator('.stepper').count() === 0, 'the drill carries on after the check');

await page.locator('.tab[data-view="stats"]').click();
await page.waitForSelector('.tiles');
const countPanel = await page.locator('.panel').filter({ hasText: 'Counting' }).first().innerText();
say(/100%|0%/.test(countPanel), `the counting panel reports accuracy (${countPanel.split('\n').slice(0, 4).join(' ').trim()})`);

await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="counting"]').click();
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('#answer .action');
say(await page.locator('#countbar').isHidden(), 'turning counting off hides the bar');
await page.locator('.tab[data-view="settings"]').click();
await page.locator('[data-opt="table"]').click();
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('#answer .action');

// --- a wide screen still works ---
await page.setViewportSize({ width: 900, height: 820 });
await page.locator('.tab[data-view="drill"]').click();
await page.waitForSelector('.action');
await page.waitForTimeout(350);
await page.screenshot({ path: join(shots, 'wide-drill.png'), fullPage: true });
const scrollW = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
say(scrollW <= 0, `no horizontal page scroll at 900px (overflow ${scrollW}px)`);
await page.setViewportSize({ width: 360, height: 780 });
await page.waitForTimeout(80);
const narrowOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
say(narrowOverflow <= 0, `no horizontal page scroll at 360px (overflow ${narrowOverflow}px)`);

// The only resources this page fetches are the Google Fonts stylesheets, and
// they come through the container's TLS proxy, which Chromium often rejects.
// That is the sandbox, not the page, so resource-load noise is filtered out.
const real = errors.filter((e) => !/Failed to load resource|ERR_CERT_AUTHORITY_INVALID|fonts\.(googleapis|gstatic)/.test(e));
say(real.length === 0, `no console errors${real.length ? ': ' + real.slice(0, 3).join(' | ') : ''}`);

await browser.close();
console.log(failures === 0 ? '\nAll interface checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
