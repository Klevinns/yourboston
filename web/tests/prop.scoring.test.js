/**
 * Property-based tests for scoring.js using fast-check + Node test runner.
 *
 * Properties 1–7, 12, 14 from design.md.
 * ≥ 200 runs each (fast-check default numRuns).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import * as fc from 'fast-check';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  CRITERIA,
  rocWeights,
  percentileTables,
  subscore,
  activeCriteria,
  criterionValue,
  evaluate,
  scoreAll,
} from '../js/scoring.js';

// ── Constants ───────────────────────────────────────────────────────────────

const FC_RUNS = 200; // ≥ 200 per design.md
const NUM_TRACTS_MIN = 2;
const NUM_TRACTS_MAX = 10; // Reduced for test performance

// ── Arbitraries ─────────────────────────────────────────────────────────────

/** Generate a nullable number for criterion values (null or 0–500). */
const arbNullablePos = fc.option(fc.float({ min: 0, max: 500, noNaN: true }), { nil: null });

/** Generate base64 Uint8 commute vector of exactly n bytes. */
function arbCommVec(n) {
  return fc.uint8Array({ minLength: n, maxLength: n }).map(arr =>
    Buffer.from(arr).toString('base64')
  );
}

/** Generate a commute vector as Uint8Array directly. */
function arbCommVecUint8(n) {
  return fc.uint8Array({ minLength: n, maxLength: n });
}

/** Generate a random permutation of CRITERIA. */
function arbRanks() {
  return fc.shuffledSubarray(CRITERIA, { minLength: CRITERIA.length, maxLength: CRITERIA.length });
}

/** Generate random modes object with at least one mode true. */
function arbModes() {
  return fc.record({
    walk: fc.boolean(),
    bike: fc.boolean(),
    car: fc.boolean(),
    transit: fc.boolean(),
  }).filter(m => m.walk || m.bike || m.car || m.transit);
}

/** Generate mustHave with random booleans. */
function arbMustHave() {
  return fc.record(Object.fromEntries(CRITERIA.map(k => [k, fc.boolean()])));
}

/** Generate threshold with null or a number in realistic range. */
function arbThreshold(features) {
  return fc.record(Object.fromEntries(CRITERIA.map(k => [
    k,
    fc.oneof(
      fc.constant(null),
      fc.float({ min: 0, max: 500, noNaN: true })
    )
  ])));
}

/** Generate a valid scoring state for n tracts. */
function arbState(n) {
  return fc.record({
    mode: fc.constantFrom('rent', 'buy'),
    ranks: arbRanks(),
    mustHave: arbMustHave(),
    threshold: arbThreshold(),
    workIdx: fc.oneof(fc.constant(null), fc.integer({ min: 0, max: Math.max(0, n - 1) })),
    modes: arbModes(),
    crimeScope: fc.constantFrom('serious', 'all'),
  });
}

/** Generate a feature with valid properties for n tracts. */
function arbFeature(idx, n) {
  return fc.record({
    rent:           arbNullablePos,
    value:          arbNullablePos,
    burden:         fc.option(fc.float({ min: 0, max: 100, noNaN: true }), { nil: null }),
    transit:        arbNullablePos,
    campus:         arbNullablePos,
    crime_serious:  arbNullablePos,
    crime_property: arbNullablePos,
    cm_walk:    arbCommVec(n),
    cm_bike:    arbCommVec(n),
    cm_car:     arbCommVec(n),
    cm_transit: arbCommVec(n),
    geoid:      fc.constant(String(1000000 + idx).padStart(11, '0')),
    idx:        fc.constant(idx),
    name:       fc.constant(`Tract ${idx}`),
    town:       fc.constant('Test Town'),
    value_topcoded: fc.oneof(fc.constant(false), fc.constant(true)),
  }).map(props => ({ type: 'Feature', properties: props, geometry: null }));
}

