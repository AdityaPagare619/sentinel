/* contract.test.mjs — the generated typed boundary matches the frozen contract.
 * Run: node --test tests/contract.test.mjs  (from platform/ui/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import {
  CONTRACT_VERSION, CONTRACT_SOURCE_SHA, ENUMS,
  DECISION_SUMMARY_REQUIRED, DECISION_DETAIL_REQUIRED,
  RIVER_FIELDS, FILTERABLE_FIELDS, isQueryableField, CONFIDENCE_DP,
  validateDecisionSummary, validateDecisionDetail,
} from '../assets/contract.js';

const ROOT = new URL('..', import.meta.url);
const openapiRaw = readFileSync(new URL('../contracts/openapi.yaml', ROOT));
const decisions = JSON.parse(readFileSync(new URL('../data/decisions.json', import.meta.url))).data;
const detail = JSON.parse(readFileSync(new URL('../data/decision-detail.json', import.meta.url))).data;

test('generated file tracks the frozen contract', () => {
  const sha = createHash('sha256').update(openapiRaw).digest('hex');
  assert.equal(CONTRACT_SOURCE_SHA, sha, 'contract.js is stale — regenerate with tools/gen_contract.py');
  assert.equal(CONTRACT_VERSION, '1.0.0');
});

test('enums mirror the contract', () => {
  assert.deepEqual(ENUMS.Severity, ['p1_critical', 'p2_high', 'p3_medium', 'p4_low', 'known_noise', 'cannot_determine']);
  assert.deepEqual(ENUMS.DispositionAction, ['page_now', 'page_business_hours', 'suppress', 'passthrough']);
  assert.deepEqual(ENUMS.Team, ['platform', 'network', 'data', 'product_backend', 'security', 'cannot_determine']);
  assert.deepEqual(ENUMS.DataSource, ['synthetic', 'shadow', 'production']);
});

test('the closed vocabularies are sane', () => {
  for (const k of DECISION_SUMMARY_REQUIRED) assert.ok(RIVER_FIELDS.includes(k), k);
  for (const f of FILTERABLE_FIELDS) {
    assert.ok(RIVER_FIELDS.includes(f), f);
    assert.ok(isQueryableField(f), f);
  }
  assert.equal(CONFIDENCE_DP, 2, '§4.2 — confidence renders at engine quantization');
});

test('display-only invariant: pin paths are never queryable', () => {
  for (const p of ['labels.cluster', 'alert.labels.region', 'custom_details.az', 'runbook_url', 'payload.status']) {
    assert.equal(isQueryableField(p), false, p);
  }
});

test('every mock decision row validates', () => {
  assert.ok(decisions.length > 0);
  for (const d of decisions) {
    const v = validateDecisionSummary(d);
    assert.ok(v.ok, `row ${d.id}: ${v.errors.join('; ')}`);
  }
  const dv = validateDecisionDetail(detail);
  assert.ok(dv.ok, dv.errors.join('; '));
});

test('validation rejects drift honestly', () => {
  const good = decisions[0];
  assert.equal(validateDecisionSummary({ ...good, severity: 'ultra' }).ok, false);
  assert.equal(validateDecisionSummary({ ...good, confidence: 1.5 }).ok, false);
  const { id, ...noId } = good;
  assert.equal(validateDecisionSummary(noId).ok, false);
  assert.equal(validateDecisionSummary(null).ok, false);
  assert.equal(validateDecisionSummary('nope').ok, false);
  /* liberal in what we accept: unknown extra keys are tolerated */
  assert.equal(validateDecisionSummary({ ...good, vendor_future_field: 1 }).ok, true);
  /* detail requires alert + audit */
  const { alert, ...noAlert } = detail;
  assert.equal(validateDecisionDetail(noAlert).ok, false);
});
