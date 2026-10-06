// The reverse drill: every chart row belongs to exactly one rule group, and a
// group's label names the rows it actually holds.
import { ruleGroups, groupLabel, HARD_TOTALS, SOFT_TOTALS, PAIR_RANKS } from '../src/engine/scenarios.js';
import { rowRule, rowLabel } from '../src/engine/strategy.js';
import { normalizeRules } from '../src/engine/rules.js';

let failures = 0;
const say = (ok, msg) => { if (!ok) failures += 1; console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); };

for (const variant of ['s17', 'h17']) {
  for (const surrender of [true, false]) {
    for (const das of [true, false]) {
      const rules = normalizeRules({ hitSoft17: variant === 'h17', surrender, das });
      const tag = `${variant.toUpperCase()}${surrender ? '' : ' no-LS'}${das ? '' : ' no-DAS'}`;
      const groups = ruleGroups(rules);

      const seen = new Set();
      let dupes = 0;
      for (const g of groups) for (const row of g.rows) {
        const key = `${row.section}${row.key}`;
        if (seen.has(key)) dupes += 1;
        seen.add(key);
      }
      const expected = HARD_TOTALS.length + SOFT_TOTALS.length + PAIR_RANKS.length;
      say(seen.size === expected && dupes === 0, `${tag}: all ${expected} rows land in exactly one of ${groups.length} groups`);

      say(new Set(groups.map((g) => g.id)).size === groups.length, `${tag}: group ids are unique`);
      say(new Set(groups.map((g) => g.rule)).size === groups.length, `${tag}: no two groups share a rule`);
      say(groups.every((g) => g.label.length > 0 && g.rule.length > 0), `${tag}: every group has a label and a rule`);
      say(groups.every((g) => g.rows.every((r) => rowRule(r.section, r.key, rules) === g.rule)),
        `${tag}: every row in a group really has that group's rule`);
      say(groups.length >= 4, `${tag}: enough groups to build a four-way choice (${groups.length})`);
    }
  }
}

// Labels collapse runs and keep the odd ones out.
{
  say(groupLabel([{ section: 'hard', key: 5 }, { section: 'hard', key: 6 }, { section: 'hard', key: 7 }, { section: 'hard', key: 8 }]) === 'Hard 5–8',
    'four straight hard totals read as a range');
  say(groupLabel([{ section: 'hard', key: 10 }, { section: 'pairs', key: 5 }]) === 'Hard 10 · 5,5',
    'a hard total and a pair read side by side');
  const spread = groupLabel([{ section: 'hard', key: 17 }, { section: 'hard', key: 18 }, { section: 'soft', key: 20 }, { section: 'pairs', key: 10 }]);
  say(spread === 'Hard 17–18 · Soft 20 · 10,10', `a mixed group lists each family: "${spread}"`);
  say(groupLabel([{ section: 'pairs', key: 1 }, { section: 'pairs', key: 8 }]) === 'A,A · 8,8', 'aces sort ahead of eights');
}

// The headline cases the drill is built around.
{
  const rules = normalizeRules({});
  const groups = ruleGroups(rules);
  const by = (text) => groups.find((g) => g.rule === text);
  say(by('Always hit.')?.label === 'Hard 5–8', `always hit is hard 5 through 8 ("${by('Always hit.')?.label}")`);
  say(by('Always split.')?.label === 'A,A · 8,8', `always split is aces and eights ("${by('Always split.')?.label}")`);
  say(by('Always stand.')?.rows.length === 6, `always stand covers six rows (${by('Always stand.')?.rows.length})`);
}

console.log(failures === 0 ? '\nAll rule-deck checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