/** Generate a feature with Uint8Array commute vectors. */
function arbFeatureUint8(idx, n) {
  return fc.record({
    rent:           arbNullablePos,
    value:          arbNullablePos,
    burden:         fc.option(fc.float({ min: 0, max: 100, noNaN: true }), { nil: null }),
    transit:        arbNullablePos,
    campus:         arbNullablePos,
    crime_serious:  arbNullablePos,
    crime_property: arbNullablePos,
    cm_walk:    arbCommVecUint8(n),
    cm_bike:    arbCommVecUint8(n),
    cm_car:     arbCommVecUint8(n),
    cm_transit: arbCommVecUint8(n),
    geoid:      fc.constant(String(1000000 + idx).padStart(11, '0')),
    idx:        fc.constant(idx),
    name:       fc.constant(`Tract ${idx}`),
    town:       fc.constant('Test Town'),
    value_topcoded: fc.oneof(fc.constant(false), fc.constant(true)),
  }).map(props => ({ type: 'Feature', properties: props, geometry: null }));
}

function arbFeatureSet() {
  return fc.integer({ min: NUM_TRACTS_MIN, max: NUM_TRACTS_MAX }).chain(n =>
    fc.tuple(
      fc.array(fc.integer({ min: 0, max: n - 1 }), { minLength: n, maxLength: n })
        .filter(arr => [...new Set(arr)].length === n)
        .map(() => Array.from({ length: n }, (_, i) => i))
        .chain(idxs => fc.tuple(...idxs.map(i => arbFeature(i, n)))),
      arbState(n),
    ).map(([featuresTuple, state]) => ({ features: [...featuresTuple], state, n }))
  );
}

function arbFeatureSetUint8() {
  return fc.integer({ min: NUM_TRACTS_MIN, max: NUM_TRACTS_MAX }).chain(n =>
    fc.tuple(
      fc.array(fc.integer({ min: 0, max: n - 1 }), { minLength: n, maxLength: n })
        .filter(arr => [...new Set(arr)].length === n)
        .map(() => Array.from({ length: n }, (_, i) => i))
        .chain(idxs => fc.tuple(...idxs.map(i => arbFeatureUint8(i, n)))),
      arbState(n),
    ).map(([featuresTuple, state]) => ({ features: [...featuresTuple], state, n }))
  );
}


// ── Property 1: Score bounds ──────────────────────────────────────────────────

