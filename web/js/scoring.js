/**
 * scoring.js — YourBoston pure scoring module.
 *
 * PURE: no DOM access, no Leaflet, no Math.random, no Date.
 * No context/prose-only fields (language, race, demographics, EJ).
 * All scoring logic is here and here only.
 *
 * Exports:
 *   CRITERIA          — ordered list of all six criterion keys
 *   rocWeights(keys)  — Rank-Order-Centroid weights {key: weight}, Σ=1
 *   percentileTables(features, keys) — sorted non-null value arrays per key
 *   subscore(value, sortedValues)    — mid-rank percentile [0,1], 1=best
 *   activeCriteria(state) — derive active criteria from state (ranks minus inactive budget axis minus commute when workIdx null)
 *   criterionValue(props, key, state) — extract criterion value from properties (public export of internal helper)
 *   evaluate(featureProps, state, tables) → {status, score?, parts[], failed[], missing[]}
 *   scoreAll(features, state)        → Map<geoid, result>
 */

// ── Constants ─────────────────────────────────────────────────────────────────

/** All six scoreable criterion keys (Requirements 7.1, equity.md). */
export const CRITERIA = ['rent', 'value', 'burden', 'commute', 'transit', 'school'];

// All criteria are lower-is-better (design.md §scoring.js).
// subscore orientation: 1 = best = lowest raw value.


// ── Weights ───────────────────────────────────────────────────────────────────

/**
 * Rank-Order-Centroid (ROC) weights for a ranked list of criterion keys.
 *
 * ROC formula: w_i = (1/k) * Σ_{j=i}^{k} 1/j   (i = 1 is most important)
 *
 * Returns {key: weight} where weights are strictly decreasing with rank,
 * each in (0,1], and sum to 1 within floating-point tolerance.
 *
 * @param {string[]} rankedKeys — criterion keys in importance order (index 0 = most important)
 * @returns {Object.<string, number>}
 */
export function rocWeights(rankedKeys) {
  const k = rankedKeys.length;
  if (k === 0) return {};
  const weights = {};
  for (let i = 0; i < k; i++) {
    let sum = 0;
    for (let j = i + 1; j <= k; j++) {
      sum += 1 / j;
    }
    weights[rankedKeys[i]] = sum / k;
  }
  return weights;
}


// ── Percentile tables ─────────────────────────────────────────────────────────

/**
 * Build sorted (ascending) non-null value arrays for each criterion key.
 * Used to compute mid-rank percentile subscores.
 *
 * @param {Array} features — GeoJSON features array
 * @param {string[]} keys — which keys to build tables for
 * @param {Object} state — scoring state (for commute mode, crime scope etc.)
 * @returns {Object.<string, number[]>}
 */
export function percentileTables(features, keys, state) {
  const tables = {};
  for (const key of keys) {
    const vals = [];
    for (const f of features) {
      const v = criterionValue(f.properties, key, state);
      if (v !== null && v !== undefined && !Number.isNaN(v)) {
        vals.push(v);
      }
    }
    vals.sort((a, b) => a - b);
    tables[key] = vals;
  }
  return tables;
}


// ── Subscore ──────────────────────────────────────────────────────────────────

/**
 * Mid-rank percentile of value v in sorted array A.
 *
 * s = (count(a > v) + 0.5 * (count(a == v) - 1)) / (n - 1)
 * When n == 1, s = 1.
 * All lower-is-better: minimum of A → score 1.0 (best).
 *
 * @param {number} value
 * @param {number[]} sortedValues — ascending sorted non-null values over full study area
 * @returns {number} in [0, 1]
 */
export function subscore(value, sortedValues) {
  const n = sortedValues.length;
  if (n === 0) return 0;
  if (n === 1) return 1;

  // Binary search to count elements greater than value
  let countGreater = 0;
  let countEqual = 0;
  for (const a of sortedValues) {
    if (a > value) countGreater++;
    else if (a === value) countEqual++;
  }

  // s = (countGreater + 0.5 * (countEqual - 1)) / (n - 1)
  const s = (countGreater + 0.5 * (countEqual - 1)) / (n - 1);
  return Math.max(0, Math.min(1, s));
}


// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Derive active criteria from state: ranks minus inactive budget axis
 * and minus commute when workIdx is null.
 *
 * @param {Object} state — scoring state
 * @returns {string[]} active criterion keys in ranked order
 */
export function activeCriteria(state) {
  const mode = state.mode || 'rent';
  const ranks = state.ranks || [...CRITERIA];
  return ranks.filter(k => {
    if (k === 'value' && mode === 'rent')  return false;
    if (k === 'rent'  && mode === 'buy')   return false;
    if (k === 'commute' && (state.workIdx === null || state.workIdx === undefined)) return false;
    if (k === 'school' && (state.school === null || state.school === undefined)) return false;
    return true;
  });
}

/**
 * Extract the criterion value for a given key from feature properties.
 * For commute: min over enabled modes (255 kept as worst, not null).
 * For crime: serious or serious+property per crimeScope.
 *
 * @param {Object} props — feature.properties
 * @param {string} key   — criterion key
 * @param {Object} state — scoring state
 * @returns {number|null}
 */
export function criterionValue(props, key, state) {
  if (key === 'commute') {
    return _commuteValue(props, state);
  }
  if (key === 'school') {
    return _schoolValue(props, state);
  }
  // Direct reads: rent, value, burden, transit
  const v = props[key];
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Return commute value (minutes) for the current work tract and enabled modes.
 * 255 = no route (kept as worst; not null — Requirement 7.9).
 *
 * @param {Object} props
 * @param {Object} state
 * @returns {number|null} minutes, or null if no work tract set
 */
function _commuteValue(props, state) {
  if (state.workIdx === null || state.workIdx === undefined) return null;
  const idx = state.workIdx;
  const modes = state.modes || { walk: true, bike: true, car: true, transit: true };

  const modeKeys = ['walk', 'bike', 'car', 'transit'];
  const vecKeys  = ['cm_walk', 'cm_bike', 'cm_car', 'cm_transit'];

  let best = null;
  for (let m = 0; m < modeKeys.length; m++) {
    if (!modes[modeKeys[m]]) continue;
    const vec = props[vecKeys[m]];
    if (!vec) continue;
    const mins = _decodeVec(vec, idx);
    if (mins === null) continue;
    // 255 = no route — keep as value (worst), not null
    if (best === null || mins < best) best = mins;
  }
  return best;
}

/**
 * Decode a commute vector (base64 string or Uint8Array) and return the value at index idx.
 * Returns null if vector is missing or invalid; 255 if no-route.
 */
function _decodeVec(vec, idx) {
  if (vec === null || vec === undefined) return null;
  let bytes;
  if (vec instanceof Uint8Array) {
    bytes = vec;
  } else if (typeof vec === 'string') {
    try {
      bytes = _b64ToBytes(vec);
    } catch {
      return null;
    }
  } else {
    return null;
  }
  if (idx < 0 || idx >= bytes.length) return null;
  return bytes[idx]; // integer 0–255
}

/**
 * Decode base64 string to Uint8Array.
 * Works in both Node (Buffer) and browser (atob) environments.
 */
function _b64ToBytes(b64) {
  if (typeof Buffer !== 'undefined') {
    // Node.js
    return Buffer.from(b64, 'base64');
  }
  // Browser
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

/**
 * School value: straight-line distance (miles) from this tract's internal point
 * to the user's school location (set by clicking the map).
 */
function _schoolValue(props, state) {
  const s = state.school;
  if (!s) return null;
  const lat = Number(props.lat), lon = Number(props.lon);
  const slat = Number(s.lat), slon = Number(s.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(slat) || !Number.isFinite(slon)) return null;
  return haversineMi(lat, lon, slat, slon);
}

/** Great-circle distance in miles (haversine). */
function haversineMi(lat1, lon1, lat2, lon2) {
  const R = 3958.8;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}


// ── Evaluate one tract ────────────────────────────────────────────────────────

/**
 * Evaluate a single tract given the current scoring state.
 *
 * @param {Object} featureProps — feature.properties (must include idx for commute)
 * @param {Object} state        — scoring state (see design.md §scoring.js)
 * @param {Object} tables       — percentile tables from percentileTables()
 * @returns {{ status: 'scored'|'excluded'|'nodata', score?: number, parts: Array, failed: string[], missing: string[] }}
 */
export function evaluate(featureProps, state, tables) {
  const props  = featureProps;
  const mode   = state.mode || 'rent';
  const ranks  = state.ranks || [...CRITERIA];

  // ── Step 1: derive active criteria ────────────────────────────────────────
  const active = activeCriteria(state);

  // ── Step 2: must-have check (loop ALL active must-haves first) ─────────────
  const failed = [];
  const missing = [];
  const mustHave  = state.mustHave  || {};
  const threshold = state.threshold || {};

  // Collect all failed/missing before deciding status (Req 7.1)
  for (const k of active) {
    if (!mustHave[k]) continue;
    const thr = threshold[k];
    // Threshold is only active if it's a finite non-negative number
    if (thr === null || thr === undefined || !Number.isFinite(thr) || thr < 0) continue;

    const v = criterionValue(props, k, state);
    if (v === null) {
      missing.push(k);
    } else if (v > thr) {
      failed.push(k);
    }
  }

  // Sort failed and missing in CRITERIA order for deterministic results
  const critIdx = Object.fromEntries(CRITERIA.map((k, i) => [k, i]));
  failed.sort((a, b) => (critIdx[a] ?? 0) - (critIdx[b] ?? 0));
  missing.sort((a, b) => (critIdx[a] ?? 0) - (critIdx[b] ?? 0));

  if (failed.length > 0) {
    return { status: 'excluded', score: null, parts: [], failed, missing };
  }

  if (missing.length > 0) {
    return { status: 'nodata', score: null, parts: [], failed: [], missing };
  }

  // ── Step 3: compute weights and subscores ──────────────────────────────────
  const weights = rocWeights(active);

  const parts = [];
  let weightedSum  = 0;
  let availWeight  = 0;

  for (const k of active) {
    const v = criterionValue(props, k, state);
    const sortedVals = tables[k] || [];
    const isNull = v === null;

    let ss = null;
    if (!isNull && sortedVals.length > 0) {
      ss = subscore(v, sortedVals);
    }

    const w = weights[k];
    if (ss !== null) {
      weightedSum += w * ss;
      availWeight += w;
    }

    parts.push({
      key:         k,
      value:       v,
      subscore:    ss,
      weight:      w,
      contribution: (ss !== null) ? w * ss : null,
      missing:     isNull,
      mustHave:    !!mustHave[k],
      threshold:   threshold[k] ?? null,
    });
  }

  if (availWeight === 0) {
    return { status: 'nodata', score: null, parts, failed: [], missing: [] };
  }

  // Renormalise: score = Σ(w_k * s_k) / Σ(w_k for available)
  const rawScore = 100 * weightedSum / availWeight;
  const score    = Math.max(0, Math.min(100, rawScore));

  return { status: 'scored', score, parts, failed: [], missing: [] };
}


// ── Score all tracts ──────────────────────────────────────────────────────────

/**
 * Score all features given the current state.
 *
 * @param {Array}  features — GeoJSON features array (with .properties including commute vectors)
 * @param {Object} state    — scoring state
 * @returns {Map<string, Object>} geoid → evaluate() result
 */
export function scoreAll(features, state) {
  // Build percentile tables over all features for active criteria
  const active = activeCriteria(state);

  const tables = percentileTables(features, active, state);
  const results = new Map();

  for (const f of features) {
    const geoid = f.properties.geoid;
    results.set(geoid, evaluate(f.properties, state, tables));
  }

  return results;
}
