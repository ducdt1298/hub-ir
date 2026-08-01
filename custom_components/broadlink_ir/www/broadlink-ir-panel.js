/**
 * Broadlink IR — learn a device's codes without leaving the browser.
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
    width: .75rem; height: .75rem; border-radius: 2px;
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
      spec: structuredClone(DEFAULT_SPEC.climate),
      cells: [],
      codes: {},
      skipped: {},
      index: 0,
      running: false,
      status: null,
      saved: null,
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
      const info = await this._call({ type: "broadlink_ir/info" });
      const learnable = info.remotes.filter((remote) => remote.can_learn);
      this._state.remotes = info.remotes;
      this._state.nextCode = info.next_code;
      this._state.remote = (learnable[0] || info.remotes[0] || {}).entity_id || "";
      this._state.deviceCode = info.next_code[this._state.platform];
    } catch (err) {
      this._state.status = { kind: "error", text: describe(err) };
    }
    this._render();
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
        type: "broadlink_ir/plan",
        platform: this._state.platform,
        spec: this._specForServer(),
      });
      this._set({
        cells,
        step: "capture",
        index: 0,
        status: null,
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: describe(err) } });
    }
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
          type: "broadlink_ir/learn",
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

  async _test() {
    const cell = this._state.cells[this._state.index - 1] || this._state.cells[0];
    const code = cell && this._state.codes[cell.key];
    if (!code) {
      this._set({ status: { kind: "error", text: "Nothing captured to test yet." } });
      return;
    }
    try {
      await this._call({
        type: "broadlink_ir/send",
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
        type: "broadlink_ir/save",
        platform: this._state.platform,
        device_code: this._state.deviceCode,
        spec: this._specForServer(),
        codes: this._state.codes,
      });
      this._set({ step: "saved", saved: result, status: null });
    } catch (err) {
      this._set({ status: { kind: "error", text: describe(err) } });
    }
  }

  // -- rendering -----------------------------------------------------------

  _render() {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLES}</style>
      <header><h1>Broadlink IR</h1></header>
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
            <select id="remote">
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
            </select>
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
                }" title="${esc(cell.label)}"></div>`
            )
            .join("")}
        </div>

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
    const platform = s.platform;
    const yaml = `${platform}:
  - platform: broadlink_ir
    name: My ${PLATFORM_LABELS[platform]}
    unique_id: my_${platform}
    device_code: ${s.saved.device_code}
    controller_data: ${s.remote}`;

    return `<div class="card">
      <h2>Saved</h2>
      <p class="muted">Written to <code>${esc(s.saved.path)}</code>.</p>
      ${
        s.saved.warnings && s.saved.warnings.length
          ? `<div class="status error"><strong>Worth knowing:</strong>
             <ul>${s.saved.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`
          : `<div class="status ok">No gaps found.</div>`
      }
      <p style="margin-top:1rem">Add this to <code>configuration.yaml</code>, then restart:</p>
      <pre>${esc(yaml)}</pre>
      <div class="row"><button id="restart">Teach another device</button></div>
    </div>`;
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

    on("platform", "change", (event) => {
      const platform = event.target.value;
      this._set({
        platform,
        spec: structuredClone(DEFAULT_SPEC[platform]),
        deviceCode: this._state.nextCode[platform],
        cells: [],
        codes: {},
        skipped: {},
      });
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

    for (const node of root.querySelectorAll(".chip")) {
      node.addEventListener("click", () => this._toggleChip(node.dataset.key));
    }

    on("plan", "click", () => this._buildPlan());
    on("run", "click", () => this._learnCurrent(true));
    on("one", "click", () => this._learnCurrent(false));
    on("stop", "click", () => this._set({ running: false }));
    on("skip", "click", () => this._skip());
    on("test", "click", () => this._test());
    on("save", "click", () => this._save());
    on("back", "click", () => this._set({ step: "setup" }));
    on("restart", "click", () =>
      this._set({
        step: "setup",
        cells: [],
        codes: {},
        skipped: {},
        index: 0,
        saved: null,
        deviceCode: (this._state.deviceCode || 0) + 1,
      })
    );
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

customElements.define("broadlink-ir-panel", BroadlinkIrPanel);
