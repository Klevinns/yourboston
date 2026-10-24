/**
 * map.js — Leaflet canvas choropleth + legend.
 *
 * Main choropleth uses the canvas renderer (preferCanvas) for performance with
 * ~450+ polygons. Excluded and no-data tracts are additionally drawn on an SVG
 * overlay pane so a diagonal hatch (excluded) / dot (no-data) pattern can be
 * applied — pattern + color, never color alone (equity.md / Req 8.2).
 */

// 5-class viridis sequential palette (dark→bright = low→high score)
const PALETTE = ['#440154', '#3b528b', '#21908d', '#5dc863', '#fde725'];
const EXCLUDED_COLOR = '#8a8a8a';
const NODATA_COLOR = '#c9c9c9';
const TOPCODE_COLOR = '#d1495b';

const SCORE_BREAKS = [20, 40, 60, 80]; // 5 classes

let map = null;
let geoLayer = null;      // canvas choropleth
let overlayLayer = null;  // SVG pane for patterned (excluded/nodata) tracts
let hatchPattern = null;
let dotPattern = null;
let onTractClick = null;
let buyMode = false;
let buyBreaks = null;
let allGeojson = null;

export function initMap(features, geojson, clickHandler) {
  onTractClick = clickHandler;
  allGeojson = geojson;

  map = L.map('map', {
    preferCanvas: true,
    center: [42.3603, -71.0580],
    zoom: 11,
  });

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18,
  }).addTo(map);

  // SVG patterns for excluded / nodata
  if (typeof L.StripePattern === 'function') {
    hatchPattern = new L.StripePattern({ color: '#444', weight: 2, spaceWeight: 3, angle: 45, opacity: 0.9 });
    hatchPattern.addTo(map);
  }

  // Canvas choropleth layer
  geoLayer = L.geoJSON(geojson, {
    renderer: L.canvas(),
    style: () => defaultStyle(),
    onEachFeature: (feature, layer) => {
      layer.on('click', () => onTractClick && onTractClick(feature.properties.geoid, feature));
    },
  }).addTo(map);

  // SVG overlay layer for patterned tracts (initially empty)
  overlayLayer = L.geoJSON(null, {
    renderer: L.svg(),
    style: () => ({ weight: 0.5, color: '#fff', fillOpacity: 1 }),
    onEachFeature: (feature, layer) => {
      layer.on('click', () => onTractClick && onTractClick(feature.properties.geoid, feature));
    },
  }).addTo(map);

  try {
    map.fitBounds(geoLayer.getBounds(), { padding: [10, 10] });
  } catch { /* ignore */ }

  return map;
}

function defaultStyle() {
  return { fillColor: NODATA_COLOR, weight: 0.5, color: '#fff', fillOpacity: 0.8 };
}

function scoreColor(score) {
  for (let i = 0; i < SCORE_BREAKS.length; i++) {
    if (score < SCORE_BREAKS[i]) return PALETTE[i];
  }
  return PALETTE[PALETTE.length - 1];
}

function valueQuintile(v) {
  if (!buyBreaks) return 2;
  for (let i = 0; i < buyBreaks.length; i++) {
    if (v <= buyBreaks[i]) return i;
  }
  return PALETTE.length - 1;
}

export function computeBuyBreaks(features) {
  // Quintile breaks over non-top-coded values
  const vals = features
    .filter(f => f.properties.value !== null && f.properties.value !== undefined && !f.properties.value_topcoded)
    .map(f => f.properties.value)
    .sort((a, b) => a - b);
  if (vals.length === 0) { buyBreaks = null; return; }
  const breaks = [];
  for (let q = 1; q <= 4; q++) {
    const idx = Math.floor((q / 5) * (vals.length - 1));
    breaks.push(vals[idx]);
  }
  buyBreaks = breaks;
}

/**
 * Determine fill for a feature: { color, patterned: 'hatch'|'dots'|null }.
 */
function featureFill(feature, result) {
  const p = feature.properties;

  if (buyMode) {
    if (p.value === null || p.value === undefined) return { color: NODATA_COLOR, patterned: 'dots' };
    if (p.value_topcoded) return { color: TOPCODE_COLOR, patterned: p.value_moe_high ? 'hatch' : null };
    return { color: PALETTE[valueQuintile(p.value)], patterned: p.value_moe_high ? 'hatch' : null };
  }

  if (!result) return { color: NODATA_COLOR, patterned: 'dots' };
  if (result.status === 'excluded') return { color: EXCLUDED_COLOR, patterned: 'hatch' };
  if (result.status === 'nodata')  return { color: NODATA_COLOR, patterned: 'dots' };
  return { color: scoreColor(result.score), patterned: null };
}

