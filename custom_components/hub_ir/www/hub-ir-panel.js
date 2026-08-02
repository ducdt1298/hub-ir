/**
 * HubIR — learn a device's codes without leaving the browser.
 *
 * Plain DOM on purpose: no build step, no bundler, and nothing fetched from
 * outside, which Home Assistant's content security policy would block anyway.
 * Styling comes from Home Assistant's own CSS custom properties, so the panel
 * follows the user's theme.
 *
 * Anything worth testing — which codes a device needs, in what order, whether
 * the result is a valid device file — is decided by the Python side and asked
 * for over the websocket. This file is the hands, not the head.
 */

/**
 * The HubIR mark, inline because the content security policy blocks anything
 * this file would try to fetch. Kept in step with brand/hub-ir-icon.svg, which
 * is what the PNGs for home-assistant/brands are rendered from.
 */
const LOGO = `
<svg viewBox="0 0 256 256" width="28" height="28" aria-hidden="true">
  <defs>
    <linearGradient id="hubir-wave" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#FF6B35"/><stop offset="1" stop-color="#FFB627"/>
    </linearGradient>
  </defs>
  <rect width="256" height="256" rx="56" fill="#14243A"/>
  <g fill="none" stroke="url(#hubir-wave)" stroke-linecap="round">
    <path d="M68 136 A 52 52 0 0 1 120 188" stroke-width="17"/>
    <path d="M68 92 A 96 96 0 0 1 164 188" stroke-width="16" opacity=".82"/>
    <path d="M68 48 A 140 140 0 0 1 208 188" stroke-width="15" opacity=".58"/>
  </g>
  <circle cx="68" cy="188" r="19" fill="url(#hubir-wave)"/>
</svg>`;

const PLATFORM_LABELS = {
  climate: "Air conditioner",
  fan: "Fan",
  light: "Light",
  media_player: "TV / media player",
};

const DEFAULT_SPEC = {
  climate: {
    manufacturer: "",
    models: "",
    minTemperature: 16,
    maxTemperature: 30,
    precision: 1,
    temperatureUnit: "C",
    operationModes: ["cool", "heat"],
    fanModes: ["auto", "low", "mid", "high"],
    swingModes: [],
    hasOnCommand: false,
    modeOptions: {},
  },
  fan: {
    manufacturer: "",
    models: "",
    speed: ["low", "mid", "high"],
    hasDirection: false,
    hasOscillate: false,
  },
  light: {
    manufacturer: "",
    models: "",
    brightness: [],
    colorTemperature: [],
    hasNight: false,
  },
  media_player: {
    manufacturer: "",
    models: "",
    buttons: [
      "on",
      "off",
      "volumeUp",
      "volumeDown",
      "mute",
      "previousChannel",
      "nextChannel",
    ],
    sources: [],
  },
};

const CLIMATE_MODES = ["cool", "heat", "dry", "fan_only", "auto", "heat_cool"];

