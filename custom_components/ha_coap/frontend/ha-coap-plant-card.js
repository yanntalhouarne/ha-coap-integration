/*
 * HA CoAP plant card: one plant board at a glance, and what to do for it.
 *
 *   type: custom:ha-coap-plant-card
 *   device: <the board's device>     (chosen in the card's editor)
 *   name: Basil                      (optional: else the device's name)
 *
 * It finds the board's entities from its device (their translation keys), so only the device is
 * chosen: its soil as a gauge with its watering target, when it needs water next and when it was
 * last watered, its soil over the last two days, its battery, air and temperature, the pump
 * (run time and Water), and with the fleet manager its water tank and its care tasks. Served by
 * the HA CoAP integration (no resource to add). Plain JavaScript, no build step.
 */

const CARD = 'ha-coap-plant-card';
const HISTORY_HOURS = 48;
const HISTORY_EVERY_MS = 15 * 60 * 1000;
const CONFIRM_MS = 4000;
const RUN_TIME_CONFIRM_MS = 8000;  // a new run time not confirmed by then: the slider goes back

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (state) => (state && !['unknown', 'unavailable', ''].includes(state.state) ? Number(state.state) : null);
const ok = (state) => Boolean(state) && !['unknown', 'unavailable'].includes(state.state);

function relative(when) {
  if (!when) return '—';
  const seconds = (new Date(when).getTime() - Date.now()) / 1000;
  const a = Math.abs(seconds);
  if (Number.isNaN(a)) return '—';
  if (a < 60) return 'now';
  const text = a < 3600 ? `${Math.round(a / 60)} min` : a < 2 * 86400 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
  return seconds > 0 ? `in ${text}` : `${text} ago`;
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
const angleOf = (pct) => START + (SWEEP * Math.max(0, Math.min(100, pct))) / 100;

/* The plant boards: devices with an HA CoAP soil sensor */
function plantDevices(hass) {
  const ids = new Set();
  for (const entity of Object.values(hass.entities || {})) {
    if (entity.platform === 'ha_coap' && entity.translation_key === 'soil_moisture' && entity.device_id) ids.add(entity.device_id);
  }
  return [...ids].map((id) => hass.devices?.[id]).filter(Boolean)
    .sort((a, b) => (a.name_by_user || a.name || '').localeCompare(b.name_by_user || b.name || ''));
}

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
    this._config = { ...config };
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
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 'auto' };
  }

  connectedCallback() {
    this._tick = setInterval(() => { this._signature = null; this._render(); }, 60 * 1000);  // "in 5 h" moves on
  }

  disconnectedCallback() {
    clearInterval(this._tick);
  }

  /* translation key → entity ID, for this board's device */
  _entities() {
    const found = {};
    for (const entity of Object.values(this._hass.entities || {})) {
      if (entity.device_id === this._config.device && entity.platform === 'ha_coap' && entity.translation_key) {
        found[entity.translation_key] = entity.entity_id;
      }
    }
    const todo = Object.values(this._hass.entities || {}).find((e) => e.platform === 'ha_coap' && e.entity_id.startsWith('todo.'));
    if (todo) found.plant_care = todo.entity_id;
    return found;
  }

  _state(key) {
    const id = this._ids[key];
    return id ? this._hass.states[id] : undefined;
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    if (this._dragging) return;  // the run time slider: not redrawn under the finger
    this._ids = this._entities();
    const signature = JSON.stringify([this._config, this._confirming, this._pendingRunTime, this._history && this._history.length,
      Object.values(this._ids).map((id) => this._hass.states[id]?.last_updated), this._hass.themes?.darkMode]);
    if (signature === this._signature) return;
    this._signature = signature;
    const device = this._hass.devices?.[this._config.device];
    if (!this._config.device || !device || !this._ids.soil_moisture) {
      this.shadowRoot.innerHTML = `${STYLE}<ha-card><div class="empty">
        <ha-icon icon="mdi:sprout-outline"></ha-icon>
        <div>${this._config.device ? 'This plant board isn\'t in Home Assistant any more.' : 'Choose a plant board in the card\'s settings.'}</div>
      </div></ha-card>`;
      return;
    }
    this._maybeLoadHistory();
    this.shadowRoot.innerHTML = STYLE + this._html(device);
    this._wire();
  }

  _html(device) {
    const soil = this._state('soil_moisture');
    const soilValue = num(soil);
    const target = num(this._state('water_below')) ?? 30;
    const needs = this._state('needs_water');
    const running = this._state('pump_running')?.state === 'on';
    const online = this._state('connectivity') ? this._state('connectivity').state === 'on' : ok(soil);
    const area = device.area_id ? this._hass.areas?.[device.area_id]?.name : '';
    const name = this._config.name || device.name_by_user || device.name || 'Plant';

    let mood = ['happy', 'Happy', 'mdi:emoticon-happy-outline'];
    if (!online) mood = ['offline', 'Not answering', 'mdi:lan-disconnect'];
    else if (running) mood = ['watering', 'Watering', 'mdi:water'];
    else if (needs?.state === 'on') mood = ['thirsty', 'Thirsty', 'mdi:water-alert-outline'];
    else if (soilValue !== null && soilValue > 85) mood = ['wet', 'Soaked', 'mdi:water-percent'];

    // The gauge: its soil, with the target (Water below) marked
    const color = soilValue === null ? 'var(--disabled-text-color, #9e9e9e)'
      : soilValue < target ? 'var(--warning-color, #ffa600)'
        : soilValue > 85 ? 'var(--info-color, #039be5)' : 'var(--success-color, #43a047)';
    const [tx1, ty1] = arcPoint(angleOf(target), 34);
    const [tx2, ty2] = arcPoint(angleOf(target), 48);
    const gauge = `
      <svg viewBox="0 0 100 92" class="gauge-svg" aria-hidden="true">
        <path d="${arc(START, START + SWEEP, 41)}" class="track"/>
        ${soilValue !== null ? `<path d="${arc(START, angleOf(Math.max(soilValue, 0.5)), 41)}" class="value" style="stroke:${color}"/>` : ''}
        <line x1="${tx1.toFixed(2)}" y1="${ty1.toFixed(2)}" x2="${tx2.toFixed(2)}" y2="${ty2.toFixed(2)}" class="target"/>
      </svg>
      <div class="gauge-text">
        <div class="big">${soilValue !== null ? `${Math.round(soilValue)}<span>%</span>` : '—'}</div>
        <div class="small">water below ${Math.round(target)} %</div>
      </div>`;

    const next = this._state('next_watering');
    const last = this._state('last_watered');
    const used = this._state('water_used');
    const facts = [
      ['mdi:calendar-clock', 'Needs water', needs?.state === 'on' ? 'now' : ok(next) ? relative(next.state) : '—', 'next_watering'],
      ['mdi:watering-can-outline', 'Last watered', ok(last) ? relative(last.state) : '—', 'last_watered'],
      ['mdi:cup-water', 'Water used', ok(used) ? `${Number(used.state).toFixed(2)} ${esc(used.attributes.unit_of_measurement || 'L')}` : '—', 'water_used'],
    ].map(([icon, label, value, key]) => `
      <button class="fact" data-more="${esc(this._ids[key] || '')}">
        <ha-icon icon="${icon}"></ha-icon><span class="label">${label}</span><span class="v">${esc(value)}</span>
      </button>`).join('');

    return `<ha-card class="${mood[0]}">
      <div class="top">
        <button class="title" data-more="${esc(this._ids.soil_moisture)}">
          <div class="name">${esc(name)}</div>
          ${area ? `<div class="area">${esc(area)}</div>` : ''}
        </button>
        <div class="mood ${mood[0]}"><ha-icon icon="${mood[2]}"></ha-icon>${mood[1]}</div>
      </div>
      <div class="main">
        <button class="gauge" data-more="${esc(this._ids.soil_moisture)}">${gauge}</button>
        <div class="facts">${facts}</div>
      </div>
      ${this._spark(target)}
      ${this._chips()}
      ${this._tank()}
      ${this._pump(running, online)}
      ${this._care()}
    </ha-card>`;
  }

  _spark(target) {
    const points = (this._history || []).filter((p) => !Number.isNaN(p[1]));
    if (points.length < 2) return '<div class="spark empty-spark"></div>';
    const t0 = Date.now() - HISTORY_HOURS * 3600 * 1000;
    const x = (t) => Math.max(0, Math.min(300, ((t - t0) / (HISTORY_HOURS * 3600 * 1000)) * 300));
    // Its own range (with the target), so a slow drying shows
    const values = points.map((point) => point[1]).concat([target]);
    const lo = Math.max(0, Math.min(...values) - 5);
    const hi = Math.min(100, Math.max(...values) + 5);
    const y = (v) => 46 - ((Math.max(lo, Math.min(hi, v)) - lo) / Math.max(1, hi - lo)) * 42;
    const line = points.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const area = `0,48 ${line} ${x(points[points.length - 1][0]).toFixed(1)},48`;
    return `<div class="spark" title="Soil, last ${HISTORY_HOURS} hours">
      <svg viewBox="0 0 300 48" preserveAspectRatio="none" aria-hidden="true">
        <defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stop-color="var(--primary-color, #03a9f4)" stop-opacity="0.28"/>
          <stop offset="1" stop-color="var(--primary-color, #03a9f4)" stop-opacity="0"/></linearGradient></defs>
        <line x1="0" x2="300" y1="${y(target).toFixed(1)}" y2="${y(target).toFixed(1)}" class="spark-target"/>
        <polygon points="${area}" fill="url(#fill)"/>
        <polyline points="${line}" class="spark-line"/>
      </svg>
      <div class="spark-labels"><span>${HISTORY_HOURS} h ago</span><span>now</span></div>
    </div>`;
  }

  _chips() {
    const chips = [];
    const battery = this._state('battery');
    const charging = this._state('charging')?.state === 'on';
    const plugged = this._state('plugged_in')?.state === 'on';
    const days = num(this._state('battery_days_left'));
    if (battery) {
      const pct = num(battery);
      const icon = charging ? 'mdi:battery-charging' : pct === null ? 'mdi:battery-unknown'
        : `mdi:battery${pct >= 95 ? '' : `-${Math.max(10, Math.round(pct / 10) * 10)}`}`;
      const extra = charging ? 'charging' : plugged ? 'on USB' : days !== null ? `${Math.round(days)} d left` : '';
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
        <div class="bar"><div style="width:${Math.max(2, Math.min(100, pct))}%"></div></div>
      </button>
      ${this._ids.water_tank_refilled ? `<button class="small-btn ${confirming ? 'confirm' : ''}" data-action="refill">${confirming ? 'Sure? Tap again' : 'Refilled'}</button>` : ''}
    </div>`;
  }

  _pump(running, online) {
    const runTime = this._state('pump_run_time');
    if (!this._ids.pump && !runTime) return '';
    const seconds = num(runTime) ?? 3;
    const confirming = this._confirming === 'water';
    const disabled = !online || running || !this._ids.pump;
    const pending = this._pendingRunTime;
    if (pending != null) {
      // A new run time, asked before it's set
      return `<div class="pump runtime-confirm">
        <span class="label"><ha-icon icon="mdi:timer-outline"></ha-icon><span>Change the run time to <b>${pending}&nbsp;s</b>?</span></span>
        <span class="confirm-buttons">
          <button class="small-btn" data-action="runtime-cancel">Cancel</button>
          <button class="water" data-action="runtime-set">Change</button>
        </span>
      </div>`;
    }
    return `<div class="pump">
      ${runTime ? `<label class="runtime">
        <span class="label"><ha-icon icon="mdi:timer-outline"></ha-icon>Run time <b>${Math.round(seconds)} s</b></span>
        <input type="range" min="1" max="9" step="1" value="${Math.round(seconds)}" ${ok(runTime) ? '' : 'disabled'} aria-label="Pump run time"/>
      </label>` : '<span></span>'}
      <button class="water ${confirming ? 'confirm' : ''} ${running ? 'running' : ''}" data-action="water" ${disabled ? 'disabled' : ''}>
        <ha-icon icon="mdi:watering-can"></ha-icon>${running ? 'Watering…' : confirming ? `Water ${Math.round(seconds)} s?` : 'Water'}
      </button>
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
        clearTimeout(this._runTimeTimer);
        this._runTimeTimer = setTimeout(() => this._cancelRunTime(), RUN_TIME_CONFIRM_MS);
        this._signature = null;
        this._render();
      });
      slider.addEventListener('pointerup', () => { setTimeout(() => { this._dragging = false; }, 300); });
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
    if (action === 'runtime-cancel') {
      this._cancelRunTime();
      return;
    }
    if (action === 'runtime-set') {
      const value = this._pendingRunTime;
      this._cancelRunTime();
      if (value != null) await this._call('number', 'set_value', { entity_id: this._ids.pump_run_time, value });
      return;
    }
    if (action === 'done') {
      el.classList.add('done');
      await this._call('todo', 'update_item', { entity_id: this._ids.plant_care, item: el.dataset.uid, status: 'completed' });
      return;
    }
    // Water and Refilled: a second tap confirms (a plant isn't watered by a stray tap)
    if (this._confirming !== action) {
      this._confirming = action;
      clearTimeout(this._confirmTimer);
      this._confirmTimer = setTimeout(() => { this._confirming = null; this._signature = null; this._render(); }, CONFIRM_MS);
      this._signature = null;
      this._render();
      return;
    }
    this._confirming = null;
    clearTimeout(this._confirmTimer);
    this._signature = null;
    const entity = action === 'water' ? this._ids.pump : this._ids.water_tank_refilled;
    await this._call('button', 'press', { entity_id: entity });
    this._render();
  }

  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
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
    this.innerHTML = `
      <style>
        .editor { display: grid; gap: 12px; padding: 4px 0; }
        label { display: grid; gap: 4px; font-size: 14px; color: var(--primary-text-color); }
        select, input { font: inherit; padding: 10px 12px; border-radius: 8px; color: var(--primary-text-color);
          background: var(--secondary-background-color, #f5f5f5); border: 1px solid var(--divider-color, #ddd); }
        .hint { font-size: 12px; color: var(--secondary-text-color); }
      </style>
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
        ${devices.length ? '' : '<div class="hint">No HA CoAP plant board yet.</div>'}
      </div>`;
    this.querySelector('#device').addEventListener('change', (e) => this._changed({ device: e.target.value }));
    this.querySelector('#name').addEventListener('change', (e) => this._changed({ name: e.target.value.trim() || undefined }));
  }

  _changed(values) {
    this._config = { ...this._config, ...values };
    Object.keys(this._config).forEach((k) => this._config[k] === undefined && delete this._config[k]);
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config }, bubbles: true, composed: true }));
  }
}

