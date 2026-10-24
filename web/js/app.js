/**
 * app.js — YourBoston application entry point.
 *
 * Wires data + scoring + map + controls + panel together.
 */

import { loadData } from './data.js';
import { scoreAll, CRITERIA, activeCriteria, criterionValue } from './scoring.js';
import * as mapMod from './map.js';
import * as controls from './controls.js';
import * as panel from './panel.js';
import { icon } from './icons.js';

let features = [];
let geojson = null;
let lastResults = null;

async function main() {
  // Icons on the map chrome buttons (before the data load so they never look blank)
  document.getElementById('sidebar-toggle').innerHTML = icon('chevron-left', 18);
  document.getElementById('sidebar-expand').innerHTML = icon('maximize-2', 16);

  // Load data + next steps in parallel
  const [data] = await Promise.all([
    loadData('data/fitmap.geojson'),
    panel.loadNextSteps('data/next-steps.json'),
  ]);

  features = data.features;
  geojson = data.geojson;

  // Init map
  mapMod.initMap(features, geojson, onTractClick);

  // Init controls
  controls.initControls(onStateChange, onArmWork);

  // Panel close button
  document.getElementById('panel-close').addEventListener('click', panel.hidePanel);

  // Map click for setting work location
  mapMod.getMap().on('click', onMapClick);

  // Initial score + render (also renders the legend for the current state)
  rescore(controls.getState());

  // ── UI extras: sidebar toggle, collapsible sections, basemap toggle ─────────
  // Width is driven purely by CSS classes (see app.css: .app-layout.sidebar-*).
  const sidebar = document.querySelector('.sidebar');
  const layout = document.querySelector('.app-layout');
  const sbToggle = document.getElementById('sidebar-toggle');
  const sbExpand = document.getElementById('sidebar-expand');

  // Leaflet must re-measure after the grid column changes (and after any transition)
  const refreshMapSize = () => {
    const m = mapMod.getMap();
    if (!m) return;
    requestAnimationFrame(() => m.invalidateSize());
    setTimeout(() => m.invalidateSize(), 250);
  };

  sbToggle.addEventListener('click', () => {
    const collapsed = sidebar.classList.toggle('collapsed');
    layout.classList.toggle('sidebar-collapsed', collapsed);
    sidebar.setAttribute('aria-hidden', String(collapsed));
    if (collapsed) sidebar.setAttribute('inert', ''); else sidebar.removeAttribute('inert');
    sbToggle.innerHTML = icon(collapsed ? 'chevron-right' : 'chevron-left', 18);
    sbToggle.setAttribute('aria-expanded', String(!collapsed));
    sbToggle.setAttribute('aria-label', collapsed ? 'Show controls' : 'Hide controls');
    sbExpand.disabled = collapsed;
    refreshMapSize();
  });

  sbExpand.addEventListener('click', () => {
    const expanded = sidebar.classList.toggle('expanded');
    layout.classList.toggle('sidebar-expanded', expanded);
    sbExpand.innerHTML = icon(expanded ? 'minimize-2' : 'maximize-2', 16);
    sbExpand.setAttribute('aria-pressed', String(expanded));
    sbExpand.setAttribute('aria-label', expanded ? 'Narrow controls' : 'Widen controls');
    refreshMapSize();
  });

  document.querySelectorAll('.control-group h2').forEach(h => {
    h.addEventListener('click', () => h.parentElement.classList.toggle('collapsed'));
  });

  const wireBasemap = (id, name) => {
    document.getElementById(id).addEventListener('click', () => {
      mapMod.setBasemap(name);
      document.querySelectorAll('.basemap-toggle button').forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      document.getElementById(id).classList.add('active');
      document.getElementById(id).setAttribute('aria-pressed', 'true');
    });
  };
  wireBasemap('basemap-satellite', 'satellite');
  wireBasemap('basemap-streets', 'streets');
  wireBasemap('basemap-light', 'light');
}

function onStateChange(state) {
  mapMod.setBuyMode(state.mode === 'buy', features);
  rescore(state);
}

function rescore(state) {
  lastResults = scoreAll(features, state);

  // No guideline (no active must-have with a finite limit) → neutral, uncolored map
  const neutral = !hasActiveMustHave(state);
  mapMod.restyle(lastResults, { neutral });
  mapMod.renderLegend(neutral ? 'neutral' : (state.mode === 'buy' ? 'buy' : 'score'));

  if (neutral) {
    announce('Map colors are off. Set a limit on any priority to color the map.');
    panel.showEmptyState(null);
    updatePrompt(state);
    return;
  }

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

// Set by a tract's own click handler (fires before the map's) so the map-level
// background check never closes a panel a tract click just opened.
let tractClickPending = false;

function onTractClick(geoid, feature) {
  tractClickPending = true;
  if (controls.isWorkArmed()) {
    // In work-set mode, a tract click sets the work location
    setWork(feature);
    return;
  }
  if (controls.isSchoolArmed()) {
    setSchoolFromFeature(feature);
    return;
  }
  const result = lastResults ? lastResults.get(geoid) : null;
  mapMod.highlightTract(geoid);
  panel.showPanel(feature, result || { status: 'nodata', parts: [], failed: [] }, controls.getState());
}

let armWork = false;
function onArmWork(armed) { armWork = armed; }

function onMapClick(e) {
  const pt = e.latlng;
  if (controls.isSchoolArmed()) {
    tractClickPending = false;
    const feat = findContainingTract(pt.lng, pt.lat);
    if (feat) setSchoolFromFeature(feat); else controls.schoolOutOfArea();
    return;
  }
  // Find containing tract via point-in-polygon
  const feat = findContainingTract(pt.lng, pt.lat);
  if (!controls.isWorkArmed()) {
    // Background click (outside every tract) clears the selection
    if (!feat && !tractClickPending) panel.hidePanel();
    tractClickPending = false;
    return;
  }
  tractClickPending = false;
  if (feat) {
    setWork(feat);
  } else {
    controls.workOutOfArea();
  }
}

function setWork(feature) {
  controls.setWorkTract(feature.properties.idx, feature.properties.name || feature.properties.town);
}

function setSchoolFromFeature(feature) {
  controls.setSchoolLocation(Number(feature.properties.lat), Number(feature.properties.lon));
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
