/**
 * controls.js — priorities, must-haves, Rent/Buy, work location, modes, crime scope.
 *
 * Builds the state object consumed by scoring.js and notifies the app on any
 * change so it can re-score and restyle. No control is ever bound to a context/prose-only field.
 */

import { CRITERIA } from './scoring.js';
import { icon } from './icons.js';

// Human-readable criterion metadata
const META = {
  rent:    { label: 'Rent (monthly)',        units: '$/mo', thresholdLabel: 'Max rent' },
  value:   { label: 'Home value',            units: '$',    thresholdLabel: 'Max value' },
  burden:  { label: 'Housing cost stress',   units: '% stretched', thresholdLabel: 'Max % stretched on housing' },
  commute: { label: 'Commute to work',       units: 'min',  thresholdLabel: 'Max minutes' },
  transit: { label: 'Transit access',        units: 'min walk', thresholdLabel: 'Max walk min' },
  school:  { label: 'School location',       units: 'mi',   thresholdLabel: 'Max mi' },
};

// Default state (Req 8.4, 8.5): budget axis + commute must-have; thresholds "no limit".
export function defaultState() {
  return {
    mode: 'rent',
    ranks: [...CRITERIA],
    mustHave: Object.fromEntries(CRITERIA.map(k => [k, false])),
    threshold: Object.fromEntries(CRITERIA.map(k => [k, null])),
    workIdx: null,
    school: null,
    modes: { walk: true, bike: true, car: true, transit: true },
    crimeScope: 'serious',
  };
}

let state = defaultState();
let onChange = null;
let setWorkArmed = false;
let onArmWork = null;
let setSchoolArmed = false;

export function getState() { return state; }

/**
 * Wire up all controls.
 * @param {Function} changeHandler — called with the new state on any change
 * @param {Function} armWorkHandler — called when "Set work location" is toggled (bool armed)
 */
export function initControls(changeHandler, armWorkHandler) {
  onChange = changeHandler;
  onArmWork = armWorkHandler;

  // Default must-haves: active budget axis (rent) + commute
  state.mustHave.rent = true;
  state.mustHave.commute = true;

  wireModeSwitch();
  wireWorkButton();
  wireSchoolButton();
  wireCommuteModes();
  wireCrimeScope();
  renderCriteria();
}

function notify() { if (onChange) onChange(state); }

// ── Rent / Buy switch ─────────────────────────────────────────────────────────
function wireModeSwitch() {
  const rentBtn = document.getElementById('mode-rent');
  const buyBtn = document.getElementById('mode-buy');

  function setMode(mode) {
    const oldMode = state.mode;
    state.mode = mode;
    const rent = mode === 'rent';
    rentBtn.classList.toggle('active', rent);
    buyBtn.classList.toggle('active', !rent);
    rentBtn.setAttribute('aria-checked', String(rent));
    buyBtn.setAttribute('aria-checked', String(!rent));
    // Transfer the must-have flag from old axis to new axis
    if (oldMode !== mode) {
      const oldKey = oldMode === 'rent' ? 'rent' : 'value';
      const newKey = mode === 'rent' ? 'rent' : 'value';
      const oldMustHave = state.mustHave[oldKey];
      state.mustHave[oldKey] = false;
      state.mustHave[newKey] = oldMustHave;
    }
    renderCriteria();
    notify();
  }

  rentBtn.addEventListener('click', () => setMode('rent'));
  buyBtn.addEventListener('click', () => setMode('buy'));
}

// ── Work location ───────────────────────────────────────────────────────────
function wireWorkButton() {
  const btn = document.getElementById('set-work-btn');
  btn.addEventListener('click', () => {
    setWorkArmed = !setWorkArmed;
    btn.classList.toggle('armed', setWorkArmed);
    btn.textContent = setWorkArmed ? 'Click the map…' : 'Set work location';
    if (onArmWork) onArmWork(setWorkArmed);
  });
}