describe('Property 1: score bounds', () => {
  it('every scored tract has 0 <= score <= 100', () => {
    fc.assert(
      fc.property(arbFeatureSet(), ({ features, state }) => {
        const results = scoreAll(features, state);
        for (const [, r] of results) {
          if (r.status === 'scored') {
            assert.ok(r.score >= 0 && r.score <= 100,
              `Score out of bounds: ${r.score}`);
          }
        }
      }),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Property 2: Weights form a distribution ───────────────────────────────────

describe('Property 2: weights form a distribution', () => {
  it('rocWeights are positive, sum to 1, strictly decreasing', () => {
    fc.assert(
      fc.property(
        fc.shuffledSubarray(CRITERIA, { minLength: 1, maxLength: CRITERIA.length }),
        (ranked) => {
          const w = rocWeights(ranked);
          const vals = ranked.map(k => w[k]);
          // Each weight > 0
          for (const v of vals) {
            assert.ok(v > 0, `Weight ${v} not positive`);
            assert.ok(v <= 1, `Weight ${v} > 1`);
          }
          // Sum ≈ 1
          const sum = vals.reduce((a, b) => a + b, 0);
          assert.ok(Math.abs(sum - 1) < 1e-9, `Weights sum to ${sum}`);
          // Strictly decreasing
          for (let i = 0; i < vals.length - 1; i++) {
            assert.ok(vals[i] > vals[i + 1],
              `Weight[${i}]=${vals[i]} not > weight[${i+1}]=${vals[i+1]}`);
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });

  it('example: rocWeights([a,b,c]) ≈ {a:0.6111, b:0.2778, c:0.1111}', () => {
    const w = rocWeights(['a', 'b', 'c']);
    assert.ok(Math.abs(w.a - 0.6111111111) < 1e-6);
    assert.ok(Math.abs(w.b - 0.2777777778) < 1e-6);
    assert.ok(Math.abs(w.c - 0.1111111111) < 1e-6);
  });
});


// ── Property 3: Determinism ───────────────────────────────────────────────────

describe('Property 3: determinism', () => {
  it('scoreAll returns identical results on two calls', () => {
    fc.assert(
      fc.property(arbFeatureSet(), ({ features, state }) => {
        const r1 = scoreAll(features, state);
        const r2 = scoreAll(features, state);
        for (const [geoid, res1] of r1) {
          const res2 = r2.get(geoid);
          assert.equal(res1.status, res2.status);
          if (res1.score !== null && res2.score !== null) {
            assert.ok(Math.abs(res1.score - res2.score) < 1e-10,
              `Scores differ: ${res1.score} vs ${res2.score}`);
          } else {
            assert.equal(res1.score, res2.score);
          }
        }
      }),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Property 4: Order independence ───────────────────────────────────────────

describe('Property 4: order independence', () => {
  it('shuffling feature order does not change results', () => {
    fc.assert(
      fc.property(
        arbFeatureSet(),
        fc.integer({ min: 0, max: 100 }),
        ({ features, state }, seed) => {
          // Deterministic shuffle using seed
          const shuffled = [...features];
          const rng = (s) => { s = ((s * 1664525 + 1013904223) >>> 0); return s; };
          let s = seed;
          for (let i = shuffled.length - 1; i > 0; i--) {
            s = rng(s);
            const j = s % (i + 1);
            [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
          }

          const r1 = scoreAll(features, state);
          const r2 = scoreAll(shuffled, state);
          for (const [geoid, res1] of r1) {
            const res2 = r2.get(geoid);
            assert.equal(res1.status, res2.status,
              `Status mismatch for ${geoid}: ${res1.status} vs ${res2.status}`);
            if (res1.score !== null && res2.score !== null) {
              assert.ok(Math.abs(res1.score - res2.score) < 1e-9);
            } else {
              assert.equal(res1.score, res2.score);
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });

  it('shuffling mustHave/threshold key insertion order does not change results', () => {
    fc.assert(
      fc.property(
        arbFeatureSet(),
        fc.integer({ min: 0, max: 100 }),
        ({ features, state }, seed) => {
          // Create a new state with permuted keys
          const shuffledMustHave = {};
          const shuffledThreshold = {};
          const keys = [...CRITERIA];
          // Deterministic shuffle
          const rng = (s) => { s = ((s * 1664525 + 1013904223) >>> 0); return s; };
          let s = seed;
          for (let i = keys.length - 1; i > 0; i--) {
            s = rng(s);
            const j = s % (i + 1);
            [keys[i], keys[j]] = [keys[j], keys[i]];
          }
          for (const k of keys) {
            shuffledMustHave[k] = state.mustHave[k];
            shuffledThreshold[k] = state.threshold[k];
          }
          const permutedState = { ...state, mustHave: shuffledMustHave, threshold: shuffledThreshold };

          const r1 = scoreAll(features, state);
          const r2 = scoreAll(features, permutedState);
          for (const [geoid, res1] of r1) {
            const res2 = r2.get(geoid);
            assert.equal(res1.status, res2.status,
              `Status mismatch for ${geoid}: ${res1.status} vs ${res2.status}`);
            if (res1.score !== null && res2.score !== null) {
              assert.ok(Math.abs(res1.score - res2.score) < 1e-9);
            } else {
              assert.equal(res1.score, res2.score);
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Property 5: Monotonicity ──────────────────────────────────────────────────

describe('Property 5: monotonicity', () => {
  it('improving a criterion value never lowers a tract score (with original state)', () => {
    fc.assert(
      fc.property(
        arbFeatureSet(),
        ({ features, state }) => {
          // Need at least one tract to test
          if (features.length < 1) return;

          // Original scoreAll results with original state (KEEP must-haves/thresholds)
          const originalResults = scoreAll(features, state);

          // Find a tract that was scored
          let targetTract = null;
          let targetIdx = -1;
          for (let i = 0; i < features.length; i++) {
            const r = originalResults.get(features[i].properties.geoid);
            if (r && r.status === 'scored') {
              targetTract = features[i];
              targetIdx = i;
              break;
            }
          }
          if (!targetTract) return; // No scored tract to test

          const p = targetTract.properties;
          const testKeys = ['rent', 'value', 'burden', 'transit'];

          for (const key of testKeys) {
            // Skip inactive criteria
            if (key === 'value' && state.mode === 'rent') continue;
            if (key === 'rent' && state.mode === 'buy') continue;

            const v = p[key];
            if (v === null || v === undefined || v <= 0) continue;

            // Create modified features with this tract's value improved
            const improvedProps = { ...p, [key]: v * 0.5 };
            const modifiedFeatures = features.map((f, i) =>
              i === targetIdx ? { ...f, properties: improvedProps } : f
            );

            const modifiedResults = scoreAll(modifiedFeatures, state);

            const r1 = originalResults.get(p.geoid);
            const r2 = modifiedResults.get(p.geoid);

            // Original must have been scored
            assert.equal(r1.status, 'scored', `Original should be scored for ${key}`);
            // After improvement should still be scored (not excluded)
            assert.equal(r2.status, 'scored', `Modified should stay scored for ${key}, got ${r2.status}`);
            // Score should not decrease
            assert.ok(r2.score >= r1.score - 1e-9,
              `Improving ${key} lowered score from ${r1.score} to ${r2.score}`);
          }

          // Test crime improvement
          const crimeSerious = p.crime_serious;
          const crimeProperty = p.crime_property;
          const scopeIsAll = (state.crimeScope || 'serious') === 'all';

          if (crimeSerious !== null && crimeSerious !== undefined && crimeSerious > 0) {
            const crimeProps = { ...p, crime_serious: crimeSerious * 0.5 };
            if (scopeIsAll && crimeProperty !== null && crimeProperty !== undefined && crimeProperty > 0) {
              crimeProps.crime_property = crimeProperty * 0.5;
            }
            const modifiedFeatures = features.map((f, i) =>
              i === targetIdx ? { ...f, properties: crimeProps } : f
            );
            const modifiedResults = scoreAll(modifiedFeatures, state);

            const r1 = originalResults.get(p.geoid);
            const r2 = modifiedResults.get(p.geoid);

            assert.equal(r1.status, 'scored', 'Original should be scored for crime');
            assert.equal(r2.status, 'scored', `Modified should stay scored for crime`);
            assert.ok(r2.score >= r1.score - 1e-9, 'Improving crime lowered score');
          }

          // Test commute improvement
          if (state.workIdx !== null && state.workIdx !== undefined) {
            const idx = state.workIdx;
            const modeKeys = ['walk', 'bike', 'car', 'transit'];

            for (const modeKey of modeKeys) {
              if (!state.modes[modeKey]) continue;
              const vecKey = `cm_${modeKey}`;
              const vec = p[vecKey];
              if (!vec) continue;

              let bytes;
              if (typeof vec === 'string') {
                bytes = Buffer.from(vec, 'base64');
              } else if (vec instanceof Uint8Array) {
                bytes = vec;
              } else continue;

              const origByte = bytes[idx];
              if (origByte === null || origByte === undefined || origByte <= 0) continue;

              // Improve: reduce byte by 1 (min 0)
              const improvedBytes = Buffer.from(bytes);
              improvedBytes[idx] = Math.max(0, origByte - 1);

              const commuteProps = { ...p, [vecKey]: improvedBytes.toString('base64') };
              const modifiedFeatures = features.map((f, i) =>
                i === targetIdx ? { ...f, properties: commuteProps } : f
              );

              const modifiedResults = scoreAll(modifiedFeatures, state);
              const r1 = originalResults.get(p.geoid);
              const r2 = modifiedResults.get(p.geoid);

              assert.equal(r1.status, 'scored', `Original should be scored for ${modeKey} commute`);
              assert.equal(r2.status, 'scored', `Modified should stay scored for ${modeKey} commute`);
              assert.ok(r2.score >= r1.score - 1e-9, `Improving ${modeKey} commute lowered score`);
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Property 6: Must-have exclusion ──────────────────────────────────────────

describe('Property 6: must-have exclusion', () => {
  it('tracts exceeding a must-have threshold are excluded, even if another must-have is null', () => {
    fc.assert(
      fc.property(
        arbFeatureSet(),
        fc.constantFrom(...CRITERIA.filter(k => k !== 'commute')),
        fc.float({ min: 0, max: 500, noNaN: true }),
        ({ features, state }, key, threshold) => {
          if (key === 'value' && state.mode === 'rent') return;
          if (key === 'rent' && state.mode === 'buy') return;

          const forcedState = {
            ...state,
            mustHave: { ...state.mustHave, [key]: true },
            threshold: { ...state.threshold, [key]: threshold },
          };

          const results = scoreAll(features, forcedState);
          for (const f of features) {
            const p = f.properties;
            // Get the value that would be checked
            let v;
            if (key === 'crime') {
              v = criterionValue(p, key, forcedState);
            } else {
              v = p[key];
            }
            if (v === null || v === undefined) continue;
            const r = results.get(p.geoid);
            if (Number(v) > threshold) {
              assert.equal(r.status, 'excluded',
                `Tract with ${key}=${v} > threshold=${threshold} should be excluded`);
              assert.ok(r.failed.includes(key),
                `failed should include ${key}`);
              assert.equal(r.score, null);
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });

  it('multiple simultaneous must-haves: excluded if ANY exceeds threshold, even if another is null', () => {
    fc.assert(
      fc.property(
        arbFeatureSet(),
        fc.array(fc.constantFrom(...CRITERIA.filter(k => k !== 'commute')), { minLength: 1, maxLength: 3 }),
        fc.float({ min: 0, max: 500, noNaN: true }),
        ({ features, state }, keys, threshold) => {
          // Set must-have for all selected keys to same threshold
          const mustHave = { ...state.mustHave };
          const thresholdObj = { ...state.threshold };
          for (const k of keys) {
            if (k === 'value' && state.mode === 'rent') continue;
            if (k === 'rent' && state.mode === 'buy') continue;
            mustHave[k] = true;
            thresholdObj[k] = threshold;
          }

          const forcedState = { ...state, mustHave, threshold: thresholdObj };
          const results = scoreAll(features, forcedState);

          for (const f of features) {
            const p = f.properties;
            const r = results.get(p.geoid);
            // Find any key that exceeds threshold
            let anyExceeds = false;
            for (const k of keys) {
              if (k === 'value' && state.mode === 'rent') continue;
              if (k === 'rent' && state.mode === 'buy') continue;
              const v = criterionValue(p, k, forcedState);
              if (v !== null && v > threshold) anyExceeds = true;
            }
            if (anyExceeds) {
              assert.equal(r.status, 'excluded', `Should be excluded when any must-have exceeds`);
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Property 7: Rank sensitivity ─────────────────────────────────────────────

describe('Property 7: rank sensitivity', () => {
  it('swapping any pair where higher-ranked has lower subscore maintains or improves score', () => {
    // When we swap any pair where the lower-ranked criterion (at position j, lower importance)
    // has a higher subscore than the higher-ranked criterion (at position i), moving it up
    // should not decrease the score (more weight to the better criterion).
    fc.assert(
      fc.property(
        arbFeatureSet(),
        ({ features, state }) => {
          if (features.length < 2) return;

          // Ensure commute is active
          const stateWithWork = state.workIdx !== null ? state : { ...state, workIdx: Math.floor(features.length / 2) };

          const noMust = {
            ...stateWithWork,
            mustHave: Object.fromEntries(CRITERIA.map(k => [k, false])),
          };
          const active = activeCriteria(noMust);
          if (active.length < 2) return;

          const tables = percentileTables(features, active, noMust);

          for (const f of features) {
            const r1 = evaluate(f.properties, noMust, tables);
            if (r1.status !== 'scored') continue;

            const partsByKey = Object.fromEntries(r1.parts.map(p => [p.key, p]));

            // Test ALL pairs (i < j): a at higher rank position i, b at lower rank position j
            for (let i = 0; i < active.length; i++) {
              for (let j = i + 1; j < active.length; j++) {
                const a = active[i];     // higher position (more important)
                const b = active[j];     // lower position (less important)
                const pa = partsByKey[a];
                const pb = partsByKey[b];
                if (!pa || !pb || pa.subscore === null || pb.subscore === null) continue;

                // Skip if the higher-ranked criterion already has higher or equal subscore
                if (pa.subscore >= pb.subscore) continue;

                // Swap: move b up (to position i), move a down (to position j)
                const newRanks = [...noMust.ranks];
                const posA = newRanks.indexOf(a);
                const posB = newRanks.indexOf(b);
                if (posA < 0 || posB < 0) continue;
                [newRanks[posA], newRanks[posB]] = [newRanks[posB], newRanks[posA]];
                const newState = { ...noMust, ranks: newRanks };
                const newTables = percentileTables(features, active, newState);
                const r2 = evaluate(f.properties, newState, newTables);
                if (r2.status !== 'scored') continue;

                // Score should NOT decrease
                assert.ok(r2.score >= r1.score - 1e-9,
                  `Swap (${a},${b}) lowered score: ${r1.score} → ${r2.score}`);
              }
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Req 7.9: 255 worst commute is scored (not null) ──────────────────────────

describe('Req 7.9: worst commute (255) is scored, not missing', () => {
  it('a tract whose only commute value is 255 gets a commute subscore, not missing', () => {
    // Create a fixed scenario: features with some valid commute values plus one with all 255
    const n = 5;
    const baseFeatures = Array.from({ length: n - 1 }, (_, i) => ({
      type: 'Feature',
      properties: {
        rent: 1000,
        value: 300000,
        burden: 30,
        transit: 10,
        campus: 2,
        crime_serious: 5,
        crime_property: 10,
        cm_walk: Buffer.from(new Uint8Array([10, 20, 30, 40])).toString('base64'),
        cm_bike: Buffer.from(new Uint8Array([5, 15, 25, 35])).toString('base64'),
        cm_car: Buffer.from(new Uint8Array([3, 8, 13, 18])).toString('base64'),
        cm_transit: Buffer.from(new Uint8Array([15, 25, 35, 45])).toString('base64'),
        geoid: String(1000000 + i).padStart(11, '0'),
        idx: i,
        name: `Tract ${i}`,
        town: 'Test Town',
        value_topcoded: false,
      },
      geometry: null,
    }));

    // Create feature with all 255s at workIdx
    const worstFeature = {
      type: 'Feature',
      properties: {
        rent: 1000,
        value: 300000,
        burden: 30,
        transit: 10,
        campus: 2,
        crime_serious: 5,
        crime_property: 10,
        cm_walk: Buffer.from(new Uint8Array([255, 255, 255, 255])).toString('base64'),
        cm_bike: Buffer.from(new Uint8Array([255, 255, 255, 255])).toString('base64'),
        cm_car: Buffer.from(new Uint8Array([255, 255, 255, 255])).toString('base64'),
        cm_transit: Buffer.from(new Uint8Array([255, 255, 255, 255])).toString('base64'),
        geoid: '99999999999',
        idx: n - 1,
        name: 'Worst Tract',
        town: 'Test Town',
        value_topcoded: false,
      },
      geometry: null,
    };

    const state = {
      mode: 'rent',
      ranks: [...CRITERIA],
      mustHave: Object.fromEntries(CRITERIA.map(k => [k, false])),
      threshold: Object.fromEntries(CRITERIA.map(k => [k, null])),
      workIdx: 1, // Use index 1 which has values 20, 15, 8, 25 for the base features
      modes: { walk: true, bike: true, car: true, transit: true },
      crimeScope: 'serious',
    };

    const results = scoreAll([...baseFeatures, worstFeature], state);
    const r = results.get('99999999999');

    // Should be scored, not missing
    assert.equal(r.status, 'scored', 'Tract with 255 commute should be scored, got ' + r.status);

    // Find the commute part
    const commutePart = r.parts.find(p => p.key === 'commute');
    assert.ok(commutePart, 'Should have commute part');
    assert.equal(commutePart.missing, false, 'commute should not be missing');
    assert.ok(commutePart.subscore !== null, 'commute subscore should not be null');
    assert.ok(commutePart.subscore >= 0 && commutePart.subscore <= 1, 'subscore in bounds');
  });

  it('criterionValue accepts Uint8Array commute vectors', () => {
    fc.assert(
      fc.property(
        arbFeatureSetUint8(),
        ({ features, state }) => {
          if (state.workIdx === null) return;
          const f = features[0];
          const v = criterionValue(f.properties, 'commute', state);
          // Should return a number (or null if no enabled modes), not throw
          assert.ok(v === null || (typeof v === 'number' && v >= 0 && v <= 255),
            `criterionValue returned ${v}`);
        }
      ),
      { numRuns: FC_RUNS }
    );
  });
});
// ── Property 12: ctx_ fields never affect scoring ────────────────────────────

describe('Property 12: context fields are never scored', () => {
  it('changing ctx_ fields does not change any tract result', () => {
    fc.assert(
      fc.property(
        arbFeatureSet(),
        fc.string({ minLength: 1, maxLength: 50 }),
        ({ features, state }, randomStr) => {
          const r1 = scoreAll(features, state);
          // Modify ctx_ fields of all features
          const modified = features.map(f => ({
            ...f,
            properties: {
              ...f.properties,
              ctx_lang: randomStr,
              ctx_race: randomStr + '2',
              ctx_ej: { is_ej: true, criteria: ['income'] },
              ctx_dummy_obj: { nested: { value: 123 } },
              ctx_dummy_num: 999,
            }
          }));
          const r2 = scoreAll(modified, state);
          for (const [geoid, res1] of r1) {
            const res2 = r2.get(geoid);
            assert.equal(res1.status, res2.status,
              `ctx_ change affected status for ${geoid}`);
            if (res1.score !== null && res2.score !== null) {
              assert.ok(Math.abs(res1.score - res2.score) < 1e-9,
                `ctx_ change affected score for ${geoid}: ${res1.score} vs ${res2.score}`);
            } else {
              assert.equal(res1.score, res2.score);
            }
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });

  it('scoring.js source contains no ctx_ reference (full source, including comments)', () => {
    const __dirname = path.dirname(fileURLToPath(import.meta.url));
    const src = readFileSync(path.join(__dirname, '../js/scoring.js'), 'utf8');
    // Check anywhere in the source file
    assert.ok(!src.includes('ctx_'),
      `scoring.js source contains 'ctx_': ${src.includes('ctx_')}`);
  });
});


// ── Property 14: Subscore bounds and orientation ──────────────────────────────

describe('Property 14: subscore bounds and orientation', () => {
  it('subscore(v, A) in [0,1] for any v in A', () => {
    fc.assert(
      fc.property(
        fc.array(fc.float({ min: 0, max: 10000, noNaN: true }), { minLength: 1, maxLength: 100 })
          .chain(arr => {
            const sorted = [...arr].sort((a, b) => a - b);
            return fc.tuple(
              fc.constant(sorted),
              fc.integer({ min: 0, max: arr.length - 1 }).map(i => sorted[i])
            );
          }),
        ([sorted, v]) => {
          const s = subscore(v, sorted);
          assert.ok(s >= 0, `subscore ${s} < 0`);
          assert.ok(s <= 1, `subscore ${s} > 1`);
        }
      ),
      { numRuns: FC_RUNS }
    );
  });

  it('minimum of A gets the highest subscore', () => {
    fc.assert(
      fc.property(
        fc.array(fc.float({ min: 0, max: 10000, noNaN: true }), { minLength: 2, maxLength: 100 })
          .filter(arr => new Set(arr).size > 1), // at least 2 distinct values
        (arr) => {
          const sorted = [...arr].sort((a, b) => a - b);
          const minVal = sorted[0];
          const sMin = subscore(minVal, sorted);
          for (const v of sorted) {
            const s = subscore(v, sorted);
            assert.ok(sMin >= s - 1e-9,
              `min(${minVal}) subscore ${sMin} < subscore of ${v}=${s}`);
          }
        }
      ),
      { numRuns: FC_RUNS }
    );
  });
});


// ── Additional: Threshold validation in evaluate ─────────────────────────────

describe('Threshold validation', () => {
  it('NaN threshold is treated as "no limit"', () => {
    // Test with a specific key that has positive values
    const features = [{
      type: 'Feature',
      properties: {
        rent: 1000, value: 300000, burden: 30, transit: 10, campus: 2,
        crime_serious: 5, crime_property: 10,
        cm_walk: 'AAA=', cm_bike: 'AAA=', cm_car: 'AAA=', cm_transit: 'AAA=',
        geoid: '00001000000', idx: 0, name: 'Test', town: 'Test', value_topcoded: false
      },
      geometry: null
    }];

    const state = {
      mode: 'rent', ranks: [...CRITERIA],
      mustHave: { rent: true, value: false, burden: false, commute: false, transit: false, campus: false, crime: false },
      threshold: { rent: NaN, value: null, burden: null, commute: null, transit: null, campus: null, crime: null },
      workIdx: null, modes: { walk: true, bike: true, car: true, transit: true }, crimeScope: 'serious'
    };

    const results = scoreAll(features, state);
    const r = results.get('00001000000');
    assert.equal(r.status, 'scored', 'NaN threshold should be treated as no limit');
  });

  it('Infinity threshold is treated as "no limit"', () => {
    const features = [{
      type: 'Feature',
      properties: {
        rent: 1000, value: 300000, burden: 30, transit: 10, campus: 2,
        crime_serious: 5, crime_property: 10,
        cm_walk: 'AAA=', cm_bike: 'AAA=', cm_car: 'AAA=', cm_transit: 'AAA=',
        geoid: '00001000000', idx: 0, name: 'Test', town: 'Test', value_topcoded: false
      },
      geometry: null
    }];

    const state = {
      mode: 'rent', ranks: [...CRITERIA],
      mustHave: { rent: true, value: false, burden: false, commute: false, transit: false, campus: false, crime: false },
      threshold: { rent: Infinity, value: null, burden: null, commute: null, transit: null, campus: null, crime: null },
      workIdx: null, modes: { walk: true, bike: true, car: true, transit: true }, crimeScope: 'serious'
    };

    const results = scoreAll(features, state);
    const r = results.get('00001000000');
    assert.equal(r.status, 'scored', 'Infinity threshold should be treated as no limit');
  });

  it('negative threshold is treated as null (no limit)', () => {
    const features = [{
      type: 'Feature',
      properties: {
        rent: 1000, value: 300000, burden: 30, transit: 10, campus: 2,
        crime_serious: 5, crime_property: 10,
        cm_walk: 'AAA=', cm_bike: 'AAA=', cm_car: 'AAA=', cm_transit: 'AAA=',
        geoid: '00001000000', idx: 0, name: 'Test', town: 'Test', value_topcoded: false
      },
      geometry: null
    }];

    const state = {
      mode: 'rent', ranks: [...CRITERIA],
      mustHave: { rent: true, value: false, burden: false, commute: false, transit: false, campus: false, crime: false },
      threshold: { rent: -100, value: null, burden: null, commute: null, transit: null, campus: null, crime: null },
      workIdx: null, modes: { walk: true, bike: true, car: true, transit: true }, crimeScope: 'serious'
    };

    const results = scoreAll(features, state);
    const r = results.get('00001000000');
    assert.equal(r.status, 'scored', 'Negative threshold should be treated as no limit');
  });
});