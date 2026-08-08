/**
 * HubIR — record a device's codes from the browser.
 *
 * Plain DOM by design: no build step, no bundler, and nothing fetched from
 * outside, which Home Assistant's content security policy would block. Styling
 * comes from Home Assistant's own CSS custom properties, so the panel follows
 * the active theme.
 *
 * Everything worth testing — which codes a device needs, in what order, and
 * whether the result is a valid device file — is decided on the Python side and
 * requested over the websocket. This file renders and dispatches; it does not
 * decide.
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
  switch: "Switch or socket",
};

const DEFAULT_SPEC = {
  climate: {
    manufacturer: "",
    models: [],
    minTemperature: 16,
    maxTemperature: 30,
    precision: 1,
    temperatureUnit: "C",
    operationModes: ["cool", "heat"],
    fanModes: ["auto", "low", "mid", "high"],
    swingModes: [],
    hasOnCommand: false,
    modeOptions: {},
    presets: [],
    presetBaseline: {},
    extraCommands: [],
  },
  fan: {
    manufacturer: "",
    models: [],
    speed: ["low", "mid", "high"],
    hasDirection: false,
    hasOscillate: false,
    extraCommands: [],
  },
  light: {
    manufacturer: "",
    models: [],
    brightness: [],
    colorTemperature: [],
    hasNight: false,
    extraCommands: [],
  },
  media_player: {
    manufacturer: "",
    models: [],
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
    extraCommands: [],
  },
  switch: {
    manufacturer: "",
    models: [],
    hasToggle: false,
    extraCommands: [],
  },
};

const CLIMATE_MODES = ["cool", "heat", "dry", "fan_only", "auto", "heat_cool"];

/**
 * Every list the user builds by hand, described once.
 *
 * Order is load-bearing for most of these: a climate file's fan speeds and a
 * fan's speeds are matched against the command tree by position, not by name.
 * The editor therefore shows the position and allows it to be changed, rather
 * than holding it in a comma-separated string where a reordering is invisible.
 *
 * `fill` is the one-click starting point offered while a list is still empty.
 */
const LIST_FIELDS = {
  models: {
    label: "Models",
    one: "model",
    placeholder: "FTKC35",
    presets: [],
  },
  fanModes: {
    label: "Fan speeds",
    one: "fan speed",
    ordered: true,
    note: "Matched against the captured codes in this order.",
    presets: ["auto", "low", "mid", "medium", "high", "quiet", "turbo"],
    fill: ["auto", "low", "mid", "high"],
  },
  swingModes: {
    label: "Swing positions",
    one: "swing position",
    ordered: true,
    note: "Leave empty unless the swing setting is part of the unit's packets.",
    presets: ["auto", "1", "2", "3", "4", "5", "swing", "off"],
    fill: ["auto", "1", "2", "3"],
  },
  speed: {
    label: "Speeds",
    one: "speed",
    ordered: true,
    note: "Slowest first.",
    presets: ["low", "mid", "medium", "high", "lowest", "highest"],
    fill: ["low", "mid", "high"],
  },
  brightness: {
    label: "Brightness steps",
    one: "step",
    numeric: true,
    ordered: true,
    note: "Dimmest first, on Home Assistant's 1–255 scale.",
    presets: [10, 64, 128, 192, 255],
    fill: [10, 128, 255],
  },
  colorTemperature: {
    label: "Colour temperatures",
    one: "temperature",
    numeric: true,
    ordered: true,
    note: "In kelvin, warmest first.",
    presets: [2700, 3000, 4000, 5000, 6500],
    fill: [2700, 4000, 6500],
  },
  sources: {
    label: "Sources and channels",
    one: "source",
    presets: ["HDMI1", "HDMI2", "HDMI3", "AV", "TV", "USB"],
  },
  presets: {
    label: "One-touch buttons",
    one: "button",
    presets: ["turbo", "eco", "sleep", "quiet", "powerful", "comfort"],
  },
  extraCommands: {
    label: "Other buttons",
    one: "button",
    note: "Called by name from the hub_ir.send_command service.",
    presets: [],
  },
};

/** Suggestions for the free-form button list, by what the device is. */
const EXTRA_PRESETS = {
  climate: ["display", "beep", "clean", "ionizer", "timer"],
  fan: ["timer", "natural", "light"],
  light: ["flash", "scene", "favourite"],
  media_player: [
    "menu",
    "home",
    "back",
    "exit",
    "up",
    "down",
    "left",
    "right",
    "ok",
    "info",
    "guide",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
  ],
  switch: ["input_cd", "input_aux", "volume_up", "volume_down"],
};

/** Fill {name} placeholders. Values are substituted verbatim, so escape first. */
function fill(template, values = {}) {
  return String(template).replace(/\{(\w+)\}/g, (whole, name) =>
    name in values ? String(values[name]) : whole
  );
}

const MAX_NAME_LENGTH = 64;

/**
 * Above this a device file cannot be pasted into an issue by hand.
 *
 * A three-mode air conditioner comes to about 23 kB, so climate files are
 * always attachments and a switch or a fan can be pasted.
 */
