/*
 * HA CoAP cards: one plant board at a glance (HA CoAP plant), and every plant by urgency (HA CoAP
 * plants). Served by the HA CoAP integration (no resource to add). Plain JavaScript, no build step.
 *
 *   type: custom:ha-coap-plant-card
 *   device: <the board's device>     (chosen in the card's editor)
 *   name: Basil                      (optional: else the device's name)
 *   compact: false                   (a small card: the gauge, when it needs water, Water)
 *   show_history / show_readings / show_tank / show_care: true
 *
 *   type: custom:ha-coap-plants-card
 *   title: Plants                    (optional)
 *
 * They find a board's entities from its device (their translation keys), so only the device is
 * chosen: its soil as a gauge with its watering target, when it needs water next (and its forecast
 * on the soil's last two days), when it was last watered, its battery, air and temperature, the
 * pump (run time, Water, or hold Water to choose how long), and with the fleet manager its photo,
 * its water tank and its care tasks.
 */

const CARD = 'ha-coap-plant-card';
const LIST_CARD = 'ha-coap-plants-card';
const HISTORY_HOURS = 48;
const HISTORY_EVERY_MS = 15 * 60 * 1000;
const CONFIRM_MS = 4000;
const RUN_TIME_CONFIRM_MS = 8000;  // a new run time not confirmed by then: the slider goes back
const HOLD_MS = 550;               // hold Water this long: choose how long it waters
const SPLASH_MS = 1600;

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (state) => (state && !['unknown', 'unavailable', ''].includes(state.state) ? Number(state.state) : null);
const ok = (state) => Boolean(state) && !['unknown', 'unavailable'].includes(state.state);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function span(seconds) {
  const a = Math.abs(seconds);
  return a < 3600 ? `${Math.max(1, Math.round(a / 60))} min` : a < 2 * 86400 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
}

function relative(when) {
  if (!when) return '—';
  const seconds = (new Date(when).getTime() - Date.now()) / 1000;
  if (Number.isNaN(seconds)) return '—';
  if (Math.abs(seconds) < 60) return 'now';
  return seconds > 0 ? `in ${span(seconds)}` : `${span(seconds)} ago`;
}

function haptic(kind) {
  window.dispatchEvent(new CustomEvent('haptic', { detail: kind }));  // the companion app vibrates
}

