/**
 * map.js — Leaflet canvas choropleth + legend.
 *
 * Main choropleth uses the canvas renderer (preferCanvas) for performance with
 * ~450+ polygons. Excluded and no-data tracts are additionally drawn on an SVG
 * overlay pane so a diagonal hatch (excluded) / dot (no-data) pattern can be
 * applied — pattern + color, never color alone (equity.md / Req 8.2).
 */

// 5-class red→yellow→green scale (red = worst fit, green = best fit)
const PALETTE = ['#d7191c', '#fdae61', '#ffffbf', '#a6d96a', '#1a9641'];
const EXCLUDED_COLOR = '#8a8a8a';
const NODATA_COLOR = '#c9c9c9';
const TOPCODE_COLOR = '#d1495b';
// Uniform neutral fill shown until the user sets at least one guideline
const NEUTRAL_COLOR = '#c7ccd3';
const NEUTRAL_OPACITY = 0.18;

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
let basemapLayer = null;
let lastResults = null;      // last scoring results (for restoring styles)
let neutralMode = true;      // true = no guideline set → no colors
let highlightedGeoid = null; // currently highlighted tract

// Map styles — all Esri, keyless (CARTO was dropped: anonymous rate-limit
// blanks the map mid-session). UI chrome stays dark independently of tiles.
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';
export const BASEMAPS = {
  satellite: {
    name: 'Satellite',
    url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
    attribution: 'Tiles &copy; Esri &mdash; Esri, Maxar, Earthstar Geographics',
    maxNativeZoom: 19,
  },
  streets: {
    name: 'Streets',
    url: `${ESRI}/World_Street_Map/MapServer/tile/{z}/{y}/{x}`,
    attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors',
    maxNativeZoom: 19,
  },
  light: {
    name: 'Light',
    url: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
    attribution: 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors',
    maxNativeZoom: 16, // Light Gray Canvas has no tiles past z16; upscale beyond
  },
};
let currentBasemap = null;

export function setBasemap(name) {
  if (!map) return;
  const key = BASEMAPS[name] ? name : 'satellite';
  if (key === currentBasemap && basemapLayer) return;
  const b = BASEMAPS[key];
  if (basemapLayer) map.removeLayer(basemapLayer);
  basemapLayer = L.tileLayer(b.url, {
    attribution: b.attribution,
    maxZoom: 18,
    maxNativeZoom: b.maxNativeZoom,
  }).addTo(map);
  basemapLayer.bringToBack();
  currentBasemap = key;
}

export function initMap(features, geojson, clickHandler) {
  onTractClick = clickHandler;
  allGeojson = geojson;

  map = L.map('map', {
    preferCanvas: true,
    center: [42.3603, -71.0580],
    zoom: 11,
    zoomControl: false, // re-added bottom-left so it never covers the sidebar toggles
  });
  L.control.zoom({ position: 'bottomleft' }).addTo(map);

  setBasemap('satellite');

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
    const bounds = geoLayer.getBounds();
    map.fitBounds(bounds, { padding: [20, 20] });
    map.setMaxBounds(bounds.pad(0.4));
    map.setMinZoom(map.getZoom() - 2);
  } catch { /* ignore */ }

  return map;
}

function defaultStyle() {
  return { fillColor: NEUTRAL_COLOR, weight: 0.5, color: '#fff', fillOpacity: NEUTRAL_OPACITY, opacity: 0.8 };
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

  // No guideline set yet → uniform neutral fill, no score colors at all
  if (neutralMode) return { color: NEUTRAL_COLOR, patterned: null, neutral: true };

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

function canvasStyle(fill) {
  if (fill.patterned) return { fillOpacity: 0, weight: 0, opacity: 0 }; // drawn on SVG overlay
  if (fill.neutral) return defaultStyle();
  return { fillColor: fill.color, weight: 0.5, color: '#ffffff', fillOpacity: 0.55, opacity: 1 };
}

function overlayStyle(fill) {
  const st = { fillColor: fill.color, weight: 0.5, color: '#fff', fillOpacity: 0.9, opacity: 1 };
  if (fill.patterned === 'hatch' && hatchPattern) st.fillPattern = hatchPattern;
  return st;
}

function fillFor(feature) {
  const r = lastResults ? lastResults.get(feature.properties.geoid) : null;
  return featureFill(feature, r);
}

/**
 * Apply scoring results: color the canvas layer and move patterned tracts to
 * the SVG overlay.
 * @param {Map} results — scoreAll() output
 * @param {{neutral?: boolean}} opts — neutral=true → no guideline set, draw a
 *        uniform neutral fill instead of score colors
 */
export function restyle(results, opts = {}) {
  if (!geoLayer) return;
  lastResults = results;
  neutralMode = !!opts.neutral;

  const patternedFeatures = [];

  geoLayer.eachLayer(layer => {
    const fill = fillFor(layer.feature);
    layer.setStyle(canvasStyle(fill));
    if (fill.patterned) patternedFeatures.push(layer.feature);
  });

  // Rebuild SVG overlay
  overlayLayer.clearLayers();
  for (const feature of patternedFeatures) overlayLayer.addData(feature);
  overlayLayer.eachLayer(layer => layer.setStyle(overlayStyle(fillFor(layer.feature))));

  // Keep the selected tract outlined across recolors
  if (highlightedGeoid) applyHighlight(highlightedGeoid);
}

function eachTractLayer(geoid, fn) {
  [geoLayer, overlayLayer].forEach(lyr => {
    if (!lyr) return;
    lyr.eachLayer(layer => {
      if (layer.feature && layer.feature.properties.geoid === geoid) fn(layer, lyr);
    });
  });
}

function applyHighlight(geoid) {
  eachTractLayer(geoid, (layer, lyr) => {
    // A patterned tract is invisible on canvas; outline it on the overlay only
    if (lyr === geoLayer && fillFor(layer.feature).patterned) return;
    layer.setStyle({ weight: 2.5, color: '#111', opacity: 1 });
    if (layer.bringToFront) layer.bringToFront();
  });
}

/** Restore a tract's normal (non-highlighted) style. */
function restoreStyle(geoid) {
  eachTractLayer(geoid, (layer, lyr) => {
    const fill = fillFor(layer.feature);
    layer.setStyle(lyr === geoLayer ? canvasStyle(fill) : overlayStyle(fill));
  });
}

/** Highlight one tract, un-highlighting the previously selected one. */
export function highlightTract(geoid) {
  if (highlightedGeoid && highlightedGeoid !== geoid) restoreStyle(highlightedGeoid);
  highlightedGeoid = geoid;
  applyHighlight(geoid);
}

/** Remove any tract highlight (panel closed / background clicked). */
export function clearHighlight() {
  if (highlightedGeoid) restoreStyle(highlightedGeoid);
  highlightedGeoid = null;
}

export function setBuyMode(on, features) {
  buyMode = on;
  if (on && features) computeBuyBreaks(features);
}

export function renderLegend(mode) {
  const el = document.getElementById('legend');
  if (!el) return;

  if (mode === 'neutral') {
    el.innerHTML = `
      <h3>No colors yet</h3>
      <div class="legend-row">
        <span class="legend-swatch" style="background:${NEUTRAL_COLOR}"></span>
        <span class="legend-label">Every area looks the same until you set a limit</span>
      </div>
      <p class="source">Type a limit for any priority (for example, max rent) to color the map by fit.</p>
    `;
    return;
  }

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