/**
 * Apply scoring results: color the canvas layer and move patterned tracts to
 * the SVG overlay.
 */
export function restyle(results) {
  if (!geoLayer) return;

  const patternedFeatures = [];

  geoLayer.eachLayer(layer => {
    const f = layer.feature;
    const r = results ? results.get(f.properties.geoid) : null;
    const fill = featureFill(f, r);

    if (fill.patterned) {
      // Hide on canvas, draw on SVG overlay instead
      layer.setStyle({ fillOpacity: 0, weight: 0, opacity: 0 });
      patternedFeatures.push({ feature: f, fill });
    } else {
      layer.setStyle({
        fillColor: fill.color,
        weight: 0.5,
        color: '#ffffff',
        fillOpacity: 0.85,
        opacity: 1,
      });
    }
  });

  // Rebuild SVG overlay
  overlayLayer.clearLayers();
  for (const { feature } of patternedFeatures) {
    overlayLayer.addData(feature);
  }
  // Apply pattern/color per layer
  overlayLayer.eachLayer(layer => {
    const geoid = layer.feature.properties.geoid;
    const match = patternedFeatures.find(pf => pf.feature.properties.geoid === geoid);
    if (!match) return;
    const st = { fillColor: match.fill.color, weight: 0.5, color: '#fff', fillOpacity: 0.9 };
    if (match.fill.patterned === 'hatch' && hatchPattern) st.fillPattern = hatchPattern;
    layer.setStyle(st);
  });
}

export function highlightTract(geoid) {
  [geoLayer, overlayLayer].forEach(lyr => {
    if (!lyr) return;
    lyr.eachLayer(layer => {
      if (!layer.feature) return;
      const sel = layer.feature.properties.geoid === geoid;
      if (sel) {
        layer.setStyle({ weight: 2.5, color: '#111', opacity: 1 });
        if (layer.bringToFront) layer.bringToFront();
      }
    });
  });
}

export function setBuyMode(on, features) {
  buyMode = on;
  if (on && features) computeBuyBreaks(features);
}

export function renderLegend(mode) {
  const el = document.getElementById('legend');
  if (!el) return;

  if (mode === 'buy') {
    el.innerHTML = `
      <h3>Home value (percentile)</h3>
      ${PALETTE.map((c, i) => `
        <div class="legend-row">
          <span class="legend-swatch" style="background:${c}"></span>
          <span class="legend-label">${['Lowest 20%','20–40%','40–60%','60–80%','Top 20%'][i]}</span>
        </div>`).join('')}
      <div class="legend-row">
        <span class="legend-swatch" style="background:${TOPCODE_COLOR}"></span>
        <span class="legend-label">$2M+ (top-coded)</span>
      </div>
      <div class="legend-row">
        <span class="legend-swatch" style="background:${NODATA_COLOR}"></span>
        <span class="legend-label">High margin of error (hatched) / no data (dots)</span>
      </div>
      <p class="source">Median owner-estimated home value, ACS 2020–2024 5-year (B25077). Self-reported; lags market prices. Hatched tracts have high margin of error.</p>
    `;
    return;
  }

  el.innerHTML = `
    <h3>Fit score (0–100)</h3>
    ${PALETTE.map((c, i) => {
      const lo = i === 0 ? 0 : SCORE_BREAKS[i - 1];
      const hi = i < SCORE_BREAKS.length ? SCORE_BREAKS[i] : 100;
      return `<div class="legend-row">
        <span class="legend-swatch" style="background:${c}"></span>
        <span class="legend-label">${lo}–${hi}</span>
      </div>`;
    }).join('')}
    <div class="legend-row">
      <span class="legend-swatch" style="background:${EXCLUDED_COLOR}"></span>
      <span class="legend-label">Excluded — fails a must-have (hatched)</span>
    </div>
    <div class="legend-row">
      <span class="legend-swatch" style="background:${NODATA_COLOR}"></span>
      <span class="legend-label">Not enough data (dots)</span>
    </div>
    <p class="source">Higher = better fit for your priorities.</p>
  `;
}

export function getMap() { return map; }