function arcPoint(angle, r, cx = 50, cy = 52) {
  const rad = (angle * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function arc(from, to, r) {
  const [x1, y1] = arcPoint(from, r);
  const [x2, y2] = arcPoint(to, r);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

const START = 150;  // the gauge: 240° from bottom left to bottom right
const SWEEP = 240;
const angleOf = (pct) => START + (SWEEP * clamp(pct, 0, 100)) / 100;

/* The plant boards: devices with an HA CoAP soil sensor */
function plantDevices(hass) {
  const ids = new Set();
  for (const entity of Object.values(hass.entities || {})) {
    if (entity.platform === 'ha_coap' && entity.translation_key === 'soil_moisture' && entity.device_id) ids.add(entity.device_id);
  }
  return [...ids].map((id) => hass.devices?.[id]).filter(Boolean)
    .sort((a, b) => (a.name_by_user || a.name || '').localeCompare(b.name_by_user || b.name || ''));
}

/* translation key → entity ID, for one board's device (and the Plant care list) */
function entitiesOf(hass, deviceId) {
  const found = {};
  for (const entity of Object.values(hass.entities || {})) {
    if (entity.platform !== 'ha_coap') continue;
    if (entity.device_id === deviceId && entity.translation_key) found[entity.translation_key] = entity.entity_id;
    if (entity.entity_id.startsWith('todo.')) found.plant_care = entity.entity_id;
  }
  return found;
}

/* How the plant is: {key, text, score} (score: the most urgent first) */
function moodOf(hass, ids) {
  const s = (key) => (ids[key] ? hass.states[ids[key]] : undefined);
  const soil = num(s('soil_moisture'));
  const connectivity = s('connectivity');
  const online = connectivity ? connectivity.state === 'on' : ok(s('soil_moisture'));
  if (!online) {
    const since = connectivity?.last_changed ? ` · ${span((Date.now() - new Date(connectivity.last_changed)) / 1000)}` : '';
    return { key: 'offline', text: `Not answering${since}`, score: 4 };
  }
  if (s('pump_running')?.state === 'on') return { key: 'watering', text: 'Watering', score: 1 };
  if (s('needs_water')?.state === 'on') return { key: 'thirsty', text: 'Thirsty', score: 0 };
  if (soil !== null && soil > 85) return { key: 'wet', text: 'Soaked', score: 6 };
  return { key: 'happy', text: 'Happy', score: 5 };
}

/* A small drawn plant: upright when its soil is fine, drooping as it dries */
function plantDrawing(wilt, cls = '') {
  const w = clamp(wilt, 0, 1);
  const tipX = 12 + 4.5 * w;
  const tipY = 6.5 + 5 * w;
  const stem = `M12 19 Q${(12 + 1.5 * w).toFixed(2)} ${(12 - w).toFixed(2)} ${tipX.toFixed(2)} ${tipY.toFixed(2)}`;
  const leaf = (angle, x, y) => `<path d="M0 0 C 2.5 -3.2, 6.2 -3.2, 7 0 C 6.2 2.2, 2.5 2.2, 0 0 Z" transform="translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${angle.toFixed(1)})" class="leaf"/>`;
  return `<svg class="plant ${cls}" viewBox="0 0 24 24" aria-hidden="true">
    <path d="${stem}" class="stem"/>
    ${leaf(-150 + 95 * w, 12.4 + 0.8 * w, 13.5 - 0.5 * w)}
    ${leaf(-35 + 80 * w, 12.6 + 1.1 * w, 11 + 0.6 * w)}
    ${leaf(-80 + 120 * w, tipX, tipY)}
    <path d="M7.5 19 h9 l-1.3 4 h-6.4 Z" class="pot"/>
  </svg>`;
}

const wiltOf = (soil, target) => (soil === null ? 0.35 : clamp((target + 12 - soil) / 24, 0, 1));

class HaCoapPlantCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement(`${CARD}-editor`);
  }

  static getStubConfig(hass) {
    const first = plantDevices(hass)[0];
    return { device: first ? first.id : '' };
  }

  setConfig(config) {
    if (!config) throw new Error('No configuration');
    this._config = { show_history: true, show_readings: true, show_tank: true, show_care: true, ...config };
    this._signature = null;
    this._history = null;
    this._historyAt = 0;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return this._config?.compact ? 3 : 7;
  }

  getGridOptions() {
    return this._config?.compact ? { columns: 6, min_columns: 4, rows: 'auto' } : { columns: 12, min_columns: 6, rows: 'auto' };
  }

  connectedCallback() {
    this._tick = setInterval(() => { this._signature = null; this._render(); }, 60 * 1000);  // "in 5 h" moves on
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  _state(key) {
    const id = this._ids[key];
    return id ? this._hass.states[id] : undefined;
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    if (this._dragging || this._holding) return;  // not redrawn under the finger
    this._ids = entitiesOf(this._hass, this._config.device);
    this._noticeWatering();
    const signature = JSON.stringify([this._config, this._confirming, this._pendingRunTime, this._choosing, this._splash,
      this._history && this._history.length, Object.values(this._ids).map((id) => this._hass.states[id]?.last_updated)]);
    if (signature === this._signature) return;
    this._signature = signature;
    const device = this._hass.devices?.[this._config.device];
    if (!this._config.device || !device || !this._ids.soil_moisture) {
      this.shadowRoot.innerHTML = `${STYLE}<ha-card><div class="empty">
        ${plantDrawing(0.4, 'empty-plant')}
        <div>${this._config.device ? 'This plant board isn\'t in Home Assistant any more.' : 'Choose a plant board in the card\'s settings.'}</div>
      </div></ha-card>`;
      return;
    }
    if (!this._config.compact && this._config.show_history !== false) this._maybeLoadHistory();
    const soil = num(this._state('soil_moisture'));
    const from = this._shownSoil ?? (soil !== null ? 0 : null);
    this.shadowRoot.innerHTML = STYLE + this._html(device, from);
    this._wire();
    this._sweep(from, soil);
  }

  /* A watering that just ended (here or anywhere: its Last watered moved): a splash, once */
  _noticeWatering() {
    const last = this._ids.last_watered ? this._hass.states[this._ids.last_watered]?.state : null;
    const running = this._ids.pump_running ? this._hass.states[this._ids.pump_running]?.state === 'on' : false;
    const ended = (this._wasRunning && !running) || (this._lastWatered !== undefined && last && last !== this._lastWatered);
    this._wasRunning = running;
    this._lastWatered = last;
    if (ended && !this._splash) {
      this._splash = true;
      setTimeout(() => { this._splash = false; this._signature = null; this._render(); }, SPLASH_MS);
    }
  }

  /* The gauge sweeps from what it showed to the new reading */
  _sweep(from, to) {
    const value = this.shadowRoot.querySelector('.value');
    this._shownSoil = to;
    if (!value || to === null || from === to) return;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      value.style.strokeDasharray = `${clamp(to, 0.5, 100)} 100`;
    }));
  }

  _html(device, from) {
    const compact = Boolean(this._config.compact);
    const soil = num(this._state('soil_moisture'));
    const target = num(this._state('water_below')) ?? 30;
    const needs = this._state('needs_water');
    const running = this._state('pump_running')?.state === 'on';
    const mood = moodOf(this._hass, this._ids);
    const online = mood.key !== 'offline';
    const area = device.area_id ? this._hass.areas?.[device.area_id]?.name : '';
    const name = this._config.name || device.name_by_user || device.name || 'Plant';
    const photo = this._state('plant_photo');
    const picture = ok(photo) ? photo.attributes.entity_picture : null;

    // Header badges: a low tank, care due
    const badges = [];
    if (this._state('water_tank_low')?.state === 'on') badges.push(['badge-tank', 'mdi:cup-water', 'Tank low']);
    const due = num(this._state('care_due'));
    if (due) badges.push(['badge-care', 'mdi:sprout', `${due} care task${due === 1 ? '' : 's'}`]);

    // The gauge: its soil, with the target (Water below) marked
    const color = soil === null ? 'var(--disabled-text-color, #9e9e9e)'
      : soil < target ? 'var(--warning-color, #ffa600)'
        : soil > 85 ? 'var(--info-color, #039be5)' : 'var(--success-color, #43a047)';
    const [tx1, ty1] = arcPoint(angleOf(target), 34);
    const [tx2, ty2] = arcPoint(angleOf(target), 48);
    const start = from === null ? (soil ?? 0) : from;
    const gauge = `
      ${running ? `<div class="drops">${[0, 1, 2, 3, 4, 5].map((i) => `<i style="--i:${i}"></i>`).join('')}</div>` : ''}
      <svg viewBox="0 0 100 92" class="gauge-svg" aria-hidden="true">
        <path d="${arc(START, START + SWEEP, 41)}" class="track"/>
        ${soil !== null ? `<path d="${arc(START, START + SWEEP, 41)}" pathLength="100" class="value"
          style="stroke:${color};stroke-dasharray:${clamp(start, 0.5, 100)} 100"/>` : ''}
        <line x1="${tx1.toFixed(2)}" y1="${ty1.toFixed(2)}" x2="${tx2.toFixed(2)}" y2="${ty2.toFixed(2)}" class="target"/>
      </svg>
      <div class="gauge-text">
        <div class="big">${soil !== null ? `${Math.round(soil)}<span>%</span>` : '—'}</div>
        <div class="small">water below ${Math.round(target)} %</div>
      </div>
      ${this._splash ? '<div class="splash"><b></b><b></b><b></b></div>' : ''}`;

    const next = this._state('next_watering');
    const last = this._state('last_watered');
    const used = this._state('water_used');
    const lasts = this._batteryLasts();
    const facts = [
      ['mdi:calendar-clock', 'Needs water', needs?.state === 'on' ? 'now' : ok(next) ? relative(next.state) : '—', 'next_watering'],
      ['mdi:watering-can-outline', 'Last watered', ok(last) ? relative(last.state) : '—', 'last_watered'],
      ...(compact ? [] : [['mdi:cup-water', 'Water used', ok(used) ? `${Number(used.state).toFixed(2)} ${esc(used.attributes.unit_of_measurement || 'L')}` : '—', 'water_used']]),
      ...(lasts ? [[lasts.icon, 'Battery', lasts.text, lasts.key]] : []),
    ].map(([icon, label, value, key]) => `
      <button class="fact" data-more="${esc(this._ids[key] || '')}">
        <ha-icon icon="${icon}"></ha-icon><span class="label">${label}</span><span class="v">${esc(value)}</span>
      </button>`).join('');

    const show = (key) => !compact && this._config[key] !== false;
    return `<ha-card class="${mood.key} ${compact ? 'compact' : ''}">
      ${picture && !compact ? `<div class="backdrop" style="background-image:url('${esc(picture)}')"></div>` : ''}
      <div class="top">
        <button class="title" data-more="${esc(this._ids.soil_moisture)}">
          ${picture ? `<img class="avatar" src="${esc(picture)}" alt="">` : `<span class="avatar drawn">${plantDrawing(wiltOf(soil, target))}</span>`}
          <span class="title-text">
            <span class="name">${esc(name)}</span>
            ${area || badges.length ? `<span class="area">${esc(area)}${badges.map(([cls, icon, text]) =>
              `<span class="badge ${cls}"><ha-icon icon="${icon}"></ha-icon>${esc(text)}</span>`).join('')}</span>` : ''}
          </span>
        </button>
        <div class="mood ${mood.key}">${plantDrawing(mood.key === 'offline' ? 0.5 : wiltOf(soil, target), 'mood-plant')}<span>${esc(mood.text)}</span></div>
      </div>
      <div class="main">
        <button class="gauge" data-more="${esc(this._ids.soil_moisture)}">${gauge}</button>
        <div class="facts">${facts}</div>
      </div>
      ${show('show_history') ? this._spark(target, soil) : ''}
      ${show('show_readings') ? this._chips() : ''}
      ${show('show_tank') ? this._tank() : ''}
      ${this._pump(running, online, compact)}
      ${show('show_care') ? this._care() : ''}
    </ha-card>`;
  }

  /* Its soil over the last two days, and where it's heading: a dashed line to when it goes under
   * its target (Next watering), "needs water in 1.5 d" */
  _spark(target, soil) {
    const points = (this._history || []).filter((p) => !Number.isNaN(p[1]));
    if (points.length < 2) return '';
    const now = Date.now();
    const next = this._state('next_watering');
    const nextAt = ok(next) && this._state('needs_water')?.state !== 'on' ? new Date(next.state).getTime() : null;
    const ahead = nextAt && nextAt > now ? Math.min(nextAt - now, HISTORY_HOURS * 3600 * 1000) : 0;
    const t0 = now - HISTORY_HOURS * 3600 * 1000;
    const total = HISTORY_HOURS * 3600 * 1000 + ahead;
    const x = (t) => clamp(((t - t0) / total) * 300, 0, 300);
    const values = points.map((point) => point[1]).concat([target]);
    const lo = Math.max(0, Math.min(...values) - 5);
    const hi = Math.min(100, Math.max(...values) + 5);
    const y = (v) => 46 - ((clamp(v, lo, hi) - lo) / Math.max(1, hi - lo)) * 42;
    const line = points.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const lastPoint = points[points.length - 1];
    const area = `0,48 ${line} ${x(lastPoint[0]).toFixed(1)},48`;
    let forecast = '';
    if (ahead) {
      // To the target at Next watering (cut where the chart ends, if that's further)
      const startValue = soil ?? lastPoint[1];
      const endAt = now + ahead;
      const endValue = startValue + ((target - startValue) * ahead) / (nextAt - now);
      forecast = `<line x1="${x(now).toFixed(1)}" y1="${y(startValue).toFixed(1)}" x2="${x(endAt).toFixed(1)}" y2="${y(endValue).toFixed(1)}" class="spark-forecast"/>
        ${nextAt <= endAt ? `<circle cx="${x(nextAt).toFixed(1)}" cy="${y(target).toFixed(1)}" r="3" class="spark-dot"/>` : ''}`;
    }
    const nowPct = ((x(now) / 300) * 100).toFixed(1);
    return `<div class="spark" title="Soil, last ${HISTORY_HOURS} hours${ahead ? ', and where it\'s heading' : ''}">
      <svg viewBox="0 0 300 48" preserveAspectRatio="none" aria-hidden="true">
        <defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="var(--primary-color, #03a9f4)" stop-opacity="0.28"/>
          <stop offset="1" stop-color="var(--primary-color, #03a9f4)" stop-opacity="0"/></linearGradient></defs>
        <line x1="0" x2="300" y1="${y(target).toFixed(1)}" y2="${y(target).toFixed(1)}" class="spark-target"/>
        ${ahead ? `<line x1="${x(now).toFixed(1)}" x2="${x(now).toFixed(1)}" y1="0" y2="48" class="spark-now"/>` : ''}
        <polygon points="${area}" fill="url(#fill)"/>
        <polyline points="${line}" class="spark-line"/>
        ${forecast}
      </svg>
      <div class="spark-labels"><span>${HISTORY_HOURS} h ago</span>
        ${ahead ? `<span class="now-label" style="left:${nowPct}%">now</span><span class="ahead">needs water ${esc(relative(nextAt))}</span>` : '<span>now</span>'}
      </div>
    </div>`;
  }

  /* How long its battery lasts (Battery days left, from the fleet manager), or that it's on USB:
   * {icon, text, key}; null when not known */
  _batteryLasts() {
    if (this._state('charging')?.state === 'on') return { icon: 'mdi:battery-charging', text: 'charging', key: 'charging' };
    if (this._state('plugged_in')?.state === 'on') return { icon: 'mdi:power-plug', text: 'on USB', key: 'plugged_in' };
    const days = num(this._state('battery_days_left'));
    if (days === null) return null;
    return { icon: 'mdi:battery-clock', text: days >= 60 ? `≈ ${Math.round(days / 30)} months left` : `≈ ${Math.round(days)} days left`,
      key: 'battery_days_left' };
  }

  _chips() {
    const chips = [];
    const battery = this._state('battery');
    const charging = this._state('charging')?.state === 'on';
    const plugged = this._state('plugged_in')?.state === 'on';
    if (battery) {
      const pct = num(battery);
      const icon = charging ? 'mdi:battery-charging' : pct === null ? 'mdi:battery-unknown'
        : `mdi:battery${pct >= 95 ? '' : `-${Math.max(10, Math.round(pct / 10) * 10)}`}`;
      const extra = charging ? 'charging' : plugged ? 'on USB' : '';  // how long it lasts: in the facts
      chips.push([icon, pct !== null ? `${Math.round(pct)} %` : '—', extra, 'battery', pct !== null && pct < 20 && !plugged ? 'bad' : '']);
    }
    const temperature = this._state('temperature');
    if (temperature) {
      chips.push(['mdi:thermometer', ok(temperature) ? `${Number(temperature.state).toFixed(1)} ${temperature.attributes.unit_of_measurement || '°C'}` : '—', '', 'temperature', '']);
    }
    const air = this._state('air_humidity');
    if (air) chips.push(['mdi:water-percent', ok(air) ? `${Math.round(Number(air.state))} %` : '—', 'air', 'air_humidity', '']);
    if (!chips.length) return '';
    return `<div class="chips">${chips.map(([icon, value, extra, key, cls]) => `
      <button class="chip ${cls}" data-more="${esc(this._ids[key] || '')}"><ha-icon icon="${icon}"></ha-icon>${esc(value)}${extra ? `<span>${esc(extra)}</span>` : ''}</button>`).join('')}</div>`;
  }

  _tank() {
    const tank = this._state('water_tank');
    if (!tank || !ok(tank)) return '';
    const pct = num(tank);
    const low = this._state('water_tank_low')?.state === 'on';
    const days = num(this._state('water_tank_days_left'));
    const left = tank.attributes.left_ml;
    const confirming = this._confirming === 'refill';
    return `<div class="tank ${low ? 'low' : ''}">
      <ha-icon icon="${low ? 'mdi:water-alert' : 'mdi:cup-water'}"></ha-icon>
      <button class="tank-info" data-more="${esc(this._ids.water_tank)}">
        <div class="tank-text"><span>Water tank</span><span>${Math.round(pct)} %${left != null ? ` · ${Math.round(left)} ml` : ''}${days !== null ? ` · ${Math.round(days)} d left` : ''}</span></div>
        <div class="bar"><div style="width:${clamp(pct, 2, 100)}%"></div></div>
      </button>
      ${this._ids.water_tank_refilled ? `<button class="small-btn ${confirming ? 'confirm' : ''}" data-action="refill">${confirming ? 'Sure? Tap again' : 'Refilled'}</button>` : ''}
    </div>`;
  }

  _pump(running, online, compact) {
    const runTime = this._state('pump_run_time');
    if (!this._ids.pump && !runTime) return '';
    const seconds = num(runTime) ?? 3;
    const confirming = this._confirming === 'water';
    const disabled = !online || running || !this._ids.pump;
    if (this._choosing) {
      // Water held: for how long, this time only (its run time doesn't change)
      return `<div class="pump choose">
        <span class="label"><ha-icon icon="mdi:watering-can"></ha-icon><span>Water for how long? (this time only)</span></span>
        <button class="small-btn" data-action="choose-cancel">Cancel</button>
        <span class="seconds">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button class="sec ${n === Math.round(seconds) ? 'usual' : ''}" data-action="water-for" data-seconds="${n}">${n}<small>s</small></button>`).join('')}</span>
      </div>`;
    }
    if (this._pendingRunTime != null) {
      // A new run time, asked before it's set
      return `<div class="pump runtime-confirm">
        <span class="label"><ha-icon icon="mdi:timer-outline"></ha-icon><span>Change the run time to <b>${this._pendingRunTime}&nbsp;s</b>?</span></span>
        <span class="confirm-buttons">
          <button class="small-btn" data-action="runtime-cancel">Cancel</button>
          <button class="water" data-action="runtime-set">Change</button>
        </span>
      </div>`;
    }
    const water = `<button class="water ${confirming ? 'confirm' : ''} ${running ? 'running' : ''}" data-action="water" ${disabled ? 'disabled' : ''}
        title="Tap twice to water for its run time; hold to choose how long">
        <ha-icon icon="mdi:watering-can"></ha-icon>${running ? 'Watering…' : confirming ? `Water ${Math.round(seconds)} s?` : 'Water'}
      </button>`;
    if (compact) return `<div class="pump compact-pump">${water}</div>`;
    return `<div class="pump">
      ${runTime ? `<label class="runtime">
        <span class="label"><ha-icon icon="mdi:timer-outline"></ha-icon>Run time <b>${Math.round(seconds)} s</b></span>
        <input type="range" min="1" max="9" step="1" value="${Math.round(seconds)}" ${ok(runTime) ? '' : 'disabled'} aria-label="Pump run time"/>
      </label>` : '<span></span>'}
      ${water}
    </div>`;
  }

  _care() {
    const care = this._state('care_due');
    const tasks = care?.attributes?.tasks || [];
    if (!tasks.length) return '';
    const due = tasks.filter((t) => t.due_now);
    const next = tasks.find((t) => !t.due_now);
    return `<div class="care">
      ${due.map((t) => `<button class="task" data-action="done" data-uid="${esc(t.uid)}">
        <ha-icon icon="mdi:checkbox-blank-circle-outline"></ha-icon>
        <span class="what">${esc(t.what)}</span><span class="when">due ${esc(relative(t.due))}</span></button>`).join('')}
      ${next ? `<div class="next"><ha-icon icon="mdi:sprout"></ha-icon>Next: ${esc(next.what)} ${esc(relative(next.due))}</div>` : ''}
    </div>`;
  }

  _wire() {
    const root = this.shadowRoot;
    root.querySelectorAll('[data-more]').forEach((el) => el.addEventListener('click', () => {
      if (el.dataset.more) this._fire('hass-more-info', { entityId: el.dataset.more });
    }));
    const slider = root.querySelector('input[type=range]');
    if (slider) {
      const label = root.querySelector('.runtime b');
      slider.addEventListener('pointerdown', () => { this._dragging = true; });
      slider.addEventListener('input', () => { label.textContent = `${slider.value} s`; });
      slider.addEventListener('change', () => {
        this._dragging = false;
        const value = Number(slider.value);
        if (value === Math.round(num(this._state('pump_run_time')) ?? 0)) return;  // back where it was
        this._pendingRunTime = value;  // asked first: "Change the run time to 6 s?"
        haptic('light');
        clearTimeout(this._runTimeTimer);
        this._runTimeTimer = setTimeout(() => this._cancelRunTime(), RUN_TIME_CONFIRM_MS);
        this._signature = null;
        this._render();
      });
      slider.addEventListener('pointerup', () => { setTimeout(() => { this._dragging = false; }, 300); });
    }
    // Water: tap twice, or hold to choose how long
    const water = root.querySelector('button[data-action=water]');
    if (water && !water.disabled) {
      water.addEventListener('pointerdown', () => {
        this._held = false;
        clearTimeout(this._holdTimer);
        this._holding = true;
        this._holdTimer = setTimeout(() => {
          this._held = true;
          this._holding = false;
          this._choosing = true;
          this._confirming = null;
          haptic('medium');
          this._signature = null;
          this._render();
        }, HOLD_MS);
      });
      const release = () => {
        clearTimeout(this._holdTimer);
        if (this._holding) {
          this._holding = false;
          this._signature = null;
        }
      };
      water.addEventListener('pointerup', release);
      water.addEventListener('pointerleave', release);
      water.addEventListener('contextmenu', (e) => e.preventDefault());
    }
    root.querySelectorAll('[data-action]').forEach((el) => el.addEventListener('click', () => this._action(el)));
  }

  _cancelRunTime() {
    clearTimeout(this._runTimeTimer);
    this._pendingRunTime = null;
    this._signature = null;
    this._render();
  }

  async _action(el) {
    const action = el.dataset.action;
    if (action === 'water' && this._held) {  // the click after a hold: the choice is open
      this._held = false;
      return;
    }
    if (action === 'runtime-cancel') {
      this._cancelRunTime();
      return;
    }
    if (action === 'runtime-set') {
      const value = this._pendingRunTime;
      this._cancelRunTime();
      if (value != null) {
        haptic('success');
        await this._call('number', 'set_value', { entity_id: this._ids.pump_run_time, value });
      }
      return;
    }
    if (action === 'choose-cancel' || action === 'water-for') {
      this._choosing = false;
      this._signature = null;
      this._render();
      if (action === 'water-for') {
        haptic('success');
        await this._call('ha_coap', 'water', { entity_id: this._ids.pump, seconds: Number(el.dataset.seconds) });
      }
      return;
    }
    if (action === 'done') {
      el.classList.add('done');
      haptic('success');
      await this._call('todo', 'update_item', { entity_id: this._ids.plant_care, item: el.dataset.uid, status: 'completed' });
      return;
    }
    // Water and Refilled: a second tap confirms (a plant isn't watered by a stray tap)
    if (this._confirming !== action) {
      this._confirming = action;
      haptic('light');
      clearTimeout(this._confirmTimer);
      this._confirmTimer = setTimeout(() => { this._confirming = null; this._signature = null; this._render(); }, CONFIRM_MS);
      this._signature = null;
      this._render();
      return;
    }
    this._confirming = null;
    clearTimeout(this._confirmTimer);
    this._signature = null;
    haptic('success');
    const entity = action === 'water' ? this._ids.pump : this._ids.water_tank_refilled;
    await this._call('button', 'press', { entity_id: entity });
    this._render();
  }

  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      haptic('failure');
      this._fire('hass-notification', { message: err.message || String(err) });
      this._signature = null;
      this._render();
    }
  }

  _fire(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
  }

  /* Its soil over the last two days, from the recorder (every 15 minutes, or when it changed) */
  async _maybeLoadHistory() {
    const soilId = this._ids.soil_moisture;
    const changed = this._hass.states[soilId]?.last_updated;
    if (this._loadingHistory || (Date.now() - this._historyAt < HISTORY_EVERY_MS && changed === this._historyFor)) return;
    this._loadingHistory = true;
    this._historyAt = Date.now();
    this._historyFor = changed;
    try {
      const start = new Date(Date.now() - HISTORY_HOURS * 3600 * 1000).toISOString();
      const result = await this._hass.callWS({ type: 'history/history_during_period', start_time: start, entity_ids: [soilId],
        minimal_response: true, no_attributes: true, significant_changes_only: false });
      this._history = (result[soilId] || []).map((p) => [(p.lu ?? p.lc) * 1000, Number(p.s)]).filter((p) => !Number.isNaN(p[1]));
    } catch (err) {
      this._history = [];
    } finally {
      this._loadingHistory = false;
      this._signature = null;
      this._render();
    }
  }
}

/* Every plant, the most urgent first: thirsty, watering, its tank low, care due, not answering,
 * then by when it needs water. Tap one for its soil. */
class HaCoapPlantsCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement(`${LIST_CARD}-editor`);
  }

  static getStubConfig() {
    return { title: 'Plants' };
  }

  setConfig(config) {
    this._config = { title: 'Plants', ...(config || {}) };
    this._signature = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 1 + plantDevices(this._hass || { entities: {} }).length;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 'auto' };
  }

  connectedCallback() {
    this._tick = setInterval(() => { this._signature = null; this._render(); }, 60 * 1000);
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  _plants() {
    const hass = this._hass;
    return plantDevices(hass).map((device) => {
      const ids = entitiesOf(hass, device.id);
      const s = (key) => (ids[key] ? hass.states[ids[key]] : undefined);
      const mood = moodOf(hass, ids);
      const soil = num(s('soil_moisture'));
      const target = num(s('water_below')) ?? 30;
      const next = s('next_watering');
      const nextAt = ok(next) ? new Date(next.state).getTime() : Infinity;
      const tankLow = s('water_tank_low')?.state === 'on';
      const care = num(s('care_due')) || 0;
      let score = mood.score;
      let line = mood.text;
      if (mood.key === 'thirsty') line = 'Thirsty: water it';
      else if (mood.key === 'happy' || mood.key === 'wet') {
        if (tankLow) { score = 2; line = `Tank ${Math.round(num(s('water_tank')) ?? 0)} %: refill it`; }
        else if (care) { score = 3; line = `${care} care task${care === 1 ? '' : 's'} due`; }
        else line = `${mood.text}${ok(next) ? ` · needs water ${relative(next.state)}` : ''}`;
      }
      const photo = s('plant_photo');
      return { device, ids, mood, soil, target, score, nextAt, line, picture: ok(photo) ? photo.attributes.entity_picture : null };
    }).sort((a, b) => a.score - b.score || a.nextAt - b.nextAt
      || (a.device.name_by_user || a.device.name).localeCompare(b.device.name_by_user || b.device.name));
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    const plants = this._plants();
    const signature = JSON.stringify([this._config, plants.map((p) => [p.device.id, p.line, p.soil, p.score, p.picture])]);
    if (signature === this._signature) return;
    this._signature = signature;
    const counts = {};
    for (const p of plants) counts[p.score] = (counts[p.score] || 0) + 1;
    const summary = [[0, 'thirsty'], [2, 'tank low'], [3, 'care due'], [4, 'not answering']]
      .filter(([score]) => counts[score]).map(([score, text]) => `${counts[score]} ${text}`).join(' · ') || 'All fine';
    this.shadowRoot.innerHTML = `${STYLE}${LIST_STYLE}<ha-card>
      <div class="list-head"><span class="list-title">${esc(this._config.title)}</span><span class="list-summary">${esc(summary)}</span></div>
      ${plants.length ? '' : '<div class="empty">No HA CoAP plant board yet.</div>'}
      ${plants.map((p) => {
        const color = p.soil === null ? 'var(--disabled-text-color, #9e9e9e)' : p.soil < p.target ? 'var(--warning-color, #ffa600)'
          : p.soil > 85 ? 'var(--info-color, #039be5)' : 'var(--success-color, #43a047)';
        const ring = p.soil === null ? 0 : clamp(p.soil, 0, 100);
        return `<button class="row ${p.mood.key} score-${p.score}" data-more="${esc(p.ids.soil_moisture || '')}">
          ${p.picture ? `<img class="avatar" src="${esc(p.picture)}" alt="">` : `<span class="avatar drawn">${plantDrawing(p.mood.key === 'offline' ? 0.5 : wiltOf(p.soil, p.target))}</span>`}
          <span class="row-text"><span class="row-name">${esc(p.device.name_by_user || p.device.name)}</span><span class="row-line">${esc(p.line)}</span></span>
          <span class="ring" style="--v:${ring};--c:${color}"><span>${p.soil === null ? '—' : `${Math.round(p.soil)}%`}</span></span>
        </button>`;
      }).join('')}
    </ha-card>`;
    this.shadowRoot.querySelectorAll('[data-more]').forEach((el) => el.addEventListener('click', () => {
      if (el.dataset.more) this.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId: el.dataset.more }, bubbles: true, composed: true }));
    }));
  }
}

const EDITOR_STYLE = `<style>
  .editor { display: grid; gap: 12px; padding: 4px 0; }
  label { display: grid; gap: 4px; font-size: 14px; color: var(--primary-text-color); }
  label.check { display: flex; align-items: center; gap: 8px; }
  select, input:not([type=checkbox]) { font: inherit; padding: 10px 12px; border-radius: 8px; color: var(--primary-text-color);
    background: var(--secondary-background-color, #f5f5f5); border: 1px solid var(--divider-color, #ddd); }
  .checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
  .hint { font-size: 12px; color: var(--secondary-text-color); }
</style>`;

class HaCoapPlantCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    const devices = plantDevices(this._hass);
    const check = (key, label, fallback = true) => `<label class="check"><input type="checkbox" data-key="${key}"
      ${(this._config[key] ?? fallback) ? 'checked' : ''}/>${label}</label>`;
    this.innerHTML = `${EDITOR_STYLE}
      <div class="editor">
        <label>Plant board
          <select id="device">
            <option value="">Choose…</option>
            ${devices.map((d) => `<option value="${esc(d.id)}" ${d.id === this._config.device ? 'selected' : ''}>${esc(d.name_by_user || d.name)}</option>`).join('')}
          </select>
        </label>
        <label>Name <span class="hint">(optional: else the device's name)</span>
          <input id="name" value="${esc(this._config.name || '')}" placeholder="${esc((devices.find((d) => d.id === this._config.device) || {}).name_by_user || '')}"/>
        </label>
        <div class="checks">
          ${check('compact', 'Compact', false)}
          ${check('show_history', 'Soil history')}
          ${check('show_readings', 'Battery, air, temperature')}
          ${check('show_tank', 'Water tank')}
          ${check('show_care', 'Care tasks')}
        </div>
        <div class="hint">Compact: the gauge, when it needs water, and Water. Hold Water to choose how long it waters.</div>
        ${devices.length ? '' : '<div class="hint">No HA CoAP plant board yet.</div>'}
      </div>`;
    this.querySelector('#device').addEventListener('change', (e) => this._changed({ device: e.target.value }));
    this.querySelector('#name').addEventListener('change', (e) => this._changed({ name: e.target.value.trim() || undefined }));
    this.querySelectorAll('input[type=checkbox]').forEach((box) => box.addEventListener('change', () => {
      const fallback = box.dataset.key !== 'compact';
      this._changed({ [box.dataset.key]: box.checked === fallback ? undefined : box.checked });
    }));
  }

  _changed(values) {
    this._config = { ...this._config, ...values };
    Object.keys(this._config).forEach((k) => this._config[k] === undefined && delete this._config[k]);
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config }, bubbles: true, composed: true }));
  }
}

class HaCoapPlantsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
  }

  _render() {
    this.innerHTML = `${EDITOR_STYLE}<div class="editor">
      <label>Title <input id="title" value="${esc(this._config.title ?? 'Plants')}"/></label>
      <div class="hint">Every HA CoAP plant, the most urgent first. Tap one for its soil.</div></div>`;
    this.querySelector('#title').addEventListener('change', (e) => {
      this._config = { ...this._config, title: e.target.value };
      this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config }, bubbles: true, composed: true }));
    });
  }
}

const STYLE = `<style>
  :host { --plant-accent: var(--success-color, #43a047); }
  ha-card { padding: 16px; display: flex; flex-direction: column; gap: 14px; overflow: hidden; position: relative; isolation: isolate; }
  ha-card.thirsty { --plant-accent: var(--warning-color, #ffa600); }
  ha-card.watering, ha-card.wet { --plant-accent: var(--info-color, #039be5); }
  ha-card.offline { --plant-accent: var(--error-color, #db4437); }
  ha-card::before { content: ''; position: absolute; inset: 0 0 auto 0; height: 4px; background: var(--plant-accent); opacity: .85; z-index: 1; }
  .backdrop { position: absolute; inset: 0 0 auto 0; height: 230px; z-index: -1; background-size: cover; background-position: center;
    filter: blur(22px) saturate(1.2); opacity: .22; transform: scale(1.2);
    -webkit-mask-image: linear-gradient(#000 30%, transparent); mask-image: linear-gradient(#000 30%, transparent); }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; text-align: inherit; cursor: pointer; }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 8px; }
  ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .empty { display: flex; gap: 12px; align-items: center; color: var(--secondary-text-color); padding: 8px 0; }
  .empty-plant { width: 40px; height: 40px; }
  .plant .stem { fill: none; stroke: #6d8f3a; stroke-width: 1.4; stroke-linecap: round; }
  .plant .leaf { fill: #7cb342; }
  .plant .pot { fill: #c47c4a; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .avatar { width: 46px; height: 46px; border-radius: 50%; object-fit: cover; flex: none;
    box-shadow: 0 0 0 2px var(--card-background-color, #fff), 0 0 0 4px var(--plant-accent); }
  .avatar.drawn { display: grid; place-items: center; background: color-mix(in srgb, var(--plant-accent) 12%, transparent); }
  .avatar.drawn .plant { width: 34px; height: 34px; }
  .title-text { display: grid; min-width: 0; }
  .name { font-size: 20px; font-weight: 600; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .area { font-size: 13px; color: var(--secondary-text-color); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .badge { display: inline-flex; align-items: center; gap: 3px; padding: 1px 8px 1px 6px; border-radius: 999px; font-size: 12px; font-weight: 500; white-space: nowrap;
    color: var(--warning-color, #ffa600); background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent); }
  .badge ha-icon { --mdc-icon-size: 14px; color: inherit; }
  .mood { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px 3px 6px; border-radius: 999px; font-size: 13px;
    font-weight: 500; white-space: nowrap; color: var(--plant-accent);
    background: color-mix(in srgb, var(--plant-accent) 14%, transparent); }
  .mood .plant { width: 22px; height: 22px; }
  .mood.watering .plant { animation: sway 1.6s ease-in-out infinite; transform-origin: 50% 90%; }
  @keyframes sway { 50% { transform: rotate(-6deg); } }
  .main { display: grid; grid-template-columns: minmax(120px, 160px) 1fr; gap: 12px; align-items: center; }
  .gauge { position: relative; display: block; width: 100%; }
  .gauge-svg { width: 100%; display: block; }
  .track { fill: none; stroke: var(--divider-color, rgba(127,127,127,.25)); stroke-width: 9; stroke-linecap: round; }
  .value { fill: none; stroke-width: 9; stroke-linecap: round; transition: stroke-dasharray 1.1s cubic-bezier(.2,.8,.2,1), stroke .4s; }
  .target { stroke: var(--primary-text-color); stroke-width: 2; stroke-linecap: round; opacity: .7; }
  .gauge-text { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; padding-top: 6px; }
  .big { font-size: 34px; font-weight: 600; line-height: 1; font-variant-numeric: tabular-nums; }
  .big span { font-size: 16px; font-weight: 500; color: var(--secondary-text-color); margin-left: 1px; }
  .small { font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; text-align: center; max-width: 64%; line-height: 1.25; }
  .drops { position: absolute; inset: 0 12% 30% 12%; pointer-events: none; overflow: hidden; }
  .drops i { position: absolute; top: -10%; left: calc(8% + var(--i) * 16%); width: 6px; height: 9px;
    border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%; background: var(--info-color, #039be5); opacity: 0;
    animation: drop 1.1s ease-in infinite; animation-delay: calc(var(--i) * .18s); }
  @keyframes drop { 0% { transform: translateY(0); opacity: 0; } 15% { opacity: .9; } 100% { transform: translateY(120px); opacity: 0; } }
  .splash { position: absolute; left: 50%; top: 52%; pointer-events: none; }
  .splash b { position: absolute; left: 0; top: 0; width: 20px; height: 20px; margin: -10px; border-radius: 50%;
    border: 2px solid var(--info-color, #039be5); opacity: 0; animation: ripple 1.4s ease-out forwards; }
  .splash b:nth-child(2) { animation-delay: .2s; } .splash b:nth-child(3) { animation-delay: .4s; }
  @keyframes ripple { 0% { transform: scale(.3); opacity: .9; } 100% { transform: scale(7); opacity: 0; } }
  .facts { display: grid; gap: 4px; }
  .fact { display: grid; grid-template-columns: 22px 1fr auto; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 10px; }
  .fact:hover, .chip:hover, .tank-info:hover { background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  .fact .label { color: var(--secondary-text-color); font-size: 13px; }
  .fact .v { font-weight: 500; font-variant-numeric: tabular-nums; }
  .spark { margin: -4px 0 0; }
  .spark svg { width: 100%; height: 48px; display: block; overflow: visible; }
  .spark-line { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 2; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
  .spark-target { stroke: var(--warning-color, #ffa600); stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; opacity: .8; }
  .spark-forecast { stroke: var(--primary-color, #03a9f4); stroke-width: 2; stroke-dasharray: 3 4; vector-effect: non-scaling-stroke; opacity: .7; }
  .spark-now { stroke: var(--divider-color, rgba(127,127,127,.4)); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .spark-dot { fill: var(--warning-color, #ffa600); }
  .spark-labels { position: relative; display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; min-height: 14px; }
  .spark-labels .now-label { position: absolute; transform: translateX(-50%); }
  .spark-labels .ahead { color: var(--warning-color, #ffa600); font-weight: 500; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px 6px 10px; border-radius: 999px;
    border: 1px solid var(--divider-color, rgba(127,127,127,.25)); font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
  .chip span { font-weight: 400; color: var(--secondary-text-color); }
  .chip.bad, .chip.bad ha-icon { color: var(--error-color, #db4437); }
  .tank { display: grid; grid-template-columns: 22px 1fr auto; align-items: center; gap: 10px; }
  .tank.low ha-icon { color: var(--warning-color, #ffa600); }
  .tank-info { display: grid; gap: 6px; padding: 4px 6px; border-radius: 10px; }
  .tank-text { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }
  .tank-text span:first-child { color: var(--secondary-text-color); }
  .bar { height: 8px; border-radius: 999px; background: var(--divider-color, rgba(127,127,127,.2)); overflow: hidden; }
  .bar div { height: 100%; border-radius: inherit; background: var(--info-color, #039be5); transition: width .4s; }
  .tank.low .bar div { background: var(--warning-color, #ffa600); }
  .pump { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 14px;
    padding-top: 12px; border-top: 1px solid var(--divider-color, rgba(127,127,127,.2)); }
  .compact-pump { grid-template-columns: 1fr; border-top: 0; padding-top: 0; }
  .compact-pump .water { justify-content: center; }
  .runtime { display: grid; gap: 4px; }
  .runtime .label, .pump .label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--secondary-text-color); }
  .runtime b { color: var(--primary-text-color); font-weight: 600; }
  input[type=range] { width: 100%; accent-color: var(--primary-color, #03a9f4); margin: 0; }
  .water, .small-btn { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; font-weight: 600; transition: background .2s, transform .1s; }
  .water { padding: 10px 18px; background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); user-select: none; -webkit-user-select: none; touch-action: manipulation; }
  .water ha-icon { color: inherit; --mdc-icon-size: 20px; }
  .water:active:not([disabled]) { transform: scale(.96); }
  .water.confirm, .small-btn.confirm { background: var(--warning-color, #ffa600); color: #fff; }
  .water.running { background: var(--info-color, #039be5); animation: pulse 1.4s ease-in-out infinite; }
  .water[disabled] { opacity: .55; cursor: default; }
  .water.running[disabled] { opacity: 1; }
  @keyframes pulse { 50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--info-color, #039be5) 20%, transparent); } }
  .small-btn { padding: 6px 12px; font-size: 13px; border: 1px solid var(--divider-color, rgba(127,127,127,.3)); }
  .runtime-confirm .label, .choose .label { font-size: 14px; color: var(--primary-text-color); }
  .runtime-confirm .label b { font-weight: 600; }
  .runtime-confirm ha-icon, .choose ha-icon { color: var(--warning-color, #ffa600); }
  .confirm-buttons { display: inline-flex; gap: 8px; align-items: center; }
  .confirm-buttons .small-btn { padding: 10px 16px; }
  .choose { grid-template-columns: 1fr auto; row-gap: 10px; }
  .seconds { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(9, minmax(0, 1fr)); gap: 6px; }
  .sec { aspect-ratio: 1; max-width: 44px; width: 100%; justify-self: center; border-radius: 50%; font-weight: 600;
    border: 1px solid var(--divider-color, rgba(127,127,127,.3)); text-align: center; font-variant-numeric: tabular-nums; }
  .sec small { font-size: 10px; font-weight: 500; opacity: .7; }
  .sec.usual { border-color: var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4); }
  .sec:hover { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .care { display: grid; gap: 4px; }
  .task { display: grid; grid-template-columns: 22px 1fr auto; align-items: center; gap: 8px; padding: 8px; border-radius: 10px;
    background: color-mix(in srgb, var(--warning-color, #ffa600) 10%, transparent); }
  .task ha-icon { color: var(--warning-color, #ffa600); }
  .task .what { font-weight: 500; }
  .task .when { font-size: 12px; color: var(--secondary-text-color); }
  .task.done { opacity: .5; text-decoration: line-through; }
  .next { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--secondary-text-color); padding: 2px 8px; }
  ha-card.compact { padding: 12px; gap: 10px; }
  ha-card.compact .main { grid-template-columns: 96px 1fr; }
  ha-card.compact .big { font-size: 24px; }
  ha-card.compact .small { display: none; }
  ha-card.compact .name { font-size: 16px; }
  ha-card.compact .avatar { width: 36px; height: 36px; }
  ha-card.compact .mood { display: none; }  /* its avatar's ring tells it */
  @media (max-width: 420px) { .main { grid-template-columns: 120px 1fr; } .big { font-size: 28px; } }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
</style>`;

const LIST_STYLE = `<style>
  ha-card { gap: 4px; }
  ha-card::before { display: none; }
  .list-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  .list-title { font-size: 20px; font-weight: 600; }
  .list-summary { font-size: 13px; color: var(--secondary-text-color); }
  .row { display: grid; grid-template-columns: 40px 1fr auto; align-items: center; gap: 12px; padding: 8px; border-radius: 12px; width: 100%;
    --row-accent: var(--success-color, #43a047); }
  .row:hover { background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  .row.thirsty, .row.score-2, .row.score-3 { --row-accent: var(--warning-color, #ffa600); }
  .row.offline { --row-accent: var(--error-color, #db4437); }
  .row.watering { --row-accent: var(--info-color, #039be5); }
  .row .avatar { width: 40px; height: 40px; box-shadow: 0 0 0 2px var(--card-background-color, #fff), 0 0 0 4px var(--row-accent); }
  .row .avatar.drawn { background: color-mix(in srgb, var(--row-accent) 12%, transparent); }
  .row .avatar.drawn .plant { width: 30px; height: 30px; }
  .row-text { display: grid; min-width: 0; }
  .row-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row-line { font-size: 13px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row.thirsty .row-line, .row.score-2 .row-line, .row.score-3 .row-line { color: var(--warning-color, #ffa600); }
  .row.offline .row-line { color: var(--error-color, #db4437); }
  .ring { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center;
    background: conic-gradient(var(--c) calc(var(--v) * 1%), var(--divider-color, rgba(127,127,127,.2)) 0); }
  .ring span { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 600;
    background: var(--card-background-color, #fff); font-variant-numeric: tabular-nums; }
</style>`;

if (!customElements.get(CARD)) {
  customElements.define(CARD, HaCoapPlantCard);
  customElements.define(`${CARD}-editor`, HaCoapPlantCardEditor);
  customElements.define(LIST_CARD, HaCoapPlantsCard);
  customElements.define(`${LIST_CARD}-editor`, HaCoapPlantsCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: CARD, name: 'HA CoAP plant', preview: true,
    description: 'A plant at a glance: its soil, when it needs water, its pump, tank and care.' });
  window.customCards.push({ type: LIST_CARD, name: 'HA CoAP plants', preview: true,
    description: 'Every plant, the most urgent first: thirsty, tank low, care due.' });
}
