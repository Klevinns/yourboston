/**
 * data.js — load and decode the FitMap GeoJSON.
 *
 * Decodes base64 Uint8 commute vectors into typed arrays attached to each
 * feature for fast lookup by scoring.js.
 */

/**
 * Decode a base64 string to Uint8Array (browser).
 */
export function b64ToBytes(b64) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr;
}

/**
 * Load fitmap.geojson and return { geojson, features, metadata }.
 * Commute vectors remain base64 in properties (scoring.js decodes lazily),
 * but we also attach decoded Uint8Arrays under _cm for the panel.
 */
export async function loadData(url = 'data/fitmap.geojson') {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`Failed to load ${url}: HTTP ${resp.status}`);
  }
  const geojson = await resp.json();
  const features = geojson.features;

  // Pre-decode commute vectors for fast panel access
  for (const f of features) {
    const p = f.properties;
    p._cm = {
      walk:    b64ToBytes(p.cm_walk),
      bike:    b64ToBytes(p.cm_bike),
      car:     b64ToBytes(p.cm_car),
      transit: b64ToBytes(p.cm_transit),
    };
  }

  return {
    geojson,
    features,
    metadata: geojson.metadata || {},
  };
}