const STYLES = `
  :host { display: block; height: 100%; background: var(--primary-background-color); }
  .wrap { max-width: 60rem; margin: 0 auto; padding: 1rem 1rem 4rem; box-sizing: border-box; }
  h1 { font-size: 1.5rem; margin: 0; }
  h2 { font-size: 1.05rem; margin: 0 0 .75rem; }
  header {
    display: flex; align-items: center; gap: .75rem;
    padding: .75rem 1rem; background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, #fff);
  }
  header svg { flex: 0 0 auto; border-radius: 6px; }
  .card {
    background: var(--card-background-color, #fff);
    border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.14));
    padding: 1rem; margin-bottom: 1rem; color: var(--primary-text-color);
  }
  label { display: block; font-size: .8rem; color: var(--secondary-text-color); margin-bottom: .2rem; }
  input, select {
    width: 100%; box-sizing: border-box; padding: .5rem;
    border: 1px solid var(--divider-color, #ccc); border-radius: 6px;
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
    font: inherit;
  }
  .grid { display: grid; gap: .75rem; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
  .row { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
  button {
    font: inherit; padding: .5rem .9rem; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--divider-color, #ccc);
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
  }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.danger { color: var(--error-color, #db4437); }
  button:disabled { opacity: .5; cursor: default; }
  .chips { display: flex; flex-wrap: wrap; gap: .4rem; }
  .chip {
    padding: .3rem .6rem; border-radius: 999px; cursor: pointer; font-size: .85rem;
    border: 1px solid var(--divider-color, #ccc); user-select: none;
  }
  .chip[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: transparent; }
  .target { font-size: 1.6rem; font-weight: 500; margin: .25rem 0 .1rem; }
  .muted { color: var(--secondary-text-color); font-size: .85rem; }
  .bar { height: 8px; border-radius: 999px; background: var(--divider-color, #ddd); overflow: hidden; margin: .6rem 0; }
  .bar > div { height: 100%; background: var(--primary-color); transition: width .2s; }
  .cells { display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .5rem; }
  .cell {
    width: .75rem; height: .75rem; border-radius: 2px; cursor: pointer;
    background: var(--divider-color, #ddd);
  }
  .cell.done { background: var(--success-color, #43a047); }
  .cell.skipped { background: var(--warning-color, #ffa600); }
  .cell.current { outline: 2px solid var(--primary-color); outline-offset: 1px; }
  .status { padding: .6rem .8rem; border-radius: 6px; margin-top: .75rem; font-size: .9rem; }
  .status.error { background: rgba(219,68,55,.12); color: var(--error-color, #db4437); }
  .status.ok { background: rgba(67,160,71,.12); color: var(--success-color, #43a047); }
  pre {
    background: var(--secondary-background-color, #f3f3f3); color: var(--primary-text-color);
    padding: .75rem; border-radius: 6px; overflow-x: auto; font-size: .8rem;
  }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  td, th { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--divider-color, #eee); }
  ul { margin: .4rem 0; padding-left: 1.2rem; font-size: .85rem; }
  a { color: var(--primary-color); }
  summary { cursor: pointer; }
`;

class BroadlinkIrPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._ready = false;
    this._state = {
      step: "setup",
      platform: "climate",
      remote: "",
      remotes: [],
      nextCode: {},
      deviceCode: null,
      templateCode: "",
      customCodes: [],
      spec: structuredClone(DEFAULT_SPEC.climate),
      cells: [],
      codes: {},
      skipped: {},
      index: 0,
      running: false,
      status: null,
      saved: null,
      entityName: "",
      creating: false,
      created: null,
    };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._ready) {
      this._ready = true;
      this._load();
    }
  }

  set narrow(_narrow) {
    // Accepted so Home Assistant can set it; the layout is fluid anyway.
  }

  async _call(message) {
    return this._hass.connection.sendMessagePromise(message);
  }

  async _load() {
    try {
      const info = await this._call({ type: "hub_ir/info" });
      const learnable = info.remotes.filter((remote) => remote.can_learn);
      this._state.remotes = info.remotes;
      this._state.nextCode = info.next_code;
      this._state.remote = (learnable[0] || info.remotes[0] || {}).entity_id || "";
      this._state.deviceCode = info.next_code[this._state.platform];
    } catch (err) {
      this._state.status = { kind: "error", text: describe(err) };
    }
    await this._refreshCustomCodes();
    this._render();
  }

  /** List the recordings already made, so they are one click away to reopen. */
  async _refreshCustomCodes() {
    try {
      const { custom } = await this._call({
        type: "hub_ir/list",
        platform: this._state.platform,
      });
      this._state.customCodes = custom;
    } catch {
      this._state.customCodes = [];
    }
  }

  // -- state helpers -------------------------------------------------------

  _set(patch) {
    Object.assign(this._state, patch);
    this._render();
  }

  _setSpec(patch) {
    Object.assign(this._state.spec, patch);
    this._render();
  }

  _remaining() {
    const { cells, codes, skipped } = this._state;
    return cells.filter((cell) => !codes[cell.key] && !skipped[cell.key]);
  }

  _cellState(cell) {
    if (this._state.codes[cell.key]) return "done";
    if (this._state.skipped[cell.key]) return "skipped";
    return "";
  }

  // -- actions -------------------------------------------------------------

  async _buildPlan() {
    try {
      const { cells } = await this._call({
        type: "hub_ir/plan",
        platform: this._state.platform,
        spec: this._specForServer(),
      });
      const state = this._state;
      state.cells = cells;
      state.index = 0;
      this._advanceToFirstGap();
      this._set({ step: "capture", status: null });
    } catch (err) {
      this._set({ status: { kind: "error", text: describe(err) } });
    }
  }

  /**
   * Load an existing device file and carry on from what it already records.
   *
   * The server derives the spec and the codes, including which modes the file
   * says ignore fan speed or temperature, so re-learning a device only costs
   * the codes that are actually missing.
   */
  async _loadTemplate() {
    const code = Number(this._state.templateCode);
    if (!code) {
      this._set({ status: { kind: "error", text: "Enter a device code first." } });
      return;
    }

    try {
      const result = await this._call({
        type: "hub_ir/get",
        platform: this._state.platform,
        device_code: code,
      });

      const spec = { ...structuredClone(DEFAULT_SPEC[this._state.platform]), ...result.spec };
      spec.models = (result.spec.supportedModels || []).join(", ");
      delete spec.supportedModels;

      const kept = Object.keys(result.codes).length;
      this._set({
        spec,
        codes: result.codes,
        skipped: {},
        cells: [],
        index: 0,
        status: {
          kind: "ok",
          text:
            `Loaded device code ${code}: ${kept} code(s) already recorded. ` +
            `Saving will write to ${this._state.deviceCode}, leaving the original alone.`,
        },
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: describe(err) } });
    }
  }

  _advanceToFirstGap() {
    const { cells } = this._state;
    let index = 0;
    while (index < cells.length && this._cellState(cells[index])) index += 1;
    this._state.index = index;
  }

  _specForServer() {
    const spec = structuredClone(this._state.spec);
    spec.supportedModels = String(spec.models || "")
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);
    delete spec.models;
    return spec;
  }

  async _learnCurrent(continuous) {
    const state = this._state;
    if (state.running) return;

    const cell = state.cells[state.index];
    if (!cell) return;

    this._set({ running: true, status: null });

    while (this._state.running) {
      const target = this._state.cells[this._state.index];
      if (!target) break;

      try {
        const { code } = await this._call({
          type: "hub_ir/learn",
          remote_entity_id: this._state.remote,
        });
        this._state.codes[target.key] = code;
        delete this._state.skipped[target.key];
        this._advance();
      } catch (err) {
        this._set({ running: false, status: { kind: "error", text: describe(err) } });
        return;
      }

      if (!continuous) break;
      if (!this._state.cells[this._state.index]) break;
      this._render();
    }

    this._set({ running: false });
  }

  _advance() {
    const { cells, index } = this._state;
    let next = index + 1;
    while (next < cells.length && this._cellState(cells[next])) next += 1;
    this._state.index = next < cells.length ? next : cells.length;
  }

  _skip() {
    const cell = this._state.cells[this._state.index];
    if (!cell) return;
    this._state.skipped[cell.key] = true;
    delete this._state.codes[cell.key];
    this._advance();
    this._render();
  }

  /**
   * Return the cell the buttons act on: the one being captured if it already
   * holds a code, otherwise the last one that does. Without the fallback there
   * is nothing to test right after a capture, because the panel has moved on.
   */
  _testableCell() {
    const { cells, codes, index } = this._state;
    if (cells[index] && codes[cells[index].key]) return cells[index];
    for (let i = Math.min(index, cells.length) - 1; i >= 0; i -= 1) {
      if (codes[cells[i].key]) return cells[i];
    }
    return null;
  }

  async _test() {
    const cell = this._testableCell();
    const code = cell && this._state.codes[cell.key];
    if (!code) {
      this._set({ status: { kind: "error", text: "Nothing captured to test yet." } });
      return;
    }
    try {
      await this._call({
        type: "hub_ir/send",
        remote_entity_id: this._state.remote,
        code,
      });
      this._set({
        status: { kind: "ok", text: `Sent the code for ${cell.label}.` },
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: describe(err) } });
    }
  }

  async _save() {
    try {
      const result = await this._call({
        type: "hub_ir/save",
        platform: this._state.platform,
        device_code: this._state.deviceCode,
        spec: this._specForServer(),
        codes: this._state.codes,
      });
      this._set({
        step: "saved",
        saved: result,
        status: null,
        entityName: defaultName(this._state.spec, this._state.platform),
        creating: false,
        // Clearing this matters: saving a second file after a create would
        // otherwise show the first entity's success over the new file.
        created: null,
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: describe(err) } });
    }
  }

  /** Turn the device file just saved into a live entity, with no restart. */
  async _create() {
    const s = this._state;
    const name = String(s.entityName || "").trim();
    if (!name) {
      this._set({ status: { kind: "error", text: "Give it a name first." } });
      return;
    }

    this._set({ creating: true, status: null });
    try {
      const created = await this._call({
        type: "hub_ir/create_entity",
        platform: s.platform,
        device_code: s.saved.device_code,
        controller_data: s.remote,
        name,
      });
      this._set({ creating: false, created, status: null });
    } catch (err) {
      this._set({ creating: false, status: { kind: "error", text: describe(err) } });
    }
  }

  // -- rendering -----------------------------------------------------------

  _render() {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLES}</style>
      <header>${LOGO}<h1>HubIR</h1></header>
      <div class="wrap">${this._body()}</div>`;
    this._bind();
  }

  _body() {
    if (this._state.step === "setup") return this._setupView();
    if (this._state.step === "capture") return this._captureView();
    return this._savedView();
  }

  _setupView() {
    const s = this._state;
    const learnable = s.remotes.filter((r) => r.can_learn);

    return `
      <div class="card">
        <h2>1 · What are you teaching?</h2>
        <div class="grid">
          <div>
            <label for="platform">Device type</label>
            <select id="platform">
              ${Object.entries(PLATFORM_LABELS)
                .map(
                  ([value, label]) =>
                    `<option value="${value}"${
                      value === s.platform ? " selected" : ""
                    }>${esc(label)}</option>`
                )
                .join("")}
            </select>
          </div>
          <div>
            <label for="remote">Broadlink remote</label>
            ${this._remoteSelect()}
          </div>
          <div>
            <label for="device_code">New device code</label>
            <input id="device_code" type="number" value="${s.deviceCode ?? ""}" />
          </div>
        </div>
        ${
          learnable.length
            ? ""
            : `<div class="status error">No Broadlink remote found. Set up the
               Broadlink integration first — only its remotes can learn codes.</div>`
        }
      </div>

      <div class="card">
        <h2>2 · Identify it</h2>
        <div class="grid">
          <div>
            <label for="manufacturer">Manufacturer</label>
            <input id="manufacturer" value="${esc(s.spec.manufacturer)}" placeholder="Daikin" />
          </div>
          <div>
            <label for="models">Models, comma separated</label>
            <input id="models" value="${esc(s.spec.models)}" placeholder="FTKC35" />
          </div>
        </div>

        <p class="muted" style="margin-top:1rem">
          Starting from a device file that is nearly right is much less work than
          starting from nothing. Any existing code can be loaded — the settings and
          every code it already holds come with it, and only the gaps are left to
          capture. Saving always writes to your own code, so the original is untouched.
        </p>
        <div class="row">
          <input id="template_code" type="number" placeholder="e.g. 1000"
                 value="${esc(s.templateCode)}" style="max-width:10rem" />
          <button id="load_template">Load that device file</button>
        </div>
        ${
          s.customCodes.length
            ? `<div class="row" style="margin-top:.5rem">
                 <span class="muted">Your recordings:</span>
                 ${s.customCodes
                   .map(
                     (code) =>
                       `<span class="chip" data-reopen="${code}" role="button">${code}</span>`
                   )
                   .join("")}
               </div>`
            : ""
        }
      </div>

      ${this._specView()}

      <div class="row">
        <button class="primary" id="plan" ${learnable.length ? "" : "disabled"}>
          Build the list of codes
        </button>
      </div>
      ${this._statusView()}
    `;
  }

  _specView() {
    const s = this._state;
    if (s.platform === "climate") return this._climateSpecView();

    if (s.platform === "fan") {
      return `<div class="card">
        <h2>3 · What can it do?</h2>
        <div class="grid">
          <div>
            <label for="speed">Speeds, slowest first</label>
            <input id="speed" value="${esc(s.spec.speed.join(", "))}" />
          </div>
        </div>
        <div class="row" style="margin-top:.75rem">
          ${chip("hasDirection", "Reversible", s.spec.hasDirection)}
          ${chip("hasOscillate", "Oscillates", s.spec.hasOscillate)}
        </div>
      </div>`;
    }

    if (s.platform === "light") {
      return `<div class="card">
        <h2>3 · What can it do?</h2>
        <div class="grid">
          <div>
            <label for="brightness">Brightness steps (blank if none)</label>
            <input id="brightness" value="${esc(s.spec.brightness.join(", "))}" placeholder="10, 128, 255" />
          </div>
          <div>
            <label for="colorTemperature">Colour temperatures in K (blank if none)</label>
            <input id="colorTemperature" value="${esc(s.spec.colorTemperature.join(", "))}" placeholder="2700, 4000, 6500" />
          </div>
        </div>
        <div class="row" style="margin-top:.75rem">
          ${chip("hasNight", "Has a night light", s.spec.hasNight)}
        </div>
      </div>`;
    }

    return `<div class="card">
      <h2>3 · What can it do?</h2>
      <div class="chips">
        ${["on", "off", "volumeUp", "volumeDown", "mute", "previousChannel", "nextChannel"]
          .map((name) =>
            chip(`button:${name}`, name, s.spec.buttons.includes(name))
          )
          .join("")}
      </div>
      <div style="margin-top:.75rem">
        <label for="sources">Sources / channels, comma separated</label>
        <input id="sources" value="${esc(s.spec.sources.join(", "))}" placeholder="HDMI1, HDMI2, Channel 1" />
      </div>
    </div>`;
  }

  _climateSpecView() {
    const spec = this._state.spec;
    const modes = spec.operationModes;

    return `<div class="card">
      <h2>3 · Temperatures and modes</h2>
      <div class="grid">
        <div><label for="minTemperature">Minimum</label>
          <input id="minTemperature" type="number" step="any" value="${spec.minTemperature}" /></div>
        <div><label for="maxTemperature">Maximum</label>
          <input id="maxTemperature" type="number" step="any" value="${spec.maxTemperature}" /></div>
        <div><label for="precision">Step</label>
          <input id="precision" type="number" step="any" value="${spec.precision}" /></div>
        <div><label for="temperatureUnit">Unit</label>
          <select id="temperatureUnit">
            <option value="C"${spec.temperatureUnit === "C" ? " selected" : ""}>Celsius</option>
            <option value="F"${spec.temperatureUnit === "F" ? " selected" : ""}>Fahrenheit</option>
          </select></div>
      </div>

      <div style="margin-top:1rem">
        <label>Operation modes</label>
        <div class="chips">
          ${CLIMATE_MODES.map((mode) =>
            chip(`mode:${mode}`, mode, modes.includes(mode))
          ).join("")}
        </div>
      </div>

      <div class="grid" style="margin-top:1rem">
        <div><label for="fanModes">Fan speeds, comma separated</label>
          <input id="fanModes" value="${esc(spec.fanModes.join(", "))}" /></div>
        <div><label for="swingModes">Swing modes (blank if none)</label>
          <input id="swingModes" value="${esc(spec.swingModes.join(", "))}" /></div>
      </div>

      <div class="row" style="margin-top:.75rem">
        ${chip("hasOnCommand", "Separate power-on code", spec.hasOnCommand)}
      </div>

      <h2 style="margin-top:1.5rem">Which modes ignore what?</h2>
      <p class="muted">
        Most units ignore the temperature in <em>dry</em> and <em>fan only</em>, and some
        ignore the fan speed too. Saying so here is the difference between
        pressing the remote a hundred times and a couple of hundred — the same
        code is written everywhere it applies.
      </p>
      <table>
        <tr><th>Mode</th><th>Responds to fan speed</th><th>Responds to temperature</th></tr>
        ${modes
          .map((mode) => {
            const options = spec.modeOptions[mode] || {};
            const fan = options.usesFan !== false;
            const temp = options.usesTemperature !== false;
            return `<tr>
              <td>${esc(mode)}</td>
              <td>${chip(`usesFan:${mode}`, fan ? "yes" : "no", fan)}</td>
              <td>${chip(`usesTemperature:${mode}`, temp ? "yes" : "no", temp)}</td>
            </tr>`;
          })
          .join("")}
      </table>
    </div>`;
  }

  _captureView() {
    const s = this._state;
    const total = s.cells.length;
    const done = Object.keys(s.codes).length;
    const skipped = Object.keys(s.skipped).length;
    const current = s.cells[s.index];
    const percent = total ? Math.round(((done + skipped) / total) * 100) : 0;

    return `
      <div class="card">
        <h2>Point the original remote at the Broadlink</h2>
        ${
          current
            ? `<p class="muted">Set the remote to this, then press send:</p>
               <div class="target">${esc(current.label)}</div>
               <p class="muted">${done + skipped} of ${total} · ${esc(current.group)}</p>`
            : `<div class="target">All ${total} codes accounted for</div>`
        }
        <div class="bar"><div style="width:${percent}%"></div></div>
        <div class="cells">
          ${s.cells
            .map(
              (cell, i) =>
                `<div class="cell ${this._cellState(cell)}${
                  i === s.index ? " current" : ""
                }" data-index="${i}" role="button" tabindex="0"
                  title="${esc(cell.label)} — click to go back to it"></div>`
            )
            .join("")}
        </div>
        <p class="muted">Click a square to return to that code and capture it again.</p>

        <div class="row" style="margin-top:1rem">
          <button class="primary" id="run" ${
            s.running || !current ? "disabled" : ""
          }>Start capturing</button>
          <button id="one" ${s.running || !current ? "disabled" : ""}>Just this one</button>
          <button id="stop" ${s.running ? "" : "disabled"}>Stop</button>
          <button id="skip" ${s.running || !current ? "disabled" : ""}>Skip</button>
          <button id="test" ${s.running ? "disabled" : ""}>Test last code</button>
        </div>
        ${
          s.running
            ? `<p class="muted" style="margin-top:.75rem">
                 Listening… each code times out after 30 seconds. Keep pressing;
                 the panel moves on by itself.
               </p>`
            : ""
        }
        ${this._statusView()}
      </div>

      <div class="row">
        <button id="back">Back to the settings</button>
        <button class="primary" id="save" ${done ? "" : "disabled"}>
          Save as device code ${s.deviceCode}
        </button>
      </div>
      ${
        skipped
          ? `<p class="muted">${skipped} skipped; those stay empty and the
             integration refuses to send them.</p>`
          : ""
      }
    `;
  }

  _savedView() {
    const s = this._state;

    return `<div class="card">
      <h2>Saved</h2>
      <p class="muted">Written to <code>${esc(s.saved.path)}</code>.</p>
      ${
        s.saved.warnings && s.saved.warnings.length
          ? `<div class="status error"><strong>Worth knowing:</strong>
             <ul>${s.saved.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`
          : `<div class="status ok">No gaps found.</div>`
      }
    </div>

    ${s.created ? this._createdView() : this._createView()}

    <div class="row"><button id="restart">Teach another device</button></div>`;
  }

  /**
   * Offer to create the entity here, rather than sending someone to a text
   * editor and a restart.
   *
   * Everything the config flow needs was settled minutes ago — the device type,
   * the code just written, the remote the codes came through — so the name is
   * the only question left, and even that has a reasonable guess. The server
   * starts the flow; this side does not need to know its steps.
   */
  _createView() {
    const s = this._state;

    return `<div class="card">
      <h2>Add it to Home Assistant</h2>
      <p class="muted">No YAML, no restart — the entity appears straight away.</p>
      <div class="grid">
        <div>
          <label for="entity_name">Name</label>
          <input id="entity_name" value="${esc(s.entityName)}"
                 placeholder="My ${esc(PLATFORM_LABELS[s.platform])}" />
        </div>
        <div>
          <label for="remote">Broadlink remote</label>
          ${this._remoteSelect()}
        </div>
      </div>
      <p class="muted" style="margin-top:.5rem">
        Already set to the remote you captured through; change it only if this
        device sits in front of a different one.
      </p>
      <div class="row" style="margin-top:1rem">
        <button class="primary" id="create" ${s.creating ? "disabled" : ""}>
          ${s.creating ? "Creating…" : "Create the entity"}
        </button>
      </div>
      ${this._statusView()}
      <details style="margin-top:1rem">
        <summary class="muted">Or write it into configuration.yaml yourself</summary>
        <p class="muted">Only worth it if you keep your entities in YAML. It needs
          a restart, and the two ways of configuring one device do not know about
          each other — so pick one, not both.</p>
        <pre>${esc(this._yaml())}</pre>
      </details>
    </div>`;
  }

  _createdView() {
    const s = this._state;
    const created = s.created;
    const label = created.entity_id || created.title;

    return `<div class="card">
      <h2>${created.existing ? "Already in Home Assistant" : "Added to Home Assistant"}</h2>
      <div class="status ok">
        ${
          created.existing
            ? `<code>${esc(label)}</code> already uses device code
               ${esc(s.saved.device_code)}. It has been reloaded, so it is running
               on the file you just saved.`
            : `Created <code>${esc(label)}</code>. Nothing to restart.`
        }
      </div>
      <div class="row" style="margin-top:1rem">
        ${created.entity_id ? `<button class="primary" id="show">Show it</button>` : ""}
        <a href="/config/integrations/integration/hub_ir">Manage HubIR devices</a>
      </div>
      <p class="muted" style="margin-top:.75rem">
        Rename it, put it in an area, or point it at a different remote from
        Settings &rarr; Devices &amp; services &rarr; HubIR.
      </p>
    </div>`;
  }

  /** The remote picker, shared by the setup step and the create step. */
  _remoteSelect() {
    const s = this._state;
    return `<select id="remote">
      ${s.remotes
        .map(
          (r) =>
            `<option value="${esc(r.entity_id)}"${
              r.entity_id === s.remote ? " selected" : ""
            }${r.can_learn ? "" : " disabled"}>${esc(r.name)}${
              r.can_learn ? "" : " — not a Broadlink remote"
            }</option>`
        )
        .join("")}
    </select>`;
  }

  /** The manual escape hatch, for people who keep their entities in YAML. */
  _yaml() {
    const s = this._state;
    const name = String(s.entityName || "").trim() ||
      `My ${PLATFORM_LABELS[s.platform]}`;
    return `${s.platform}:
  - platform: hub_ir
    name: ${name}
    unique_id: ${slugify(name) || `my_${s.platform}`}
    device_code: ${s.saved.device_code}
    controller_data: ${s.remote}`;
  }

  _statusView() {
    const status = this._state.status;
    if (!status) return "";
    return `<div class="status ${status.kind === "ok" ? "ok" : "error"}">${esc(
      status.text
    )}</div>`;
  }

  // -- wiring --------------------------------------------------------------

  _bind() {
    const root = this.shadowRoot;
    const on = (id, event, handler) => {
      const node = root.getElementById(id);
      if (node) node.addEventListener(event, handler);
    };

    on("platform", "change", async (event) => {
      const platform = event.target.value;
      Object.assign(this._state, {
        platform,
        spec: structuredClone(DEFAULT_SPEC[platform]),
        deviceCode: this._state.nextCode[platform],
        templateCode: "",
        customCodes: [],
        cells: [],
        codes: {},
        skipped: {},
        index: 0,
        status: null,
        entityName: "",
        created: null,
      });
      this._render();
      await this._refreshCustomCodes();
      this._render();
    });
    on("remote", "change", (event) => this._set({ remote: event.target.value }));
    on("device_code", "change", (event) =>
      this._set({ deviceCode: Number(event.target.value) })
    );

    for (const field of ["manufacturer", "models"]) {
      on(field, "input", (event) => {
        this._state.spec[field] = event.target.value;
      });
    }
    for (const field of ["minTemperature", "maxTemperature", "precision"]) {
      on(field, "change", (event) =>
        this._setSpec({ [field]: Number(event.target.value) })
      );
    }
    on("temperatureUnit", "change", (event) =>
      this._setSpec({ temperatureUnit: event.target.value })
    );
    for (const field of [
      "fanModes",
      "swingModes",
      "speed",
      "brightness",
      "colorTemperature",
      "sources",
    ]) {
      on(field, "change", (event) => {
        const numeric = field === "brightness" || field === "colorTemperature";
        const list = splitList(event.target.value, numeric);
        this._setSpec({ [field]: list });
      });
    }

    for (const node of root.querySelectorAll(".chip[data-key]")) {
      node.addEventListener("click", () => this._toggleChip(node.dataset.key));
    }

    for (const node of root.querySelectorAll(".chip[data-reopen]")) {
      node.addEventListener("click", () => {
        this._state.templateCode = node.dataset.reopen;
        this._loadTemplate();
      });
    }

    for (const node of root.querySelectorAll(".cell[data-index]")) {
      node.addEventListener("click", () => {
        if (this._state.running) return;
        this._set({ index: Number(node.dataset.index), status: null });
      });
    }

    on("template_code", "input", (event) => {
      this._state.templateCode = event.target.value;
    });
    on("load_template", "click", () => this._loadTemplate());

    on("plan", "click", () => this._buildPlan());
    on("run", "click", () => this._learnCurrent(true));
    on("one", "click", () => this._learnCurrent(false));
    on("stop", "click", () => this._set({ running: false }));
    on("skip", "click", () => this._skip());
    on("test", "click", () => this._test());
    on("save", "click", () => this._save());
    on("back", "click", () => this._set({ step: "setup" }));

    // Stored without re-rendering, like manufacturer and models: rebuilding the
    // shadow root on every keystroke would lose the caret.
    on("entity_name", "input", (event) => {
      this._state.entityName = event.target.value;
    });
    // change fires on blur, a safe moment to redraw the YAML fallback with the
    // name that was actually typed.
    on("entity_name", "change", () => this._render());
    on("create", "click", () => this._create());
    on("show", "click", () => {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: this._state.created.entity_id },
          bubbles: true,
          composed: true,
        })
      );
    });
    on("restart", "click", async () => {
      Object.assign(this._state, {
        step: "setup",
        cells: [],
        codes: {},
        skipped: {},
        index: 0,
        saved: null,
        templateCode: "",
        deviceCode: (this._state.deviceCode || 0) + 1,
        entityName: "",
        creating: false,
        created: null,
        // Never cleared before, so a failed create would follow the user back
        // to a fresh setup screen as a red box about nothing.
        status: null,
      });
      // The recording just saved should show up in the list straight away.
      await this._refreshCustomCodes();
      this._render();
    });
  }

  _toggleChip(key) {
    const spec = this._state.spec;
    const [kind, value] = key.split(":");

    if (kind === "mode") {
      const modes = spec.operationModes;
      spec.operationModes = modes.includes(value)
        ? modes.filter((mode) => mode !== value)
        : [...modes, value];
    } else if (kind === "button") {
      const buttons = spec.buttons;
      spec.buttons = buttons.includes(value)
        ? buttons.filter((button) => button !== value)
        : [...buttons, value];
    } else if (kind === "usesFan" || kind === "usesTemperature") {
      const options = { ...(spec.modeOptions[value] || {}) };
      options[kind] = options[kind] === false;
      spec.modeOptions = { ...spec.modeOptions, [value]: options };
    } else {
      spec[kind] = !spec[kind];
    }
    this._render();
  }
}

function chip(key, label, pressed) {
  return `<span class="chip" data-key="${esc(key)}" role="button"
    aria-pressed="${pressed ? "true" : "false"}">${esc(label)}</span>`;
}

function splitList(value, numeric) {
  const parts = String(value)
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  return numeric ? parts.map(Number).filter((entry) => !Number.isNaN(entry)) : parts;
}

function slugify(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** Guess a name from what the user already typed on the identify step. */
function defaultName(spec, platform) {
  const model = String(spec.models || "").split(",")[0].trim();
  const guess = [String(spec.manufacturer || "").trim(), model]
    .filter(Boolean)
    .join(" ");
  return guess || `My ${PLATFORM_LABELS[platform]}`;
}

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character]
  );
}

function describe(err) {
  if (!err) return "Something went wrong.";
  return err.message || err.error || String(err);
}

customElements.define("hub-ir-panel", BroadlinkIrPanel);
