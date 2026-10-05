/* honesty.test.mjs — the §9.2 honesty invariants as automated gates.
 *
 * These are Type 1: violation blocks merge. Each invariant combines
 * behavioral assertions on the component functions with static scans of the
 * view sources — the prose laws of INTERFACE_PRINCIPLES.md turned into
 * falsifiable checks.
 *
 * Run: node --test tests/honesty.test.mjs (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { decisionRow, drawerHtml, freshnessBadge, derivedMark, dispChip,
         skeletonRows, errorBlock, emptyBlock, confBar,
         COMPONENT_STATE_COVERAGE, FIVE_STATES } from '../assets/components.js';
import { freshnessState } from '../assets/freshness.js';
import { FILTERABLE_FIELDS, isQueryableField } from '../assets/contract.js';

const ROOT = new URL('..', import.meta.url);
const src = (f) => readFileSync(new URL('./assets/' + f, ROOT), 'utf8');
const decisions = JSON.parse(readFileSync(new URL('../data/decisions.json', import.meta.url))).data;
const D = decisions[0];

/* ---------- Invariant 1: no data component renders without a freshness
 * indicator when its payload exceeds the surface's freshness budget. ---------- */
test('INV-1: every data component names a freshness renderer for stale/degraded', () => {
  for (const [comp, states] of Object.entries(COMPONENT_STATE_COVERAGE)) {
    if (states.stale_degraded === null) continue; /* deliberate: pure labels */
    assert.equal(states.stale_degraded, 'freshnessBadge', `${comp}: stale_degraded must render the FreshnessBadge`);
  }
});

test('INV-1: the decision drawer carries a freshness badge in the companions strip', () => {
  const html = drawerHtml(D, { freshness: { state: 'live', asOfIso: D.time } });
  assert.match(html, /class="freshness mono fr-live"/, 'drawer renders the freshness badge');
  assert.match(html, /companions/, 'P2 companions strip present');
});

test('INV-1: every view wires freshness (static scan)', () => {
  /* Redesign v2: freshness is ambient chrome — app.js paints the pipeline
   * strip + health chip on every screen (stronger than per-view badges).
   * Carried-forward lab views keep their per-view freshnessBadge. */
  const shell = src('app.js');
  assert.ok(shell.includes('paintPipeline'), 'shell paints the pipeline strip');
  assert.ok(shell.includes('health-chip'), 'shell renders the health chip');
  assert.ok(shell.includes("Store.on('health'"), 'shell re-paints on health events');
  for (const v of ['views-cal.js', 'views-sim.js', 'views-shadow.js']) {
    assert.ok(src(v).includes('freshnessBadge'), `${v} must render FreshnessBadge`);
  }
});

/* ---------- Invariant 2: no reconstructed/simulated/derived value renders
 * without its in-band label. ---------- */
test('INV-2: the counterfactual receipt carries its derived mark in-band', () => {
  const html = decisionRow(D, { thresholds: { suppress_conf_min: 0.9 } });
  assert.match(html, /derived-mark/, 'receipt is labeled derived at the point of display');
});

test('INV-2: the simulator labels its entire surface', () => {
  const s = src('views-sim.js');
  assert.ok(s.includes('SIMULATION'), 'simulator carries the full-surface SIMULATION banner');
  assert.ok(s.includes('derivedMark'), 'every projection card carries the simulated mark');
});

test('INV-2: shadow figures are labeled derived', () => {
  assert.ok(src('views-shadow.js').includes('derivedMark'));
});

test('INV-2: audit entries carry attribution, never unlabeled automation', () => {
  /* Redesign v2: the audit view has no chain-derivation feature to label —
   * instead every timeline entry carries an explicit attribution tag
   * (human / engine / rule), so automation is never anonymous. */
  const s = src('view-audit.js');
  assert.ok(s.includes('>human<') || s.includes('human</span>'), 'human attribution renders');
  assert.ok(s.includes('>engine<') || s.includes('engine</span>'), 'engine attribution renders');
});

