// Flattens the app into one file: the stylesheet inlined, the ES modules
// concatenated in dependency order with their import and export keywords
// removed, and the markup lifted out of index.html.
//
// Two outputs:
//   dist/hard-sixteen.html           a standalone page you can open from disk
//   dist/hard-sixteen.artifact.html  the same page without the document shell
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(root, p), 'utf8');

const IMPORT_RE = /^import\s+\{[^}]*\}\s+from\s+['"](\.[^'"]+)['"];?\s*$/gm;

const order = [];
const seen = new Set();

function visit(file) {
  if (seen.has(file)) return;
  seen.add(file);
  const src = readFileSync(file, 'utf8');
  for (const m of src.matchAll(IMPORT_RE)) visit(resolve(dirname(file), m[1]));
  order.push(file);
}
visit(join(root, 'src/app/main.js'));

const modules = order.map((file) => {
  const rel = file.slice(root.length + 1);
  const body = readFileSync(file, 'utf8')
    .replace(IMPORT_RE, '')
    .replace(/^export\s+(const|let|function|class|async)\b/gm, '$1')
    .trimEnd();
  return `/* ${rel} */\n${body}\n`;
});

// One shared scope after flattening, so a name used twice would collide.
const declared = new Map();
for (let i = 0; i < order.length; i += 1) {
  for (const m of modules[i].matchAll(/^(?:const|let|function|class)\s+([A-Za-z_$][\w$]*)/gm)) {
    const prev = declared.get(m[1]);
    if (prev) throw new Error(`name collision after bundling: ${m[1]} in ${prev} and ${order[i].slice(root.length + 1)}`);
    declared.set(m[1], order[i].slice(root.length + 1));
  }
}

const css = read('src/styles.css');
const html = read('index.html');
const markup = html.split('<!-- #region app-markup -->')[1].split('<!-- #endregion app-markup -->')[0].trim();
const fontLink = html.match(/<link rel="stylesheet" href="https:\/\/fonts\.googleapis\.com[^>]*>/)[0];

const page = `<title>Hard Sixteen</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
${fontLink}
<style>
${css}
</style>
${markup}
<script>
(() => {
${modules.join('\n')}
})();
</script>
`;

const standalone = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Drill blackjack basic strategy with the win, push and lose odds behind every play.">
<meta name="theme-color" content="#f5f7f9" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#11151b" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Hard Sixteen">
${page}</body>
</html>
`;

mkdirSync(join(root, 'dist'), { recursive: true });
writeFileSync(join(root, 'dist/hard-sixteen.artifact.html'), page);
writeFileSync(join(root, 'dist/hard-sixteen.html'), standalone);
console.log(`bundled ${order.length} modules`);
for (const f of order) console.log(`  ${f.slice(root.length + 1)}`);
console.log(`dist/hard-sixteen.html            ${(standalone.length / 1024).toFixed(1)} KB`);
console.log(`dist/hard-sixteen.artifact.html   ${(page.length / 1024).toFixed(1)} KB`);