export function setWorkTract(idx, name) {
  state.workIdx = idx;
  const status = document.getElementById('work-status');
  status.textContent = idx === null
    ? 'No work location set.'
    : `Work location: ${name}. Commute now scored.`;
  // Disarm button
  setWorkArmed = false;
  const btn = document.getElementById('set-work-btn');
  btn.classList.remove('armed');
  btn.textContent = 'Set work location';
  renderCriteria();
  notify();
}

export function workOutOfArea() {
  const status = document.getElementById('work-status');
  status.textContent = 'That spot is outside the mapped area. Pick a place within 10 miles of Boston.';
}

export function isWorkArmed() { return setWorkArmed; }

// ── School location ─────────────────────────────────────────────────────────
function wireSchoolButton() {
  const btn = document.getElementById('set-school-btn');
  btn.addEventListener('click', () => {
    setSchoolArmed = !setSchoolArmed;
    btn.classList.toggle('armed', setSchoolArmed);
    btn.textContent = setSchoolArmed ? 'Click the map…' : 'Set school location';
  });
}

export function setSchoolLocation(lat, lon) {
  state.school = { lat, lon };
  const status = document.getElementById('school-status');
  if (status) status.textContent = 'School location set. Distance to school now scored.';
  setSchoolArmed = false;
  const btn = document.getElementById('set-school-btn');
  btn.classList.remove('armed');
  btn.textContent = 'Set school location';
  renderCriteria();
  notify();
}

export function schoolOutOfArea() {
  const status = document.getElementById('school-status');
  if (status) status.textContent = 'That spot is outside the mapped area. Pick a place within 10 miles of Boston.';
}

export function isSchoolArmed() { return setSchoolArmed; }

// ── Commute modes ─────────────────────────────────────────────────────────────
function wireCommuteModes() {
  document.querySelectorAll('#mode-checkboxes input[data-mode]').forEach(cb => {
    cb.addEventListener('change', () => {
      state.modes[cb.dataset.mode] = cb.checked;
      notify();
    });
  });
}

// ── Crime scope ───────────────────────────────────────────────────────────────
function wireCrimeScope() {
  document.querySelectorAll('input[name="crimeScope"]').forEach(radio => {
    radio.addEventListener('change', () => {
      if (radio.checked) { state.crimeScope = radio.value; notify(); }
    });
  });
}

// ── Criteria list (ranks, must-have, threshold) ──────────────────────────────
function isActive(key) {
  if (key === 'value' && state.mode === 'rent') return false;
  if (key === 'rent' && state.mode === 'buy') return false;
  if (key === 'commute' && state.workIdx === null) return false;
  if (key === 'school' && state.school === null) return false;
  return true;
}

