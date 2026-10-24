/**
 * panel.js — tract breakdown, context prose, next-step panel, empty state.
 *
 * All ctx_ prose is neutral, sourced, and non-evaluative (equity.md).
 */

import { META } from './controls.js';
import { clearHighlight } from './map.js';
import { icon } from './icons.js';

let nextSteps = null;

export async function loadNextSteps(url = 'data/next-steps.json') {
  try {
    const resp = await fetch(url);
    if (resp.ok) nextSteps = await resp.json();
  } catch { nextSteps = null; }
}

/** Escape text for safe insertion into innerHTML (OSM names are untrusted). */
function esc(v) {
  return String(v).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** Collapsible panel section (native <details>), same pattern as "What to do next". */
function section(title, body, { open = false, iconName = null } = {}) {
  const lead = iconName ? icon(iconName, 15, 'summary-icon') : '';
  return `<details class="collapsible ctx-block"${open ? ' open' : ''}>
    <summary>${lead}<span class="summary-title">${title}</span>${icon('chevron-down', 16, 'summary-chevron')}</summary>
    ${body}
  </details>`;
}

function fmt(v, digits = 0) {
  if (v === null || v === undefined) return '—';
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
}

/**
 * Render the tract detail panel.
 * @param {Object} feature — clicked GeoJSON feature
 * @param {Object} result  — evaluate() result for this tract
 * @param {Object} state   — current scoring state
 */
export function showPanel(feature, result, state) {
  const p = feature.properties;
  const el = document.getElementById('panel');
  const content = document.getElementById('panel-content');

  let html = `<h2>${p.name || p.geoid}</h2>
    <p class="source">${p.town} · Tract ${p.geoid}</p>`;

  // ── Score / exclusion ──────────────────────────────────────────────────────
  if (result.status === 'scored') {
    html += `<div class="score-big">${result.score.toFixed(1)}<span style="font-size:1rem;"> / 100</span></div>`;
  } else if (result.status === 'excluded') {
    html += `<p class="exclusion">Excluded — fails a must-have: ${result.failed.map(k => META[k]?.label || k).join(', ')}</p>`;
  } else if (result.status === 'nodata') {
    if (result.missing && result.missing.length > 0) {
      const labels = result.missing.map(k => META[k]?.label || k).join(', ');
      html += `<p class="exclusion">Not enough data: ${labels} has no data for this tract.</p>`;
    } else {
      html += `<p class="exclusion">Not enough data to score this tract.</p>`;
    }
  }

  // ── Breakdown table ──────────────────────────────────────────────────────────
  if (result.parts && result.parts.length) {
    html += `<table class="breakdown">
      <thead><tr><th>Criterion</th><th>Value</th><th>Subscore</th><th>Weight</th><th>Contribution</th></tr></thead>
      <tbody>`;
    for (const part of result.parts) {
      const meta = META[part.key] || { label: part.key, units: '' };
      const failed = result.failed && result.failed.includes(part.key);
      const cls = failed ? 'fail' : (part.missing ? 'missing' : '');
      let valStr;
      if (part.missing) {
        valStr = 'no data (left out of the score)';
      } else if (part.key === 'rent' && part.value >= 3501) {
        valStr = '$3,500 or more';
      } else if (part.key === 'value' && (p.value_topcoded === true || p.value_topcoded === 'true')) {
        valStr = '$2M+';
      } else {
        valStr = `${fmt(part.value, part.key === 'campus' ? 1 : 1)} ${meta.units}`;
      }
      html += `<tr class="${cls}">
        <td>${meta.label}${part.mustHave ? ' ★' : ''}${failed ? ' ✗' : ''}</td>
        <td>${valStr}</td>
        <td>${part.subscore === null ? '—' : part.subscore.toFixed(2)}</td>
        <td>${(part.weight * 100).toFixed(0)}%</td>
        <td>${part.contribution === null ? '—' : (part.contribution * 100).toFixed(1)}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
    html += `<p class="source">★ must-have · ✗ failed must-have. Subscore 1.0 = best (lowest) value in the study area.</p>`;
  }

  // ── Raw criterion notes ──────────────────────────────────────────────────────
  let about = '';
  if (state.mode === 'buy') {
    const valueTopcoded = p.value_topcoded === true || p.value_topcoded === 'true';
    if (valueTopcoded) {
      about += `<p>Median owner-estimated home value, ACS 2020–2024 5-year (B25077). Self-reported; lags market prices. Hatched tracts have high margin of error.</p><p>Values at or above $2M are grouped as "$2M+".</p>`;
    } else {
      about += `<p>Median owner-estimated home value, ACS 2020–2024 5-year (B25077). Self-reported; lags market prices. Hatched tracts have high margin of error.</p>`;
    }
  } else {
    const rentTopcoded = p.rent >= 3501;
    if (rentTopcoded) {
      about += `<p>Median gross rent, ACS 2020–2024 5-year (B25064). Rents at or above $3,500 are grouped as "$3,500 or more".</p>`;
    } else {
      about += `<p>Median gross rent, ACS 2020–2024 5-year (B25064).</p>`;
    }
  }
  // Crime note — tract-level (local PD) for Boston/Cambridge, town-level (FBI NIBRS) elsewhere
  if (p.crime_serious !== null && p.crime_serious !== undefined) {
    const town = p.town || 'the area';
    const year = p.crime_year || 'recent';
    const tl = town.toLowerCase();
    if (tl === 'boston' || tl === 'cambridge') {
      const pd = tl === 'boston' ? 'Boston Police Department' : 'Cambridge Police Department';
      about += `<p>Crime: reported incidents per 1,000 residents in this census tract, ${year}, from ${pd} incident reports. Incidents are counted where they are reported, so busy areas with few residents can show high rates.</p>`;
    } else {
      about += `<p>Crime: reported incidents per 1,000 residents, ${year}, a town-level rate from FBI NIBRS data, uniform across ${town}. YourBoston does not imply block-level precision.</p>`;
    }
  } else {
    const town = p.town || 'this area';
    about += `<p>Crime data is not available for ${town} yet.</p>`;
  }
  // Car free-flow note (Req 6.8)
  about += `<p>Car commute times are <strong>free-flow</strong> (no traffic). Transit, walking, and biking are first-class modes.</p>`;
  html += section('About these numbers', about, { iconName: 'info' });

  // ── Nearby places (name + distance only; never scored) ───────────────────────
  html += renderNearby(p);

  // ── Context prose (ctx_, never scored) ───────────────────────────────────────
  html += renderContext(p);

  // ── Next steps ─────────────────────────────────────────────────────────────
  html += renderNextSteps(p);

  content.innerHTML = html;
  el.hidden = false;
}

function renderContext(p) {
  let html = '';

  const lang = parseCtx(p.ctx_lang);
  const race = parseCtx(p.ctx_race);

  // "Who lives here" — languages spoken + race/ethnicity (context only, never scored)
  if (race && race.total_pop) {
    let body = '';
    // Languages spoken at home (C16001)
    if (lang && lang.pct_non_english !== null && lang.pct_non_english !== undefined) {
      body += `<p>About ${esc(lang.pct_non_english)}% of residents age 5+ speak a language other than English at home (estimate).</p>`;
      if (lang.top_languages && Object.keys(lang.top_languages).length) {
        body += `<p>Most common: ${Object.keys(lang.top_languages).map(esc).join(', ')}.</p>`;
      }
    }
    // Race / ethnicity
    const pct = (v) => v ? Math.round(100 * v / race.total_pop) : 0;
    body += `<p>Of about ${fmt(race.total_pop)} residents (estimate): White alone ${pct(race.white_alone)}%, Black alone ${pct(race.black_alone)}%, Asian alone ${pct(race.asian_alone)}%, Hispanic or Latino ${pct(race.hispanic)}%.</p>`;
    const langSrc = lang && lang.source ? `${esc(lang.source)} · ` : '';
    body += `<p class="source">${langSrc}${esc(race.source)}. Percentages are estimates and may not sum to 100%. Shown for context only; never used in the score or map colors.</p>`;
    html += section('Who lives here', body);
  }

  return html;
}

// Nearby categories in display order (labels are plain nouns: no ratings)
const NEARBY = [
  ['school',       'School',        'school'],
  ['library',      'Library',       'library'],
  ['park',         'Park',          'park'],
  ['supermarket',  'Grocery store', 'grocery store'],
  ['police',       'Police station', 'police station'],
  ['fire_station', 'Fire station',  'fire station'],
];

function fmtMiles(m) {
  const mi = m / 1609.344;
  if (mi < 0.1) return 'under 0.1 mi';
  return `${mi.toFixed(1)} mi`;
}

function renderNearby(p) {
  const nb = p.nearby && typeof p.nearby === 'object' ? p.nearby : null;
  let body;
  if (!nb) {
    body = `<p>Nearby places are not available for this tract.</p>`;
  } else {
    body = '<ul class="nearby-list">';
    for (const [key, label, noun] of NEARBY) {
      const e = nb[key];
      let txt;
      if (!e || !Number.isFinite(Number(e.m))) {
        txt = 'None within 10 mi';
      } else {
        const name = e.name ? esc(e.name) : `Unnamed ${noun}`;
        txt = `${name} — ${fmtMiles(Number(e.m))}`;
      }
      body += `<li><span class="nearby-label">${label}:</span> ${txt}</li>`;
    }
    body += '</ul>';
  }
  body += `<p class="source">Closest one of each type, in a straight line from the tract's center point (real walking distance is longer). Source: OpenStreetMap contributors. Names only; no ratings.</p>`;
  return section('Nearby', body);
}

function parseCtx(v) {
  if (!v) return null;
  if (typeof v === 'object') return v;
  try { return JSON.parse(v); } catch { return null; }
}

function renderNextSteps(p) {
  let html = `<div class="next-steps"><details class="collapsible"><summary><span class="summary-title">What to do next</span>${icon('chevron-down', 16, 'summary-chevron')}</summary>`;
  if (nextSteps && Array.isArray(nextSteps.actions)) {
    html += '<ul>';
    for (const a of nextSteps.actions) {
      html += `<li><a href="${a.url}" target="_blank" rel="noopener">${a.label}</a> — ${a.description}</li>`;
    }
    html += '</ul>';
    if (nextSteps.verified) html += `<p class="source">Links verified ${nextSteps.verified}.</p>`;
  } else {
    html += `<p>Next-step links are loading or unavailable.</p>`;
  }
  html += `</details></div>`;
  return html;
}

export function hidePanel() {
  const el = document.getElementById('panel');
  if (el) el.hidden = true;
  clearHighlight();
}

/**
 * Show / hide the empty state (Req 8.7).
 * @param {Object|null} info — { criterion, count } or null to hide
 */
export function showEmptyState(info) {
  const el = document.getElementById('empty-state');
  if (!el) return;
  if (!info) { el.hidden = true; return; }
  const label = META[info.criterion]?.label || info.criterion;
  el.innerHTML = `<h3>No neighborhoods pass all your must-haves</h3>
    <p>The must-have that rules out the most areas is <strong>${label}</strong> (${info.count} tracts excluded).</p>
    <p>Try raising its limit, turning off its must-have toggle, or relaxing another deal-breaker.</p>`;
  el.hidden = false;
}
