// Renders icons/icon.svg to the PNG sizes the manifest and iOS ask for.
// Uses the Chromium that ships with this container, so there is no image
// library to install.
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// Playwright lives outside this project in the dev container, so resolve it the
// way CommonJS would rather than pinning a path.
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
// Only the root <svg> loses its intrinsic size; the shapes inside keep theirs
// and scale with the viewBox.
const svg = readFileSync(join(root, 'icons/icon.svg'), 'utf8').replace(
  /<svg([^>]*)>/,
  (_, attrs) => `<svg${attrs.replace(/\s(width|height)="[^"]*"/g, '')} style="display:block;width:100%;height:100%">`,
);

const TARGETS = [
  { file: 'icons/icon-192.png', size: 192, pad: 0 },
  { file: 'icons/icon-512.png', size: 512, pad: 0 },
  { file: 'icons/apple-touch-icon.png', size: 180, pad: 0 },
  // Maskable icons get cropped to a circle on some launchers, so inset the art.
  { file: 'icons/icon-maskable-512.png', size: 512, pad: 0.12 },
];

const browser = await chromium.launch();
const page = await browser.newPage();
for (const t of TARGETS) {
  const inset = Math.round(t.size * t.pad);
  await page.setViewportSize({ width: t.size, height: t.size });
  await page.setContent(`<body style="margin:0;background:#14181f">
    <div style="width:${t.size}px;height:${t.size}px;display:grid;place-items:center;background:#14181f">
      <div style="width:${t.size - inset * 2}px;height:${t.size - inset * 2}px">${svg}</div>
    </div></body>`);
  const shot = await page.locator('body').screenshot({ type: 'png' });
  writeFileSync(join(root, t.file), shot);
  console.log(`${t.file}  ${t.size}x${t.size}  ${(shot.length / 1024).toFixed(1)} KB`);
}
await browser.close();
