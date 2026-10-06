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
say(await page.locator('.tile').count() === 4, 'four progress tiles');
const tileVals = await page.locator('.tile .val').allTextContents();
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