const PASTEABLE_BYTES = 4000;

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
  .grid.wide { grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); }
  .list + .list { margin-top: 1rem; }
  ol.items { margin: .3rem 0 .5rem; padding-left: 1.7rem; font-size: .9rem; }
  ol.items li { padding: .1rem 0; }
  .item { display: flex; align-items: center; gap: .5rem; }
  .item-name { flex: 1 1 auto; overflow-wrap: anywhere; }
  .item-tools { display: flex; gap: .2rem; flex: 0 0 auto; }
  button.icon { padding: .15rem .45rem; line-height: 1.1; font-size: .85rem; }
  .empty { margin: .3rem 0 .5rem; font-style: italic; }
  .addrow { flex-wrap: nowrap; margin-top: .25rem; }
  .addrow input { flex: 1 1 auto; min-width: 6rem; }
  .addrow button { flex: 0 0 auto; }
  .presets { margin-top: .4rem; }
  .presets .chip { font-size: .8rem; padding: .2rem .5rem; }
  .listerr { margin-top: .4rem; }
  .draftrow { padding: .5rem 0; align-items: flex-start; }
  .draftrow + .draftrow { border-top: 1px solid var(--divider-color, #eee); }
  .draftrow .bar { margin: .35rem 0 0; max-width: 18rem; }
  a.button-link {
    display: inline-block; text-decoration: none; font: inherit;
    padding: .5rem .9rem; border-radius: 6px;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
  }
  textarea {
    border: 1px solid var(--divider-color, #ccc); border-radius: 6px;
    background: var(--card-background-color, #fff); color: var(--primary-text-color);
  }
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
      // Per-list scratch space, none of which is ever sent to the server:
      // what is currently typed in each add box, the last thing refused and
      // why, and which input to put the caret back into after a re-render.
      draft: {},
      listError: {},
      focus: null,
      // A capture-session setting, deliberately not in spec so it can never
      // reach build_device_file.
      toggle: false,
      // Permission to replace a device file that already exists.
      overwrite: false,
      // The file just saved, ready to copy or download.
      export: null,
      showRaw: false,
      copied: false,
      // Unfinished recordings parked on the server. `drafts` holds the summary
      // rows the setup screen lists; `draftKey` is which of them this session
      // is, so that changing the device code moves the draft rather than
      // leaving the old one behind as an orphan.
      drafts: [],
      draftKey: null,
      draftBusy: false,
      draftCleared: false,
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

  /** The display name of a device type. */
  _platformLabel(platform) {
    return PLATFORM_LABELS[platform];
  }

  /** Turn a failed websocket call into a message. */
  _describe(err) {
    return describe(err);
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
      this._state.status = { kind: "error", text: this._describe(err) };
    }
    await this._refreshCustomCodes();
    await this._refreshDrafts();
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

  /**
   * List the unfinished recordings, across every platform.
   *
   * Not filtered by the platform currently selected: a draft has to be findable
   * whatever the dropdown happens to be showing.
   */
  async _refreshDrafts() {
    try {
      const { drafts } = await this._call({ type: "hub_ir/draft_list" });
      this._state.drafts = drafts;
    } catch {
      this._state.drafts = [];
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
      this._set({ status: { kind: "error", text: this._describe(err) } });
    }
  }

  /**
   * Load an existing device file and continue from what it already records.
   *
   * The server derives the spec and the codes, including which modes the file
   * declares as ignoring fan speed or temperature, so only the missing codes
   * have to be captured.
   */
  async _loadTemplate() {
    const code = Number(this._state.templateCode);
    if (!code) {
      this._set({
        status: { kind: "error", text: "Enter a device code." },
      });
      return;
    }

    try {
      const result = await this._call({
        type: "hub_ir/get",
        platform: this._state.platform,
        device_code: code,
      });

      const spec = {
        ...structuredClone(DEFAULT_SPEC[this._state.platform]),
        ...result.spec,
      };
      spec.models = [...(result.spec.supportedModels || [])];
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
          text: fill(
            "Loaded device code {code}: {kept} code(s) already recorded. " +
              "Saving writes to device code {target}; the original is unchanged.",
            { code, kept, target: this._state.deviceCode }
          ),
        },
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: this._describe(err) } });
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
    spec.supportedModels = (spec.models || [])
      .map((entry) => String(entry).trim())
      .filter(Boolean);
    delete spec.models;

    if (this._state.platform === "climate") {
      // A preset has to be captured from a state that can be named, so drop
      // them entirely if the lists they refer to have been emptied since.
      if (!(spec.operationModes || []).length || !(spec.fanModes || []).length) {
        spec.presets = [];
      }
      // Send the resolved baseline rather than whatever was last clicked, so
      // the capture labels and the saved file cannot disagree.
      spec.presetBaseline = spec.presets.length ? this._presetBaseline() : {};
    }

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
          toggle: this._state.toggle,
        });
        this._state.codes[target.key] = code;
        delete this._state.skipped[target.key];
        this._advance();
      } catch (err) {
        this._set({ running: false, status: { kind: "error", text: this._describe(err) } });
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
   * is nothing to test immediately after a capture, because the panel has
   * already moved on.
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
      this._set({
        status: { kind: "error", text: "No code captured yet." },
      });
      return;
    }
    try {
      await this._call({
        type: "hub_ir/send",
        remote_entity_id: this._state.remote,
        code,
      });
      this._set({
        status: {
          kind: "ok",
          text: fill("Sent the code for {label}.", { label: cell.label }),
        },
      });
    } catch (err) {
      this._set({ status: { kind: "error", text: this._describe(err) } });
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
        overwrite: this._state.overwrite,
      });

      // Fetched now rather than when Copy is pressed: the clipboard API has to
      // run inside the click that asked for it, and a websocket round trip in
      // that handler risks losing the user activation. A failure here is not
      // fatal; the file is written either way.
      let exported = null;
      try {
        exported = await this._export(result.device_code);
      } catch {
        exported = null;
      }

      // The file on disk supersedes the draft and reopens through the template
      // loader, so discarding the draft loses nothing. Doing it here keeps the
      // list on the first screen to work that is still outstanding.
      const cleared = Boolean(this._state.draftKey);
      if (cleared) {
        await this._dropDraft(this._state.draftKey);
        await this._refreshDrafts();
      }

      this._set({
        step: "saved",
        saved: result,
        export: exported,
        showRaw: false,
        copied: false,
        status: null,
        draftKey: null,
        draftCleared: cleared,
        entityName: defaultName(this._state.spec, this._state.platform),
        creating: false,
        // Clearing this matters: saving a second file after a create would
        // otherwise show the first entity's success over the new file.
        created: null,
      });
    } catch (err) {
      const text =
        err && err.code === "already_exists"
          ? fill(
              "{message}. Select “Replace the existing file”, or choose another " +
                "device code in step 1.",
              { message: this._describe(err) }
            )
          : this._describe(err);
      this._set({ status: { kind: "error", text } });
    }
  }

  // -- drafts --------------------------------------------------------------

  /** The key a draft is filed under, matching drafts.py::draft_key. */
  _draftKey() {
    return `${this._state.platform}/${this._state.deviceCode}`;
  }

  /**
   * Store the current session on the server.
   *
   * Everything the panel cannot recompute is sent: the spec, the codes captured
   * so far, the skip marks and the cursor. The capture plan is not. Resuming
   * asks the server to rebuild it, so a draft cannot restore a stale plan.
   */
  async _saveDraft() {
    const s = this._state;
    if (s.draftBusy) return;

    const previous = s.draftKey;
    const key = this._draftKey();
    this._set({ draftBusy: true, status: null });

    try {
      await this._call({
        type: "hub_ir/draft_save",
        platform: s.platform,
        device_code: s.deviceCode,
        spec: this._specForServer(),
        codes: s.codes,
        skipped: s.skipped,
        index: s.index,
        toggle: s.toggle,
        remote_entity_id: s.remote,
        total: s.cells.length,
      });
    } catch (err) {
      this._set({
        draftBusy: false,
        status: { kind: "error", text: this._describe(err) },
      });
      return;
    }

    // The device code is half the key, so changing it part way through would
    // otherwise leave the earlier draft behind with nothing pointing at it.
    if (previous && previous !== key) await this._dropDraft(previous);

    this._state.draftKey = key;
    await this._refreshDrafts();
    this._set({
      draftBusy: false,
      status: { kind: "ok", text: "Draft saved. It is listed on the first screen." },
    });
  }

  /** Delete one draft by key, ignoring failures. */
  async _dropDraft(key) {
    const [platform, code] = String(key).split("/");
    try {
      const { deleted } = await this._call({
        type: "hub_ir/draft_delete",
        platform,
        device_code: Number(code),
      });
      return deleted;
    } catch {
      return false;
    }
  }

  /** Restore a draft and continue from where it stopped. */
  async _resumeDraft(key) {
    const [platform, code] = String(key).split("/");

    let draft;
    try {
      ({ draft } = await this._call({
        type: "hub_ir/draft_get",
        platform,
        device_code: Number(code),
      }));
    } catch (err) {
      this._set({ status: { kind: "error", text: this._describe(err) } });
      await this._refreshDrafts();
      this._render();
      return;
    }

    const spec = {
      ...structuredClone(DEFAULT_SPEC[draft.platform]),
      ...draft.spec,
    };
    spec.models = [...(draft.spec.supportedModels || [])];
    delete spec.supportedModels;

    const state = this._state;
    Object.assign(state, {
      platform: draft.platform,
      deviceCode: draft.device_code,
      spec,
      codes: draft.codes || {},
      skipped: draft.skipped || {},
      cells: [],
      index: 0,
      toggle: Boolean(draft.toggle),
      overwrite: false,
      templateCode: "",
      entityName: "",
      created: null,
      saved: null,
      draftKey: key,
      draftCleared: false,
      status: null,
    });

    // Only if it still exists: a Broadlink can be replaced between sessions,
    // and pointing at an entity that is gone would fail at the first capture
    // with a message about the wrong thing.
    if (state.remotes.some((remote) => remote.entity_id === draft.remote_entity_id)) {
      state.remote = draft.remote_entity_id;
    }

    await this._refreshCustomCodes();

    // A draft saved before capturing began has no plan to return to. Leave it
    // on the settings screen with everything filled in, rather than pushing an
    // incomplete spec through hub_ir/plan to produce an error.
    if (!draft.total) {
      this._set({
        status: {
          kind: "ok",
          text: "Draft loaded. Review the settings, then build the list of codes.",
        },
      });
      return;
    }

    await this._buildPlan();

    // _buildPlan places the cursor on the first gap. Restore the draft's
    // position, but only if that cell still exists: the spec may have been
    // edited since, or a newer version may plan the same device differently.
    if (this._state.step === "capture" && draft.index < this._state.cells.length) {
      this._set({ index: draft.index });
    }
  }

  /** Discard a draft, after confirmation. */
  async _deleteDraft(key) {
    const summary = this._state.drafts.find((entry) => entry.key === key);
    const label = (summary && summary.label) || key;

    if (
      !confirm(
        fill("Delete the draft for {label}? Its codes are not stored anywhere else.", {
          label,
        })
      )
    ) {
      return;
    }

    await this._dropDraft(key);
    if (this._state.draftKey === key) this._state.draftKey = null;
    await this._refreshDrafts();
    this._render();
  }

  /** True when saving now would replace one of the user's own recordings. */
  _wouldOverwrite() {
    return this._state.customCodes.includes(Number(this._state.deviceCode));
  }

  /** Warn, and offer permission, when the chosen code is already taken. */
  _overwriteWarning() {
    if (!this._wouldOverwrite()) return "";
    return `<div class="status error">${fill(
      "Device code {code} already exists. Saving replaces it.",
      { code: esc(this._state.deviceCode) }
    )}</div>
      <div class="row" style="margin-top:.5rem">
        ${chip("overwrite", "Replace the existing file", this._state.overwrite)}
      </div>`;
  }

  /** Re-ask which code is free; the answer goes stale as soon as one is saved. */
  async _refreshNextCode() {
    try {
      const info = await this._call({ type: "hub_ir/info" });
      this._state.nextCode = info.next_code;
    } catch {
      // Keep the previous value rather than failing the screen over it.
    }
  }

  /** Turn the device file just saved into a live entity, without a restart. */
  async _create() {
    const s = this._state;
    const name = String(s.entityName || "").trim();
    if (!name) {
      this._set({ status: { kind: "error", text: "Enter a name." } });
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
      this._set({
        creating: false,
        status: { kind: "error", text: this._describe(err) },
      });
    }
  }

  // -- rendering -----------------------------------------------------------

  _render() {
    const root = this.shadowRoot;
    root.innerHTML = `<style>${STYLES}</style>
      <header>${LOGO}<h1>HubIR</h1></header>
      <div class="wrap">${this._body()}</div>`;
    this._bind();

    // The add boxes are the only inputs that must survive a deliberate
    // re-render: Enter clears one and the user carries on typing into it.
    const focus = this._state.focus;
    if (focus) {
      this._state.focus = null;
      const node = root.getElementById(focus);
      if (node) {
        node.focus();
        if (node.setSelectionRange) {
          node.setSelectionRange(node.value.length, node.value.length);
        }
      }
    }
  }

  _body() {
    if (this._state.step === "setup") return this._setupView();
    if (this._state.step === "capture") return this._captureView();
    return this._savedView();
  }

  /**
   * The unfinished recordings, listed above everything else.
   *
   * Deliberately not numbered by _step(). The numbered cards are the steps of a
   * new recording and they match docs/PANEL.md in order, so a step inserted
   * here would renumber the documentation.
   */
  _draftsCard() {
    const drafts = this._state.drafts;
    if (!drafts.length) return "";

    return `
      <div class="card">
        <h2>Drafts</h2>
        <p class="muted">
          Stored on this Home Assistant, not in this browser. Any device can
          resume them.
        </p>
        ${drafts.map((draft) => this._draftRow(draft)).join("")}
      </div>
    `;
  }

  _draftRow(draft) {
    const total = draft.total || 0;
    const label = draft.label || this._platformLabel(draft.platform);
    const percent = total
      ? Math.min(100, Math.round(((draft.done + draft.skipped) / total) * 100))
      : 0;

    const facts = [
      this._platformLabel(draft.platform),
      String(draft.device_code),
      total
        ? fill("{done} of {total} · {percent}%", {
            done: draft.done + draft.skipped,
            total,
            percent,
          })
        : "settings only, no codes captured",
      this._when(draft.updated),
    ].filter(Boolean);

    return `
      <div class="item draftrow">
        <div class="item-name">
          <div>${esc(label) || "&mdash;"}</div>
          <div class="muted">${esc(facts.join(" · "))}</div>
          ${total ? `<div class="bar"><div style="width:${percent}%"></div></div>` : ""}
        </div>
        <div class="item-tools">
          <button data-draft="${esc(draft.key)}" data-act="resume">Resume</button>
          <button class="icon danger" data-draft="${esc(draft.key)}" data-act="delete"
            aria-label="${esc(
              fill("Delete the draft for {label}", { label: label || draft.key })
            )}">&#10005;</button>
        </div>
      </div>
    `;
  }

  /** An ISO timestamp in the browser's locale format, or "" if unreadable. */
  _when(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  }

  _setupView() {
    const s = this._state;
    this._cardNumber = 0;
    const learnable = s.remotes.filter((r) => r.can_learn);

    return `
      ${this._draftsCard()}
      <div class="card">
        <h2>${this._step("Device")}</h2>
        <div class="grid">
          <div>
            <label for="platform">Device type</label>
            <select id="platform">
              ${Object.keys(PLATFORM_LABELS)
                .map(
                  (value) =>
                    `<option value="${value}"${
                      value === s.platform ? " selected" : ""
                    }>${esc(this._platformLabel(value))}</option>`
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
            : `<div class="status error">
                 No Broadlink remote found. Set up the Broadlink integration first;
                 only its remotes can learn codes.
               </div>`
        }
        ${this._overwriteWarning()}
      </div>

      <div class="card">
        <h2>${this._step("Identification")}</h2>
        <div class="grid">
          <div>
            <label for="manufacturer">Manufacturer</label>
            <input id="manufacturer" value="${esc(s.spec.manufacturer)}"
                   placeholder="Daikin" />
          </div>
          <div>${this._listEditor("models")}</div>
        </div>

        <p class="muted" style="margin-top:1rem">
          Any existing device code can be loaded as a starting point. Its settings
          and every code it holds are carried over, leaving only the gaps to
          capture. Saving writes to your own device code; the original is
          unchanged.
        </p>
        <div class="row">
          <input id="template_code" type="number" placeholder="e.g. 1000"
                 value="${esc(s.templateCode)}" style="max-width:10rem" />
          <button id="load_template">Load device file</button>
        </div>
        ${
          s.customCodes.length
            ? `<div class="row" style="margin-top:.5rem">
                 <span class="muted">Your recordings:</span>
                 ${s.customCodes
                   .map(
                     (code) =>
                       `<span class="chip" data-reopen="${code}" role="button">${code}</span>
                        <button type="button" class="icon" data-download="${code}"
                          title="${esc(
                            fill("Download {code}.json — keep a copy before reinstalling", {
                              code,
                            })
                          )}"
                          aria-label="${esc(fill("Download {code}.json", { code }))}"
                          >&#11015;</button>`
                   )
                   .join("")}
               </div>`
            : ""
        }
      </div>

      ${this._specView()}
      ${this._presetCard()}
      ${this._extrasCard()}

      <div class="row">
        <button class="primary" id="plan" ${learnable.length ? "" : "disabled"}>
          Build the list of codes
        </button>
        <button id="save_draft" ${s.draftBusy ? "disabled" : ""}>Save draft</button>
      </div>
      <p class="muted">
        Saving a draft here preserves the settings above, so the list of codes
        does not have to be declared again.
      </p>
      ${this._statusView()}
    `;
  }

  /**
   * Number the setup cards in the order they are rendered.
   *
   * Rendering is one synchronous pass, so a counter is safe, and it keeps the
   * headings in step with PANEL.md when a card is added or made conditional.
   */
  _step(title) {
    this._cardNumber = (this._cardNumber || 0) + 1;
    return `${this._cardNumber} · ${esc(title)}`;
  }

  /**
   * The one-touch buttons: Turbo, Eco, Sleep. Climate only.
   *
   * The explanation is on this card rather than on the capture screen, which can
   * state which settings to dial in but not why. Without it, a Turbo code gets
   * recorded from whatever the remote happened to show, and then forces the unit
   * back to that state every time it fires.
   */
  _presetCard() {
    const s = this._state;
    if (s.platform !== "climate") return "";

    const modes = s.spec.operationModes || [];
    const fanModes = s.spec.fanModes || [];

    if (!modes.length || !fanModes.length) {
      return `<div class="card">
        <h2>${this._step("One-touch buttons")}</h2>
        <div class="status error">
          Declare at least one operation mode and one fan speed first. A
          one-touch button has to be recorded from a defined state.
        </div>
      </div>`;
    }

    const baseline = this._presetBaseline();
    const option = (value, selected) =>
      `<option value="${esc(value)}"${
        String(value) === String(selected) ? " selected" : ""
      }>${esc(value)}</option>`;

    return `<div class="card">
      <h2>${this._step("One-touch buttons (Turbo, Eco, Sleep)")}</h2>
      <p class="muted">
        On most air conditioners these buttons do <strong>not</strong> send a
        discrete &ldquo;turbo on&rdquo; packet. They transmit the unit&rsquo;s
        entire state &mdash; mode, fan speed and temperature &mdash; with one
        additional bit set. A code recorded here therefore returns the unit to
        whichever state the remote was displaying at the time. Declare that state
        below. It is shown on every capture screen and written into the device
        file.
      </p>
      <div class="grid" style="margin-top:.75rem">
        <div>
          <label for="presetBaseMode">Record from this mode</label>
          <select id="presetBaseMode">
            ${modes.map((mode) => option(mode, baseline.operationMode)).join("")}
          </select>
        </div>
        <div>
          <label for="presetBaseFanMode">Fan speed</label>
          <select id="presetBaseFanMode">
            ${fanModes.map((fan) => option(fan, baseline.fanMode)).join("")}
          </select>
        </div>
        <div>
          <label for="presetBaseTemperature">Temperature</label>
          <input id="presetBaseTemperature" type="number" step="${s.spec.precision}"
            min="${s.spec.minTemperature}" max="${s.spec.maxTemperature}"
            value="${baseline.temperature}" />
        </div>
      </div>
      <div style="margin-top:1rem">${this._listEditor("presets")}</div>
    </div>`;
  }

  /** The base state presets are captured from, resolved as the server would. */
  _presetBaseline() {
    const spec = this._state.spec;
    const declared = spec.presetBaseline || {};
    const modes = spec.operationModes || [];
    const fanModes = spec.fanModes || [];

    const steps = [];
    for (
      let value = spec.minTemperature;
      value <= spec.maxTemperature;
      value = Math.round((value + spec.precision) * 100) / 100
    ) {
      steps.push(value);
    }

    return {
      operationMode: modes.includes(declared.operationMode)
        ? declared.operationMode
        : modes[0],
      fanMode: fanModes.includes(declared.fanMode) ? declared.fanMode : fanModes[0],
      // The middle step, matching the server's default.
      temperature: steps.includes(declared.temperature)
        ? declared.temperature
        : steps[Math.floor(steps.length / 2)],
    };
  }

  /** Describe the preset base state the way the capture labels spell it. */
  _presetBaseLabel() {
    const baseline = this._presetBaseline();
    const unit = this._state.spec.temperatureUnit === "F" ? "°F" : "°C";
    return [
      baseline.operationMode,
      baseline.fanMode,
      `${baseline.temperature}${unit}`,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  /** Free-form buttons, for every platform. */
  _extrasCard() {
    const s = this._state;
    return `<div class="card">
      <h2>${this._step("Other buttons")}</h2>
      <p class="muted">
        Any remaining key on the remote: a menu key, the arrows, the digits, an
        LED toggle, a preferred input. These are not bound to the entity&rsquo;s
        standard controls. They are called by name from the
        <code>hub_ir.send_command</code> service and from scripts. Use short
        lower-case names.
      </p>
      <div style="margin-top:.75rem">
        ${this._listEditor("extraCommands", {
          presets: EXTRA_PRESETS[s.platform] || [],
        })}
      </div>
    </div>`;
  }

  /** Return the description of a list field, with any per-call overrides. */
  _listConfig(field, options = {}) {
    return { presets: [], ...LIST_FIELDS[field], ...options };
  }

  /**
   * Render one editable, ordered list.
   *
   * The add box's contents live in state.draft so a keystroke never re-renders;
   * every other action does re-render, because a button press has no caret to
   * lose.
   */
  _listEditor(field, options = {}) {
    const config = this._listConfig(field, options);
    const list = this._state.spec[field] || [];
    const error = this._state.listError[field];
    const addId = `list_add_${field}`;

    // An <ol> rather than a <ul>: the number the user sees is the position the
    // server will match against, so it is content, not decoration.
    const items = list.length
      ? `<ol class="items">${list
          .map((entry, index) => {
            const name = esc(entry);
            return `<li><div class="item">
              <span class="item-name">${name}</span>
              <span class="item-tools">
                <button type="button" class="icon" data-list="${field}" data-act="up"
                  data-index="${index}"
                  aria-label="${fill("Move {name} up", { name })}"
                  ${index === 0 ? "disabled" : ""}>&#8593;</button>
                <button type="button" class="icon" data-list="${field}" data-act="down"
                  data-index="${index}"
                  aria-label="${fill("Move {name} down", { name })}"
                  ${index === list.length - 1 ? "disabled" : ""}>&#8595;</button>
                <button type="button" class="icon danger" data-list="${field}"
                  data-act="remove" data-index="${index}"
                  aria-label="${fill("Remove {name}", { name })}">&#10005;</button>
              </span>
            </div></li>`;
          })
          .join("")}</ol>`
      : `<p class="muted empty">Empty.</p>`;

    // Only offered while the list is empty, and only where there is a
    // conventional answer: one click instead of typing four names.
    const fillChip =
      !list.length && config.fill
        ? `<span class="chip" role="button" data-list="${field}" data-act="fill"
             >${fill("Use {items}", { items: config.fill.join(" · ") })}</span>`
        : "";

    // A suggestion already in the list is not rendered at all. Hiding beats
    // disabling: the row stays short and there is no inert control to explain.
    const suggestions = config.presets
      .filter((entry) => !list.some((existing) => String(existing) === String(entry)))
      .map(
        (entry) =>
          `<span class="chip" role="button" data-list="${field}" data-act="preset"
             data-value="${esc(entry)}">+ ${esc(entry)}</span>`
      )
      .join("");

    return `<div class="list">
      <label for="${addId}">${esc(config.label)}${
        config.ordered ? ` <span class="muted">— order matters</span>` : ""
      }</label>
      ${config.note ? `<p class="muted" style="margin:.1rem 0 .3rem">${esc(config.note)}</p>` : ""}
      ${items}
      <div class="row addrow">
        <input id="${addId}" type="text" autocomplete="off"
          ${config.numeric ? 'inputmode="numeric"' : ""}
          placeholder="${esc(fill("add a {one}", { one: config.one }))}"
          value="${esc(this._state.draft[field] || "")}" />
        <button type="button" data-list="${field}" data-act="add">Add</button>
      </div>
      ${
        fillChip || suggestions
          ? `<div class="chips presets">${fillChip}${suggestions}</div>`
          : ""
      }
      ${error ? `<div class="status error listerr">${esc(error)}</div>` : ""}
    </div>`;
  }

  /** Apply an add / remove / reorder / suggestion action to a spec list. */
  _listAction(field, act, data = {}) {
    const config = this._listConfig(field, {
      presets: EXTRA_PRESETS[this._state.platform] || [],
    });
    const list = [...(this._state.spec[field] || [])];
    const index = Number(data.index);

    if (act === "remove") {
      list.splice(index, 1);
    } else if (act === "up" && index > 0) {
      [list[index - 1], list[index]] = [list[index], list[index - 1]];
    } else if (act === "down" && index < list.length - 1) {
      [list[index], list[index + 1]] = [list[index + 1], list[index]];
    } else if (act === "fill") {
      if (list.length || !config.fill) return;
      list.push(...config.fill);
    } else if (act === "add" || act === "preset") {
      const raw = act === "preset" ? data.value : this._state.draft[field];
      const outcome = listValue(raw, config, list);
      if (outcome.error) {
        this._state.listError[field] = outcome.error;
        this._state.focus = `list_add_${field}`;
        this._render();
        return;
      }
      list.push(outcome.value);
      this._state.draft[field] = "";
    }

    delete this._state.listError[field];
    // One rule for every action, including a suggestion chip: the caret returns
    // to the add box, so building a list stays continuous.
    this._state.focus = `list_add_${field}`;
    this._setSpec({ [field]: list });
  }

  _specView() {
    const s = this._state;
    if (s.platform === "climate") return this._climateSpecView();

    const heading = "Capabilities";

    if (s.platform === "fan") {
      return `<div class="card">
        <h2>${this._step(heading)}</h2>
        ${this._listEditor("speed")}
        <div class="row" style="margin-top:.75rem">
          ${chip("hasDirection", "Reversible", s.spec.hasDirection)}
          ${chip("hasOscillate", "Oscillates", s.spec.hasOscillate)}
        </div>
      </div>`;
    }

    if (s.platform === "light") {
      return `<div class="card">
        <h2>${this._step(heading)}</h2>
        <div class="grid wide">
          <div>${this._listEditor("brightness")}</div>
          <div>${this._listEditor("colorTemperature")}</div>
        </div>
        <div class="row" style="margin-top:.75rem">
          ${chip("hasNight", "Has a night light", s.spec.hasNight)}
        </div>
      </div>`;
    }

    if (s.platform === "switch") {
      return `<div class="card">
        <h2>${this._step(heading)}</h2>
        <p class="muted">
          Most remotes have separate on and off keys. Some, projectors in
          particular, have a single power key whose code alternates. Select this
          to record that key instead.
        </p>
        <div class="row">
          ${chip("hasToggle", "One power button that toggles", s.spec.hasToggle)}
        </div>
      </div>`;
    }

    return `<div class="card">
      <h2>${this._step(heading)}</h2>
      <div class="chips">
        ${["on", "off", "volumeUp", "volumeDown", "mute", "previousChannel", "nextChannel"]
          .map((name) => chip(`button:${name}`, name, s.spec.buttons.includes(name)))
          .join("")}
      </div>
      <div style="margin-top:.75rem">${this._listEditor("sources")}</div>
    </div>`;
  }

  _climateSpecView() {
    const spec = this._state.spec;
    const modes = spec.operationModes;

    return `<div class="card">
      <h2>${this._step("Temperatures and modes")}</h2>
      <div class="grid">
        <div><label for="minTemperature">Minimum</label>
          <input id="minTemperature" type="number" step="any"
                 value="${spec.minTemperature}" /></div>
        <div><label for="maxTemperature">Maximum</label>
          <input id="maxTemperature" type="number" step="any"
                 value="${spec.maxTemperature}" /></div>
        <div><label for="precision">Step</label>
          <input id="precision" type="number" step="any"
                 value="${spec.precision}" /></div>
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

      <div class="grid wide" style="margin-top:1rem">
        <div>${this._listEditor("fanModes")}</div>
        <div>${this._listEditor("swingModes")}</div>
      </div>

      <div class="row" style="margin-top:.75rem">
        ${chip("hasOnCommand", "Separate power-on code", spec.hasOnCommand)}
      </div>

      <h2 style="margin-top:1.5rem">Mode dependencies</h2>
      <p class="muted">
        Most units ignore the temperature in <em>dry</em> and <em>fan only</em>,
        and some ignore the fan speed as well. Declaring this roughly halves the
        number of codes to capture: one code is written to every position it
        applies to.
      </p>
      <table>
        <tr>
          <th>Mode</th>
          <th>Responds to fan speed</th>
          <th>Responds to temperature</th>
        </tr>
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
    // Counted over the plan, not over the maps. Loading a template — or
    // resuming a draft — and then narrowing the spec leaves codes keyed to
    // cells the plan no longer has, and counting those would push the bar past
    // its own total. They are kept, not pruned: widening the spec again brings
    // them back, and build_device_file ignores a key it did not ask for.
    const done = s.cells.filter((cell) => s.codes[cell.key]).length;
    const skipped = s.cells.filter((cell) => s.skipped[cell.key]).length;
    const current = s.cells[s.index];
    const percent = total ? Math.round(((done + skipped) / total) * 100) : 0;

    return `
      <div class="card">
        <h2>Point the remote at the Broadlink</h2>
        ${
          current
            ? `<p class="muted">Set the remote to the following, then press send:</p>
               <div class="target">${esc(current.label)}</div>
               <p class="muted">${fill("{done} of {total} · {group}", {
                 done: done + skipped,
                 total,
                 group: esc(current.group),
               })}</p>`
            : `<div class="target">${fill("All {total} codes accounted for", {
                total,
              })}</div>`
        }
        ${
          current && current.group === "Presets"
            ? `<div class="status ok">${fill(
                "This code carries the unit's entire state. Set the remote to " +
                  "<strong>{state}</strong> before pressing it.",
                { state: esc(this._presetBaseLabel()) }
              )}</div>`
            : ""
        }
        <div class="bar"><div style="width:${percent}%"></div></div>
        <div class="cells">
          ${s.cells
            .map(
              (cell, i) =>
                `<div class="cell ${this._cellState(cell)}${
                  i === s.index ? " current" : ""
                }" data-index="${i}" role="button" tabindex="0"
                  title="${fill("{label} — select to return to it", {
                    label: esc(cell.label),
                  })}"></div>`
            )
            .join("")}
        </div>
        <p class="muted">Select a square to return to that code and capture it again.</p>

        <div class="row" style="margin-top:1rem">
          <button class="primary" id="run" ${s.running || !current ? "disabled" : ""}>
            Start capturing
          </button>
          <button id="one" ${s.running || !current ? "disabled" : ""}>Capture one</button>
          <button id="stop" ${s.running ? "" : "disabled"}>Stop</button>
          <button id="skip" ${s.running || !current ? "disabled" : ""}>Skip</button>
          <button id="test" ${s.running ? "disabled" : ""}>Test last code</button>
        </div>
        <div class="row" style="margin-top:.6rem">
          ${chip("toggle", "Two-packet button", s.toggle)}
        </div>
        ${
          s.toggle
            ? `<p class="muted" style="margin-top:.4rem">
                 Some remotes alternate between two packets for the same button;
                 Samsung power keys are the common case. The panel requests both
                 and stores them as a pair, and the integration sends them in
                 turn. Enable this only if a captured code works on every second
                 press.
               </p>`
            : ""
        }
        ${
          s.running
            ? `<p class="muted" style="margin-top:.75rem">
                 Listening. Each code times out after 30 seconds. Continue
                 pressing; the panel advances on its own.
               </p>`
            : ""
        }
        ${this._statusView()}
      </div>

      ${this._overwriteWarning()}
      <div class="row">
        <button id="back">Back to settings</button>
        <button id="save_draft" ${s.running || s.draftBusy ? "disabled" : ""}>
          Save draft
        </button>
        <button class="primary" id="save" ${
          done && !(this._wouldOverwrite() && !s.overwrite) ? "" : "disabled"
        }>
          ${fill("Save as device code {code}", { code: s.deviceCode })}
        </button>
      </div>
      ${
        skipped
          ? `<p class="muted">${fill(
              "{count} skipped. Those positions stay empty and the integration " +
                "refuses to transmit them.",
              { count: skipped }
            )}</p>`
          : ""
      }
      <p class="muted">
        <strong>Save draft</strong> stores the captured codes, the skipped
        positions and the current position, and lists the draft on the first
        screen.
      </p>
    `;
  }

  _savedView() {
    const s = this._state;

    return `<div class="card">
      <h2>Saved</h2>
      <p class="muted">${fill("Written to <code>{path}</code>.", {
        path: esc(s.saved.path),
      })}</p>
      ${
        s.saved.warnings && s.saved.warnings.length
          ? `<div class="status error"><strong>Warnings</strong>
             <ul>${s.saved.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`
          : `<div class="status ok">No gaps found.</div>`
      }
      ${
        s.draftCleared
          ? `<p class="muted">
               The draft has been discarded. This file replaces it and can be
               reopened from step 2 to add further codes.
             </p>`
          : ""
      }
    </div>

    ${s.created ? this._createdView() : this._createView()}
    ${this._shareView()}

    <div class="row"><button id="restart">Record another device</button></div>`;
  }

  /**
   * Offer to contribute the recording upstream.
   *
   * A device file is only useful to others if it can leave this machine, and
   * the coverage of this fork depends on how many files come back.
   */
  _shareView() {
    const x = this._state.export;

    const heading = "Contribute this file";

    if (!x) {
      return `<div class="card">
        <h2>${heading}</h2>
        <p class="muted">
          The file was written but could not be read back for export. It remains
          on disk at the path above.
        </p>
      </div>`;
    }

    return `<div class="card">
      <h2>${heading}</h2>
      <p class="muted">
        Contributing a device file adds it to the shipped database. Two steps, in
        this order:
      </p>
      <div class="row" style="margin-top:.75rem">
        <button id="copy_json">Copy JSON</button>
        <button id="download_json">${fill("Download {filename}", {
          filename: esc(x.filename),
        })}</button>
      </div>
      <p class="muted" style="margin-top:.4rem">
        ${formatBytes(x.bytes)} — ${
          x.bytes > PASTEABLE_BYTES
            ? "too large for an issue URL. Attach it or paste it into the issue body."
            : "small enough to paste directly into the issue."
        }
      </p>
      <div class="row" style="margin-top:.75rem">
        <a class="button-link" href="${esc(x.issue_url)}" target="_blank"
          rel="noopener">Open a pre-filled issue</a>
      </div>
      <p class="muted" style="margin-top:.4rem">
        The manufacturer, models, code count and version numbers are filled in.
        Add the file you copied or downloaded to the section reserved for it.
        <strong>The link carries no codes</strong>, and nothing is submitted until
        you confirm on GitHub.
      </p>
      <div class="row" style="margin-top:.75rem">
        <button id="show_raw">${
          this._state.showRaw ? "Hide raw JSON" : "Show raw JSON"
        }</button>
      </div>
      ${
        this._state.showRaw
          ? `<textarea id="raw_json" readonly rows="12"
               style="width:100%;box-sizing:border-box;margin-top:.5rem;font-family:monospace;font-size:.75rem"
               >${esc(x.json)}</textarea>`
          : ""
      }
      ${
        this._state.copied
          ? `<div class="status ok">
               Copied. Paste it into the issue, or attach the downloaded file.
             </div>`
          : ""
      }
    </div>`;
  }

  /** Fetch the file just written, so Copy and Download are instant. */
  async _export(deviceCode) {
    const code = deviceCode ?? this._state.saved.device_code;
    return this._call({
      type: "hub_ir/export",
      platform: this._state.platform,
      device_code: code,
    });
  }

  /** Put the JSON on the clipboard, or fall back to showing it. */
  async _copyJson() {
    const x = this._state.export;
    if (!x) return;

    try {
      await navigator.clipboard.writeText(x.json);
      this._set({ copied: true, showRaw: false });
    } catch {
      // Permission refused, or an embedding without the clipboard API. Showing
      // the text is worse but never a dead end.
      this._set({
        copied: false,
        showRaw: true,
        status: {
          kind: "error",
          text: "Clipboard access was refused. Select the text below and copy it.",
        },
      });
    }
  }

  /** Save the file to the user's machine. A Blob, not a fetch. */
  _downloadJson(payload = this._state.export) {
    if (!payload) return;

    const url = URL.createObjectURL(
      new Blob([payload.json], { type: "application/json" })
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = payload.filename;
    // Never appended to the shadow root: the next render would tear it out.
    link.click();
    URL.revokeObjectURL(url);
  }

  /**
   * Offer to create the entity here rather than through a text editor and a
   * restart.
   *
   * Everything the config flow needs is already known: the device type, the code
   * just written, and the remote the codes came through. The name is the only
   * remaining input, and it has a default. The server starts the flow; this side
   * does not need to know its steps.
   */
  _createView() {
    const s = this._state;

    return `<div class="card">
      <h2>Add to Home Assistant</h2>
      <p class="muted">No YAML and no restart; the entity appears immediately.</p>
      <div class="grid">
        <div>
          <label for="entity_name">Name</label>
          <input id="entity_name" value="${esc(s.entityName)}"
                 placeholder="${esc(
                   fill("My {platform}", {
                     platform: this._platformLabel(s.platform),
                   })
                 )}" />
        </div>
        <div>
          <label for="remote">Broadlink remote</label>
          ${this._remoteSelect()}
        </div>
      </div>
      <p class="muted" style="margin-top:.5rem">
        Set to the remote the codes were captured through. Change it only if this
        device is controlled by a different one.
      </p>
      <div class="row" style="margin-top:1rem">
        <button class="primary" id="create" ${s.creating ? "disabled" : ""}>
          ${s.creating ? "Creating…" : "Create the entity"}
        </button>
      </div>
      ${this._statusView()}
      <details style="margin-top:1rem">
        <summary class="muted">Configure in configuration.yaml instead</summary>
        <p class="muted">
          For entities kept in YAML. This requires a restart. Configure a device
          one way or the other, not both: the two methods are independent.
        </p>
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
            ? fill(
                "<code>{label}</code> already uses device code {code}. It has " +
                  "been reloaded and is running on the file just saved.",
                { label: esc(label), code: esc(s.saved.device_code) }
              )
            : fill("Created <code>{label}</code>. No restart required.", {
                label: esc(label),
              })
        }
      </div>
      <div class="row" style="margin-top:1rem">
        ${created.entity_id ? `<button class="primary" id="show">Show entity</button>` : ""}
        <a href="/config/integrations/integration/hub_ir">Manage HubIR devices</a>
      </div>
      <p class="muted" style="margin-top:.75rem">
        Rename it, assign an area, or point it at a different remote in Settings
        &rarr; Devices &amp; services &rarr; HubIR.
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
              r.can_learn ? "" : esc(" — not a Broadlink remote")
            }</option>`
        )
        .join("")}
    </select>`;
  }

  /** The manual alternative, for entities kept in YAML. */
  _yaml() {
    const s = this._state;
    const name =
      String(s.entityName || "").trim() ||
      fill("My {platform}", { platform: this._platformLabel(s.platform) });
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
        toggle: false,
        overwrite: false,
        // A different platform is a different recording, so the session stops
        // being the draft it was; saving now must not overwrite that one.
        draftKey: null,
        draftCleared: false,
      });
      this._render();
      await this._refreshCustomCodes();
      await this._refreshNextCode();
      this._state.deviceCode = this._state.nextCode[platform];
      this._render();
    });
    on("remote", "change", (event) => this._set({ remote: event.target.value }));
    // Changing the number withdraws any permission given for the old one.
    on("device_code", "change", (event) =>
      this._set({ deviceCode: Number(event.target.value), overwrite: false })
    );

    on("manufacturer", "input", (event) => {
      this._state.spec.manufacturer = event.target.value;
    });

    // One listener for every list control, however many lists there come to be.
    // These carry data-list; the older chips carry data-key, so the two
    // dispatches cannot collide.
    const wrap = root.querySelector(".wrap");
    if (wrap) {
      wrap.addEventListener("click", (event) => {
        const node = event.target.closest("[data-list][data-act]");
        if (node) this._listAction(node.dataset.list, node.dataset.act, node.dataset);
      });
    }

    for (const field of Object.keys(LIST_FIELDS)) {
      // No re-render on input: the add box is the one field whose caret has to
      // survive typing.
      on(`list_add_${field}`, "input", (event) => {
        this._state.draft[field] = event.target.value;
      });
      on(`list_add_${field}`, "keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        this._listAction(field, "add");
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

    // The base state presets are captured from. Stored as one object so the
    // server receives it the same shape it writes into the device file.
    for (const [id, key] of [
      ["presetBaseMode", "operationMode"],
      ["presetBaseFanMode", "fanMode"],
      ["presetBaseTemperature", "temperature"],
    ]) {
      on(id, "change", (event) => {
        const value =
          key === "temperature" ? Number(event.target.value) : event.target.value;
        this._setSpec({
          presetBaseline: { ...this._presetBaseline(), [key]: value },
        });
      });
    }
    for (const node of root.querySelectorAll(".chip[data-key]")) {
      node.addEventListener("click", () => this._toggleChip(node.dataset.key));
    }

    // The draft rows. Their own attribute pair, so this dispatch cannot collide
    // with the list controls' data-list or the older chips' data-key.
    for (const node of root.querySelectorAll("[data-draft][data-act]")) {
      node.addEventListener("click", () => {
        const { draft, act } = node.dataset;
        if (act === "resume") this._resumeDraft(draft);
        else if (act === "delete") this._deleteDraft(draft);
      });
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
    on("save_draft", "click", () => this._saveDraft());
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
    on("copy_json", "click", () => this._copyJson());
    on("download_json", "click", () => this._downloadJson());
    on("show_raw", "click", () =>
      this._set({ showRaw: !this._state.showRaw, copied: false })
    );

    // Download any earlier recording. HACS installs only the component, so a
    // locally recorded file does not exist upstream to be downloaded again and
    // has to be copied off the machine before a reinstall.
    for (const node of root.querySelectorAll("[data-download]")) {
      node.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          this._downloadJson(await this._export(Number(node.dataset.download)));
        } catch (err) {
          this._set({ status: { kind: "error", text: this._describe(err) } });
        }
      });
    }
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
        entityName: "",
        creating: false,
        created: null,
        toggle: false,
        overwrite: false,
        // Cleared so that a failed create does not carry over to a fresh setup
        // screen as an error about nothing.
        status: null,
        draftKey: null,
        draftCleared: false,
      });
      // The recording just saved has to appear in the list immediately, and the
      // next free code is read from the filesystem rather than assumed to be the
      // last code plus one, which would write over an existing file.
      await this._refreshCustomCodes();
      await this._refreshNextCode();
      this._state.deviceCode = this._state.nextCode[this._state.platform];
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
    } else if (kind === "toggle" || kind === "overwrite") {
      // Session settings, not part of the spec: the final `else` writes into it.
      this._state[kind] = !this._state[kind];
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

/**
 * Normalise one typed entry, or explain why it cannot be added.
 *
 * Returns {value} or {error}. Rejecting explicitly matters: the field this
 * replaced accepted "low,, high" and silently dropped the gap, and accepted
 * "High" alongside "high" as two different keys in the command tree.
 */
function listValue(raw, config, list) {
  const text = String(raw ?? "").trim();

  if (!text) return { error: "Enter a value." };
  if (text.includes("/")) {
    return { error: "A name cannot contain a slash; it would split the command path." };
  }
  if (text.includes(",")) {
    return { error: "Add one entry at a time. Commas are not accepted." };
  }
  if (text.length > MAX_NAME_LENGTH) {
    return {
      error: fill("Maximum {max} characters.", { max: MAX_NAME_LENGTH }),
    };
  }

  if (config.numeric) {
    const number = Number(text);
    if (!Number.isFinite(number)) {
      return { error: "Numbers only." };
    }
    if (number <= 0) {
      return { error: "Must be a positive number." };
    }
    if (list.some((entry) => Number(entry) === number)) {
      return {
        error: fill("{value} is already in the list.", { value: number }),
      };
    }
    return { value: number };
  }

  if (list.some((entry) => String(entry).toLowerCase() === text.toLowerCase())) {
    return {
      error: fill("“{value}” is already in the list.", { value: text }),
    };
  }
  return { value: text };
}

/** Format a file size for someone deciding how to transfer it. */
function formatBytes(bytes) {
  const size = Number(bytes) || 0;
  if (size < 1024) return `${size} bytes`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function slugify(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** Derive a name from what was entered on the identification step. */
function defaultName(spec, platform) {
  const model = String((spec.models || [])[0] ?? "").trim();
  const guess = [String(spec.manufacturer || "").trim(), model]
    .filter(Boolean)
    .join(" ");
  if (guess) return guess;
  return fill("My {platform}", { platform: PLATFORM_LABELS[platform] });
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

/**
 * Turn a rejected websocket call into a message.
 *
 * Everything but the no-details case is the server's own message, raised in
 * Python where this file cannot reach it.
 */
function describe(err) {
  if (!err) return "The request failed.";
  return err.message || err.error || String(err);
}

customElements.define("hub-ir-panel", BroadlinkIrPanel);
