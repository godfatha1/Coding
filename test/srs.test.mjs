// Checks the scheduler does the two things the drill promises: retire hands you
// own, and keep bringing back hands you are shaky on.
import { newCard, gradeCard, pickNext, retireCard, reviveCard, cardCounts, SRS_DEFAULTS } from '../src/engine/srs.js';
import { buildScenarioIds, parseScenario } from '../src/engine/scenarios.js';

let failures = 0;
const say = (ok, msg) => { if (!ok) failures += 1; console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); };

// 1. Three confident correct answers retire a cell.
{
  let card = newCard('p10-5');
  const intervals = [];
  for (let i = 0; i < 3; i += 1) {
    card = gradeCard(card, { correct: true, lowConfidence: false, step: i });
    intervals.push(card.interval);
  }
  say(card.stage === 'retired', `tens vs 5 retires after ${SRS_DEFAULTS.retireStreak} confident answers (intervals ${intervals.join(', ')})`);
  say(card.retiredBy === 'mastered', 'retirement is recorded as mastered');
}

// 2. Low confidence keeps a cell in short rotation however often it is right.
{
  let card = newCard('h16-9');
  for (let i = 0; i < 8; i += 1) card = gradeCard(card, { correct: true, lowConfidence: true, step: i });
  say(card.stage === 'learning', 'eight low-confidence answers leave the cell in learning');
  say(card.interval <= 12, `low-confidence interval stays short (${card.interval})`);
  say(card.shaky === 8, 'low-confidence answers are counted');
}

// 3. A wrong answer resets progress.
{
  let card = newCard('h12-4');
  card = gradeCard(card, { correct: true, lowConfidence: false, step: 0 });
  card = gradeCard(card, { correct: true, lowConfidence: false, step: 1 });
  const beforeEase = card.ease;
  card = gradeCard(card, { correct: false, lowConfidence: false, step: 2 });
  say(card.hcStreak === 0 && card.interval === 1 && card.stage === 'learning', 'a miss sends the cell back to the front of the queue');
  say(card.ease < beforeEase, 'a miss lowers ease');
}

// 4. Manual retire and revive.
{
  const retired = retireCard(newCard('p10-6'), 5);
  say(retired.stage === 'retired' && retired.retiredBy === 'manual', 'a hand can be retired by hand');
  const revived = reviveCard(retired, 10);
  say(revived.stage === 'review' && revived.due === 16, 'a retired hand can be brought back');
}

// 5. A full drill: confident on everything except hard 15 and 16, which stay shaky.
{
  const ids = buildScenarioIds();
  const cards = new Map(ids.map((id) => [id, newCard(id)]));
  const shown = new Map(ids.map((id) => [id, 0]));
  const shakyIds = new Set(ids.filter((id) => {
    const s = parseScenario(id);
    return s.kind === 'h' && (s.key === 15 || s.key === 16);
  }));

  let step = 0;
  let lastId = null;
  let sinceNew = 0;
  const seedRng = (() => { let x = 12345; return () => { x = (x * 1103515245 + 12345) % 2147483648; return x / 2147483648; }; })();
  for (let i = 0; i < 4000; i += 1) {
    const pick = pickNext([...cards.values()], { step, lastId, sinceNew, rng: seedRng });
    if (!pick) break;
    sinceNew = pick.stage === 'new' ? 0 : sinceNew + 1;
    shown.set(pick.id, shown.get(pick.id) + 1);
    const low = shakyIds.has(pick.id);
    cards.set(pick.id, gradeCard(pick, { correct: true, lowConfidence: low, step }));
    lastId = pick.id;
    step += 1;
  }

  const counts = cardCounts([...cards.values()]);
  const shakySeen = [...shakyIds].reduce((a, id) => a + shown.get(id), 0) / shakyIds.size;
  const easySeen = ids.filter((id) => !shakyIds.has(id)).reduce((a, id) => a + shown.get(id), 0) / (ids.length - shakyIds.size);
  const shakyRetired = [...shakyIds].filter((id) => cards.get(id).stage === 'retired').length;

  console.log(`      after 4000 hands: ${counts.retired} retired, ${counts.learning} learning, ${counts.new} untouched`);
  console.log(`      shaky cells seen ${shakySeen.toFixed(1)}x each, settled cells ${easySeen.toFixed(1)}x each`);
  say(shakySeen > easySeen * 3, 'shaky hands come back far more often than settled ones');
  say(shakyRetired === 0, 'no low-confidence hand is ever retired');
  say(counts.retired === ids.length - shakyIds.size, `every confidently answered hand retires (${counts.retired} of ${ids.length - shakyIds.size})`);
  say(counts.new === 0, 'the whole deck gets introduced');
}

console.log(failures === 0 ? '\nAll scheduler checks passed.' : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