function renderCriteria() {
  const list = document.getElementById('criteria-list');
  if (!list) return;
  list.innerHTML = '';

  state.ranks.forEach((key, i) => {
    const active = isActive(key);
    const meta = META[key];

    const li = document.createElement('li');
    li.className = 'criterion' + (active ? '' : ' inactive');
    li.dataset.key = key;

    const activeRanks = state.ranks.filter(isActive);
    const activePos = activeRanks.indexOf(key);

    li.innerHTML = `
      <div class="criterion-head">
        <span class="drag-handle" title="Drag to reorder">${icon('grip-vertical', 14)}</span>
        <span class="criterion-rank">${active ? (activePos + 1) : '—'}</span>
        <span class="criterion-name">${meta.label}</span>
        <span class="rank-btns">
          <button class="rank-up" aria-label="Move ${meta.label} up" ${activePos <= 0 || !active ? 'disabled' : ''}>▲</button>
          <button class="rank-down" aria-label="Move ${meta.label} down" ${activePos < 0 || activePos >= activeRanks.length - 1 || !active ? 'disabled' : ''}>▼</button>
        </span>
      </div>
      <div class="criterion-controls">
        <label>
          <input type="checkbox" class="must-have" ${state.mustHave[key] ? 'checked' : ''} ${active ? '' : 'disabled'} />
          Must-have
        </label>
        <label>
          ${meta.thresholdLabel}:
          <input type="number" class="threshold-input" min="0" step="any"
                 value="${state.threshold[key] ?? ''}" placeholder="no limit"
                 ${active ? '' : 'disabled'} />
          <span class="units">${meta.units}</span>
        </label>
      </div>
    `;

    // Rank up/down
    li.querySelector('.rank-up').addEventListener('click', () => moveRank(key, -1));
    li.querySelector('.rank-down').addEventListener('click', () => moveRank(key, +1));

    // Must-have
    li.querySelector('.must-have').addEventListener('change', (e) => {
      state.mustHave[key] = e.target.checked;
      notify();
    });

    // Threshold — typing a value auto-applies it as a must-have so the map reacts live
    const applyThreshold = (e) => {
      const v = e.target.value.trim();
      const num = v === '' ? null : Number(v);
      // Reject non-finite or negative numbers: store null (no limit)
      state.threshold[key] = (num !== null && Number.isFinite(num) && num >= 0) ? num : null;
      if (state.threshold[key] !== null) {
        state.mustHave[key] = true;
        const cb = li.querySelector('.must-have');
        if (cb) cb.checked = true;
      }
      notify();
    };
    li.querySelector('.threshold-input').addEventListener('input', applyThreshold);
    li.querySelector('.threshold-input').addEventListener('change', applyThreshold);

    // Drag-and-drop reorder (rank buttons remain the keyboard fallback)
    li.draggable = active;
    li.addEventListener('dragstart', (e) => {
      if (!active) { e.preventDefault(); return; }
      e.dataTransfer.setData('text/plain', key);
      e.dataTransfer.effectAllowed = 'move';
      li.classList.add('dragging');
    });
    li.addEventListener('dragend', () => {
      li.classList.remove('dragging');
      document.querySelectorAll('.criterion.drag-over').forEach(el => el.classList.remove('drag-over'));
    });
    li.addEventListener('dragover', (e) => {
      if (!active) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      li.classList.add('drag-over');
    });
    li.addEventListener('dragleave', () => li.classList.remove('drag-over'));
    li.addEventListener('drop', (e) => {
      e.preventDefault();
      li.classList.remove('drag-over');
      const fromKey = e.dataTransfer.getData('text/plain');
      if (fromKey && fromKey !== key) moveRankTo(fromKey, key);
    });

    list.appendChild(li);
  });
}

/**
 * Move a criterion up or down among the ACTIVE criteria, preserving the
 * relative order of inactive ones.
 */
function moveRank(key, delta) {
  const activeRanks = state.ranks.filter(isActive);
  const pos = activeRanks.indexOf(key);
  const newPos = pos + delta;
  if (newPos < 0 || newPos >= activeRanks.length) return;

  // Swap within active ordering
  [activeRanks[pos], activeRanks[newPos]] = [activeRanks[newPos], activeRanks[pos]];

  // Rebuild full ranks: active keys take their new order, inactive stay in place
  const inactiveKeys = state.ranks.filter(k => !isActive(k));
  // Merge: place active keys in their order, keep inactive at the end in original order
  state.ranks = [...activeRanks, ...inactiveKeys];

  renderCriteria();
  notify();
}

/**
 * Move a criterion to the position of another active criterion (drag-and-drop).
 */
function moveRankTo(fromKey, toKey) {
  if (fromKey === toKey) return;
  const activeRanks = state.ranks.filter(isActive);
  const fromIdx = activeRanks.indexOf(fromKey);
  const toIdx = activeRanks.indexOf(toKey);
  if (fromIdx < 0 || toIdx < 0) return;
  activeRanks.splice(fromIdx, 1);
  activeRanks.splice(toIdx, 0, fromKey);
  const inactiveKeys = state.ranks.filter(k => !isActive(k));
  state.ranks = [...activeRanks, ...inactiveKeys];
  renderCriteria();
  notify();
}

export { META };