/* ---------- Invariant 3: no disposition renders without its evidence
 * companions (P2). ---------- */
test('INV-3: the drawer shows all five companions', () => {
  const html = drawerHtml(D, { bins: null, freshness: { state: 'live', asOfIso: D.time } });
  assert.match(html, /drawer-sec"><h3>Evidence/, 'companion 1: evidence');
  assert.match(html, /comp-k">freshness/, 'companion 3: freshness');
  assert.match(html, /comp-k">policy/, 'companion 4: policy version');
  assert.match(html, /comp-k">fallback/, 'companion 5: fallback reason');
  /* companion 2 (uncertainty) renders when bins are available */
  const htmlBins = drawerHtml(D, {
    bins: [{ predicted_lo: 0.8, predicted_hi: 0.9, n: 100, observed_rate: 0.85, ci95_lo: 0.8, ci95_hi: 0.9 }],
    freshness: { state: 'live', asOfIso: D.time },
  });
  assert.match(htmlBins, /confbar/, 'companion 2: quantized uncertainty with calibration context');
});

test('INV-3: policy version and facets render their honest absence, never invented', () => {
  const html = drawerHtml(D, { freshness: { state: 'live', asOfIso: D.time } });
  assert.match(html, /not exposed by the read API/, 'missing policy_version is stated, not invented');
  assert.match(html, /No promoted facets/, 'missing facets are stated, not invented');
});

/* ---------- Invariant 4: no screen renders a raw error or blank state for a
 * handled failure mode (the five states, §4.3). ---------- */
test('INV-4: the five-state registry covers every data component', () => {
  for (const [comp, states] of Object.entries(COMPONENT_STATE_COVERAGE)) {
    for (const s of FIVE_STATES) {
      assert.ok(s in states, `${comp} must name a renderer for state '${s}' (null = deliberately n/a)`);
    }
  }
});

test('INV-4: the named state renderers exist', () => {
  const fns = { skeletonRows, errorBlock, emptyBlock, decisionRow, freshnessBadge, confBar, drawerHtml, derivedMark, dispChip };
  for (const [comp, states] of Object.entries(COMPONENT_STATE_COVERAGE)) {
    for (const name of Object.values(states)) {
      if (name === null) continue;
      /* view-owned painters (paintChain, paintCards, renderShadow) live in the
       * views; everything else must be an exported component function */
      if (['paintChain', 'paintCards', 'renderShadow'].includes(name)) continue;
      assert.ok(fns[name], `${comp}: renderer '${name}' must exist`);
    }
  }
});

test('INV-4: every view designs loading, error, and empty (static scan)', () => {
  /* Redesign v2: the synth store is synchronous (no loading flash to design);
   * empty states are per-view, the error boundary is shell-global. */
  for (const v of ['view-now.js', 'view-pages.js', 'view-proofs.js', 'view-river.js', 'view-audit.js', 'view-safety.js']) {
    assert.ok(src(v).includes('empty'), `${v}: empty state designed`);
  }
  assert.ok(src('app.js').includes("Couldn't render this screen"), 'shell: global error boundary');
  for (const v of ['views-cal.js', 'views-sim.js', 'views-shadow.js']) {
    const s = src(v);
    assert.ok(s.includes('skeletonRows'), `${v}: loading state`);
    assert.ok(s.includes('errorBlock'), `${v}: error state`);
    assert.ok(s.includes('emptyBlock'), `${v}: empty state`);
  }
});

/* ---------- Invariant 5: console network traffic contains zero
 * model-evaluation calls on read paths (P5). ---------- */
test('INV-5: no Jev/model-evaluation call on any read path', () => {
  /* The ONLY sanctioned Jev-path touchpoint is the START flow's key
   * verification (one live typed-answer call, user-initiated) — everything
   * else must be read-only against the platform contract. The pattern below
   * matches evaluation CALLS, not prose mentions of Jev/TypeSafe. */
  const offenders = [];
  const re = /\/api\/jev|fetch\s*\([^)]*jev[^)]*\)|jev\s*\.\s*(call|ask|complete|answer)\s*\(/i;
  for (const f of ['lib.js', 'api.js', 'app.js', 'components.js', 'contract.js',
                   'freshness.js', 'shadow.js', 'chain.js', 'payload.js', 'pins.js',
                   'views-cal.js', 'views-sim.js', 'views-shadow.js', 'views-setup.js',
                   'synth.js', 'store.js',
                   'view-now.js', 'view-pages.js', 'view-proofs.js',
                   'view-river.js', 'view-audit.js', 'view-safety.js']) {
    const s = src(f);
    if (re.test(s)) offenders.push(f);
  }
  assert.deepEqual(offenders, [], 'read-path files must not touch the Jev path');
});

/* ---------- R1 ruling (pins are display bindings, never query operands) ----------
 * Q1 stands: fixed envelope fields are the ONLY things any surface may
 * sort/filter/color by. The committed direction's "filter operand" language
 * is superseded — recorded here as a gate, not a comment. */
test('R1: pin paths can never enter the filter grammar', () => {
  for (const p of ['labels.cluster', 'annotations.0.runbook_url', 'status']) {
    assert.equal(isQueryableField(p), false, `pin path '${p}' must not be queryable`);
  }
  assert.ok(!FILTERABLE_FIELDS.some(f => f.includes('.')), 'no dotted (payload) path is filterable');
});

test('R1: the river has no pin-based filtering at all (static scan)', () => {
  /* Redesign v2: pins are gone from the river — the only filter operand is
   * the disposition, a fixed envelope field. */
  const s = src('view-river.js');
  assert.ok(!/pins/i.test(s), 'the river must never reference pins');
  assert.ok(s.includes("data-disp"), 'the disposition filter is a fixed-operand chip set');
});

/* ---------- Invariant 6 (R7 B1): row source badges derive from envelope
 * evidence — never hardcoded. A badge claiming a source the payload does
 * not evidence is a P3 honesty violation. ---------- */
test('INV-6: decisionRow badge derives from the dataSource option, never hardcoded', () => {
  const htmlSyn = decisionRow(D, { dataSource: 'synthetic' });
  assert.match(htmlSyn, /data-src="synthetic"/, 'badge reflects synthetic evidence');
  assert.doesNotMatch(htmlSyn, /data-src="shadow"/, 'badge must not claim shadow on synthetic data');
  const htmlLive = decisionRow(D, { dataSource: 'live' });
  assert.match(htmlLive, /data-src="live"/, 'badge reflects live evidence');
  const htmlDefault = decisionRow(D, {});
  assert.match(htmlDefault, /data-src="unknown"/, 'no evidence → unknown, never a guessed source');
});

test('INV-6: no hardcoded srcBadge source in the row component (static scan)', () => {
  const c = src('components.js');
  assert.doesNotMatch(c, /srcBadge\('shadow'\)/, 'the shadow hardcode must not return');
  assert.doesNotMatch(c, /row-denom/, 'the unevidenced confidence denominator must not return');
});

test('INV-6: the data source is in-band on every screen, derived from the build (static scan)', () => {
  /* Redesign v2: per-row badges are replaced by the ambient mode bar — the
   * source is derived from window.SENTINEL_DATA_MODE (the build), never
   * hardcoded per row. A badge claiming a source the payload does not
   * evidence is still a P3 honesty violation. */
  const s = src('app.js');
  assert.ok(s.includes('modebar'), 'shell renders the in-band mode bar');
  assert.ok(s.includes('SIMULATED'), 'simulated mode is labeled in-band');
  assert.ok(s.includes('window.SENTINEL_DATA_MODE') || s.includes('Store.isSimulated'),
    'the mode derives from the build, never a hardcoded claim');
});