const STYLE = `<style>
  :host { --plant-accent: var(--success-color, #43a047); }
  ha-card { padding: 16px; display: flex; flex-direction: column; gap: 14px; overflow: hidden; position: relative; }
  ha-card.thirsty { --plant-accent: var(--warning-color, #ffa600); }
  ha-card.watering { --plant-accent: var(--info-color, #039be5); }
  ha-card.wet { --plant-accent: var(--info-color, #039be5); }
  ha-card.offline { --plant-accent: var(--error-color, #db4437); }
  ha-card::before { content: ''; position: absolute; inset: 0 0 auto 0; height: 4px; background: var(--plant-accent); opacity: .85; }
  button { font: inherit; color: inherit; background: none; border: 0; padding: 0; text-align: inherit; cursor: pointer; }
  button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 8px; }
  ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .empty { display: flex; gap: 12px; align-items: center; color: var(--secondary-text-color); padding: 8px 0; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { min-width: 0; }
  .name { font-size: 20px; font-weight: 600; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .area { font-size: 13px; color: var(--secondary-text-color); }
  .mood { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px 4px 8px; border-radius: 999px; font-size: 13px;
    font-weight: 500; white-space: nowrap; color: var(--plant-accent);
    background: color-mix(in srgb, var(--plant-accent) 14%, transparent); }
  .mood ha-icon { color: inherit; --mdc-icon-size: 16px; }
  .mood.watering ha-icon { animation: drip 1.2s ease-in-out infinite; }
  @keyframes drip { 50% { transform: translateY(2px); opacity: .55; } }
  .main { display: grid; grid-template-columns: minmax(120px, 160px) 1fr; gap: 12px; align-items: center; }
  .gauge { position: relative; display: block; width: 100%; }
  .gauge-svg { width: 100%; display: block; }
  .track { fill: none; stroke: var(--divider-color, rgba(127,127,127,.25)); stroke-width: 9; stroke-linecap: round; }
  .value { fill: none; stroke-width: 9; stroke-linecap: round; transition: stroke .4s; }
  .target { stroke: var(--primary-text-color); stroke-width: 2; stroke-linecap: round; opacity: .7; }
  .gauge-text { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; padding-top: 6px; }
  .big { font-size: 34px; font-weight: 600; line-height: 1; font-variant-numeric: tabular-nums; }
  .big span { font-size: 16px; font-weight: 500; color: var(--secondary-text-color); margin-left: 1px; }
  .small { font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; text-align: center; max-width: 64%; line-height: 1.25; }
  .facts { display: grid; gap: 4px; }
  .fact { display: grid; grid-template-columns: 22px 1fr auto; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 10px; }
  .fact:hover, .chip:hover, .tank-info:hover { background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  .fact .label { color: var(--secondary-text-color); font-size: 13px; }
  .fact .v { font-weight: 500; font-variant-numeric: tabular-nums; }
  .spark { margin: -4px 0 0; }
  .spark svg { width: 100%; height: 48px; display: block; overflow: visible; }
  .spark-line { fill: none; stroke: var(--primary-color, #03a9f4); stroke-width: 2; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
  .spark-target { stroke: var(--warning-color, #ffa600); stroke-width: 1; stroke-dasharray: 4 4; vector-effect: non-scaling-stroke; opacity: .8; }
  .spark-labels { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 2px; }
  .empty-spark { display: none; }
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
  .runtime { display: grid; gap: 4px; }
  .runtime .label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--secondary-text-color); }
  .runtime b { color: var(--primary-text-color); font-weight: 600; }
  input[type=range] { width: 100%; accent-color: var(--primary-color, #03a9f4); margin: 0; }
  .water, .small-btn { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; font-weight: 600; transition: background .2s, transform .1s; }
  .water { padding: 10px 18px; background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .water ha-icon { color: inherit; --mdc-icon-size: 20px; }
  .water:active:not([disabled]) { transform: scale(.97); }
  .water.confirm, .small-btn.confirm { background: var(--warning-color, #ffa600); color: #fff; }
  .water.running { background: var(--info-color, #039be5); animation: pulse 1.4s ease-in-out infinite; }
  .water[disabled] { opacity: .55; cursor: default; }
  .water.running[disabled] { opacity: 1; }
  @keyframes pulse { 50% { box-shadow: 0 0 0 8px color-mix(in srgb, var(--info-color, #039be5) 20%, transparent); } }
  .small-btn { padding: 6px 12px; font-size: 13px; border: 1px solid var(--divider-color, rgba(127,127,127,.3)); }
  .runtime-confirm .label { display: flex; align-items: center; gap: 6px; font-size: 14px; }
  .runtime-confirm .label b { font-weight: 600; }
  .runtime-confirm ha-icon { color: var(--warning-color, #ffa600); }
  .confirm-buttons { display: inline-flex; gap: 8px; align-items: center; }
  .confirm-buttons .small-btn { padding: 10px 16px; }
  .care { display: grid; gap: 4px; }
  .task { display: grid; grid-template-columns: 22px 1fr auto; align-items: center; gap: 8px; padding: 8px; border-radius: 10px;
    background: color-mix(in srgb, var(--warning-color, #ffa600) 10%, transparent); }
  .task ha-icon { color: var(--warning-color, #ffa600); }
  .task .what { font-weight: 500; }
  .task .when { font-size: 12px; color: var(--secondary-text-color); }
  .task.done { opacity: .5; text-decoration: line-through; }
  .next { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--secondary-text-color); padding: 2px 8px; }
  @media (max-width: 420px) { .main { grid-template-columns: 120px 1fr; } .big { font-size: 28px; } }
</style>`;

if (!customElements.get(CARD)) {
  customElements.define(CARD, HaCoapPlantCard);
  customElements.define(`${CARD}-editor`, HaCoapPlantCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: CARD, name: 'HA CoAP plant', preview: true,
    description: 'A plant board at a glance: its soil, when it needs water, its pump, tank and care.' });
}
