// Renders the 1200x630 link-preview card from the same tokens the app uses.
import { createRequire } from 'node:module';
import { writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
// Only the root <svg> loses its intrinsic size; the shapes inside keep theirs
// and scale with the viewBox.
const svg = readFileSync(join(root, 'icons/icon.svg'), 'utf8').replace(
  /<svg([^>]*)>/,
  (_, attrs) => `<svg${attrs.replace(/\s(width|height)="[^"]*"/g, '')} style="display:block;width:100%;height:100%">`,
);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await page.setContent(`<body style="margin:0">
  <div style="width:1200px;height:630px;background:#0c0f14;color:#e9edf3;display:flex;align-items:center;gap:56px;padding:0 84px;box-sizing:border-box;font-family:system-ui,sans-serif">
    <div style="width:232px;height:232px;flex:none">${svg}</div>
    <div style="min-width:0">
      <div style="font-size:26px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#dfb264">Blackjack strategy drill</div>
      <div style="font-size:92px;font-weight:800;letter-spacing:-.03em;margin:14px 0 20px;line-height:1">Hard Sixteen</div>
      <div style="font-size:33px;line-height:1.35;color:#c2cad5">Every hand the book covers, with the odds<br>behind the play and the ones you own retired.</div>
      <div style="display:flex;gap:12px;margin-top:30px;font-size:24px;font-weight:700">
        <span style="background:#0ca30c22;color:#3ec43e;padding:9px 18px;border-radius:999px">Win</span>
        <span style="background:#8b939f22;color:#9aa4b2;padding:9px 18px;border-radius:999px">Push</span>
        <span style="background:#d03b3b22;color:#e8625f;padding:9px 18px;border-radius:999px">Lose</span>
      </div>
    </div>
  </div></body>`);
writeFileSync(join(root, 'icons/og.png'), await page.screenshot({ type: 'png' }));
await browser.close();
console.log('icons/og.png written');
