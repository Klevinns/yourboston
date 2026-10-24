/**
 * app.js — FitMap application entry point.
 *
 * Wires data + scoring + map + controls + panel together.
 */

import { loadData } from './data.js';
import { scoreAll, CRITERIA, activeCriteria, criterionValue } from './scoring.js';
import * as mapMod from './map.js';
import * as controls from './controls.js';
import * as panel from './panel.js';

let features = [];
let geojson = null;
let lastResults = null;

async function main() {
  // Load data + next steps in parallel
  const [data] = await Promise.all([
    loadData('data/fitmap.geojson'),
    panel.loadNextSteps('data/next-steps.json'),
  ]);

  features = data.features;
  geojson = data.geojson;

  // Init map
  mapMod.initMap(features, geojson, onTractClick);
  mapMod.renderLegend('score');

  // Init controls
  controls.initControls(onStateChange, onArmWork);

  // Panel close button
  document.getElementById('panel-close').addEventListener('click', panel.hidePanel);

  // Map click for setting work location
  mapMod.getMap().on('click', onMapClick);

  // Initial score + render
  rescore(controls.getState());
}

function onStateChange(state) {
  mapMod.setBuyMode(state.mode === 'buy', features);
  mapMod.renderLegend(state.mode === 'buy' ? 'buy' : 'score');
  rescore(state);
}

function rescore(state) {
  lastResults = scoreAll(features, state);
  mapMod.restyle(lastResults);

  // Count passing (scored) tracts
  let passing = 0, excluded = 0;
  for (const [, r] of lastResults) {
    if (r.status === 'scored') passing++;
    else if (r.status === 'excluded') excluded++;
  }

  announce(`${passing} neighborhoods match your priorities.`);

  // Empty state when zero pass and must-haves are active
  if (passing === 0 && hasActiveMustHave(state)) {
    panel.showEmptyState(worstMustHave(state, lastResults));
  } else {
    panel.showEmptyState(null);
  }

  // Dismiss budget prompt once a budget threshold is set
  updatePrompt(state);
}

function hasActiveMustHave(state) {
  const active = activeCriteria(state);
  return active.some(k => {
    if (!state.mustHave[k]) return false;
    const thr = state.threshold[k];
    return thr !== null && thr !== undefined && Number.isFinite(thr) && thr >= 0;
  });
}

/**
 * Determine which must-have criterion excludes the most tracts.
 * Uses lastResults to count failures per criterion key.
 */
function worstMustHave(state, lastResults) {
  if (!lastResults) return null;

  // Count failures per criterion key from lastResults
  const counts = {};
  for (const [, result] of lastResults) {
    if (!result.failed) continue;
    for (const k of result.failed) {
      counts[k] = (counts[k] || 0) + 1;
    }
  }

  if (Object.keys(counts).length === 0) return null;

  // Find max count, tie-break by CRITERIA order
  let worst = null, max = -1;
  for (const k of CRITERIA) {
    const c = counts[k] || 0;
    if (c > max) { max = c; worst = k; }
  }
  return worst ? { criterion: worst, count: max } : null;
}

function onTractClick(geoid, feature) {
  if (controls.isWorkArmed()) {
    // In work-set mode, a tract click sets the work location
    setWork(feature);
    return;
  }
  const result = lastResults ? lastResults.get(geoid) : null;
  mapMod.highlightTract(geoid);
  panel.showPanel(feature, result || { status: 'nodata', parts: [], failed: [] }, controls.getState());
}

let armWork = false;
function onArmWork(armed) { armWork = armed; }

function onMapClick(e) {
  if (!controls.isWorkArmed()) return;
  // Find containing tract via point-in-polygon
  const pt = e.latlng;
  const feat = findContainingTract(pt.lng, pt.lat);
  if (feat) {
    setWork(feat);
  } else {
    controls.workOutOfArea();
  }
}

function setWork(feature) {
  controls.setWorkTract(feature.properties.idx, feature.properties.name || feature.properties.town);
}

/**
 * Point-in-polygon against loaded geometry (ray casting).
 */
function findContainingTract(lon, lat) {
  for (const f of features) {
    if (pointInGeometry(lon, lat, f.geometry)) return f;
  }
  return null;
}

function pointInGeometry(lon, lat, geom) {
  if (!geom) return false;
  if (geom.type === 'Polygon') return pointInPolygon(lon, lat, geom.coordinates);
  if (geom.type === 'MultiPolygon') {
    return geom.coordinates.some(poly => pointInPolygon(lon, lat, poly));
  }
  return false;
}

function pointInPolygon(lon, lat, rings) {
  // rings[0] = outer, rings[1..] = holes
  if (!rings.length) return false;
  let inside = rayCast(lon, lat, rings[0]);
  for (let i = 1; i < rings.length; i++) {
    if (rayCast(lon, lat, rings[i])) inside = !inside; // in a hole
  }
  return inside;
}

function rayCast(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > lat) !== (yj > lat)) &&
      (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function announce(msg) {
  const el = document.getElementById('aria-announce');
  if (el) el.textContent = msg;
}

function updatePrompt(state) {
  const prompt = document.getElementById('budget-prompt');
  if (!prompt) return;
  const budgetKey = state.mode === 'rent' ? 'rent' : 'value';
  const hasBudget = state.threshold[budgetKey] !== null && state.threshold[budgetKey] !== undefined;
  const hasWork = state.workIdx !== null;
  if (hasBudget && hasWork) {
    prompt.hidden = true;
  } else {
    prompt.hidden = false;
    const need = [];
    if (!hasBudget) need.push('a budget');
    if (!hasWork) need.push('a work location');
    prompt.textContent = `Set ${need.join(' and ')} to see your best-fit neighborhoods.`;
  }
}

main().catch(err => {
  console.error(err);
  const el = document.getElementById('map');
  if (el) el.innerHTML = `<p style="padding:2rem;color:#d84315">Failed to load map data: ${err.message}</p>`;
});
