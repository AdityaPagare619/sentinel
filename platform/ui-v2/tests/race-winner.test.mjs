/* race-winner.test.mjs — the console's judge-vs-timer winner must derive from
 * the engine's authoritative budget_outcome, never from jev_model presence
 * (audit P1/C2, RFC aiml-winner-heuristic).
 * Run: node --test tests/race-winner.test.mjs (from platform/ui-v2/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf-8');
// Extract the raceWinner function defined in the inline console script.
const m = html.match(/function raceWinner\(d\)\{[\s\S]*?\n\}/);
assert.ok(m, 'raceWinner not found in index.html');
const raceWinner = new Function(`${m[0]}; return raceWinner;`)();

test('answered_in_time -> judge', () => {
  assert.equal(raceWinner({ budget_outcome: 'answered_in_time', jev_model: 'jev-1.13.0' }), 'judge');
});

test('timer_won / timer_won_shed -> timer', () => {
  assert.equal(raceWinner({ budget_outcome: 'timer_won', jev_model: null }), 'timer');
  assert.equal(raceWinner({ budget_outcome: 'timer_won_shed', jev_model: null }), 'timer');
});

test('error_passthrough with jev_model set -> error (never judge)', () => {
  // THE regression: the old heuristic (d.jev_model?'judge':'timer') labeled
  // this a judge win. The judge errored; the fail-open path owned the page.
  assert.equal(raceWinner({ budget_outcome: 'error_passthrough', jev_model: 'jev-1.13.0' }), 'error');
});

test('structural_passthrough -> timer (no judge answer exists)', () => {
  assert.equal(raceWinner({ budget_outcome: 'structural_passthrough', jev_model: null }), 'timer');
});

test('legacy rows without budget_outcome fall back to the old heuristic', () => {
  assert.equal(raceWinner({ jev_model: 'jev-1.13.0' }), 'judge');
  assert.equal(raceWinner({ jev_model: null }), 'timer');
});
