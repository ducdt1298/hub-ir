<img src="brand/logo.png" alt="HubIR" width="300">

# HubIR for Home Assistant

Control **climate devices**, **media players**, **fans** and **lights** over IR/RF
with a [Broadlink](https://www.home-assistant.io/integrations/broadlink/) remote
and a database of pre-recorded device codes.

This is a maintained, Broadlink-only fork of
[smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR), whose last
release predates the Home Assistant versions people actually run today.

> Upstream asks that forks not carry the SmartIR name, so this integration uses
> the domain `hub_ir`. See [Migrating from SmartIR](#migrating-from-smartir).

> **Renamed in 3.0.0.** This was `broadlink_ir`, in a repository called
> `broadlink-ir-hass`. If you installed that, see
> [Migrating from broadlink_ir](#migrating-from-broadlink_ir).

## Why this fork exists

Upstream SmartIR **cannot load at all** on current Home Assistant. Its
`__init__.py` starts with:

```python
from distutils.version import StrictVersion
```

`distutils` was removed from the standard library in Python 3.12, and Home
Assistant 2026.7 runs on Python 3.14. The import raises `ModuleNotFoundError`
before anything else happens, so every platform fails. Its self-updater also
calls `hass.components.persistent_notification`, and the `hass.components`
shim no longer exists.

This fork removes the self-updater entirely — HACS handles updates — drops the
four non-Broadlink controllers, and fixes a set of long-standing bugs.

## Requirements

- Home Assistant with a working [Broadlink](https://www.home-assistant.io/integrations/broadlink/)
  remote entity (`remote.*`).
- Minimum Home Assistant version: **2025.1.0** (see [Verified versions](#verified-versions)).

## Installation

### HACS

Add this repository as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/)
of type *Integration*, install it, and restart Home Assistant.

### Manual

Copy `custom_components/hub_ir` into your configuration directory:

```
<config directory>/
|-- custom_components/
|   |-- hub_ir/
|       |-- __init__.py
|       |-- climate.py
|       |-- config_flow.py
|       |-- const.py
|       |-- controller.py
|       |-- fan.py
|       |-- light.py
|       |-- media_player.py
|       |-- validation.py
|       |-- manifest.json
|       |-- strings.json
|       |-- translations/
|       |-- www/            <- the learning panel
|       |-- codes/          <- created on first use, see below
```

Then restart Home Assistant and add the integration from **Settings → Devices
& services → Add integration → HubIR**. That creates your first entity and, at
the same time, puts the [learning panel](#recording-a-device-of-your-own) in the
sidebar.

If you would rather configure everything in YAML, this line does the same for
the panel and nothing else. It is not needed once anything else has loaded the
integration:

```yaml
hub_ir:
```

### Where the device files come from

The 407 device files are **not** part of the installed integration: at 45 MB they
would dominate it. Home Assistant downloads each one the first time you use its
device code and caches it under
`custom_components/hub_ir/codes/<platform>/`. HACS preserves that cache
across updates.

So the first use of a device code needs Home Assistant to reach
`raw.githubusercontent.com`. For an air-gapped install, or to use your own
recording, copy the JSON there yourself — a file that is already present is never
downloaded:

```
custom_components/hub_ir/codes/climate/1000.json
```

Browse the files in [codes/](codes/) to find one to copy.

## Recording a device of your own

If your air conditioner is not in the tables below, the **HubIR** panel in
the sidebar records it — no Developer Tools, no SSH, no hand-written JSON.

It shows one code at a time in the order the buttons sit on your remote, and
moves on by itself the moment a code arrives, so recording a device is a run of
button presses rather than a session of copy-and-paste. It asks up front which
modes ignore the temperature or the fan speed, which is what turns the 180 codes
a typical air conditioner needs into around 120. Files are saved under device
codes from 90000 up, so they can never shadow a shipped one.

It can also start from a device file that is nearly right — any of the 407
shipped ones — carrying over its settings and every code it already holds, so
only the gaps are captured. Saving always writes to a code of your own, so the
original is never touched.

When it has saved the file it offers to create the entity there and then, so
recording a device the tables do not cover never involves a text editor or a
restart at all.

See [docs/PANEL.md](docs/PANEL.md).

## Configuration

Find your device's code in the tables below, then add it either way. The two
coexist, so an existing `configuration.yaml` keeps working untouched — but
configure any one device through one of them, not both, or you will end up with
two entities fighting over the same remote.

### From the UI, with no restart

**Settings → Devices & services → Add integration → HubIR**, pick air
conditioner, fan, light or TV, then fill in the name, the device code and the
Broadlink remote. A device code that cannot work is rejected in the form rather
than becoming a broken entity, and the entity appears immediately.

The entity is grouped into a device named after it, carrying the manufacturer
and model from the device file. **Configure** on the entry changes the remote,
the delay, the helper sensors and — for a media player — the source names; the
entity reloads itself, so there is still nothing to restart.

The device type and the device code are fixed when the entity is created. A
different device file is a different device: add another entity for it.

The [learning panel](#recording-a-device-of-your-own) offers to do all of this
for you the moment it has saved a recording.

### In `configuration.yaml`

Unchanged, and still fully supported:

```yaml
climate:
  - platform: hub_ir
    name: Office AC
    unique_id: office_ac
    device_code: 1000
    controller_data: remote.bedroom_remote
    temperature_sensor: sensor.temperature
    humidity_sensor: sensor.humidity
    power_sensor: binary_sensor.ac_power
```

YAML entities are not grouped into devices; see
[Known limitations](#known-limitations).

Per-platform options and the full code tables:

- [Climate](docs/CLIMATE.md) — 333 device files
- [Media player](docs/MEDIA_PLAYER.md) — 53 device files
- [Fan](docs/FAN.md) — 16 device files
- [Light](docs/LIGHT.md) — 5 device files
- [Learning panel](docs/PANEL.md) — recording a device the tables do not cover

Device files are downloaded from this repository on first use and cached under
`custom_components/hub_ir/codes/<platform>/`. To use your own recording,
drop the JSON file there and it will be used instead.

## Migrating from broadlink_ir

Version 3.0.0 renamed the integration to HubIR and moved it to the
[hub-ir](https://github.com/ducdt1298/hub-ir) repository. The domain changed with
it, which Home Assistant treats as a different integration entirely.

1. Copy any device files you recorded yourself out of
   `custom_components/broadlink_ir/codes/` — the ones numbered 90000 and up.
   Nothing else in that directory is worth keeping; the rest is re-downloaded.
2. In HACS, remove the old *Broadlink IR* custom repository and add
   `https://github.com/ducdt1298/hub-ir` instead. For a manual install, delete
   `custom_components/broadlink_ir` and copy `custom_components/hub_ir` in.
3. Put your own device files back under `custom_components/hub_ir/codes/`.
4. In `configuration.yaml`, change every `platform: broadlink_ir` to
   `platform: hub_ir`, and `broadlink_ir:` to `hub_ir:`.
5. Restart Home Assistant.

Entities are recreated, because the entity registry keys them by platform. If
you set `unique_id`, the old entities linger as unavailable — delete them in
**Settings → Devices & services → Entities**, then rename the new ones to the
old entity IDs so your automations keep working.

## Migrating from SmartIR

1. Remove the old `custom_components/smartir` directory (or uninstall it in HACS).
2. Install this integration.
3. In `configuration.yaml`, change every `platform: smartir` to
   `platform: hub_ir`, and drop the `smartir:` block if you had one.
4. Restart Home Assistant.

Because the platform name changes, entities are recreated. If you set
`unique_id`, rename the old entities in the entity registry — or accept the new
`_2` suffixed IDs and update your automations.

The `check_updates` and `update_branch` options are gone. If you leave them in
place, setup still succeeds and logs a warning telling you to remove them.

Two behaviour changes worth knowing about before you migrate:

- `controller_data` must now be a `remote.*` entity_id. It always had to be one
  for it to work; the difference is that a wrong value is now rejected at startup
  instead of failing silently on every command.
- A command that cannot be sent now makes the service call **fail** rather than
  only writing to the log. An automation that used to carry on regardless will
  now stop at that step — which is the point, but it is a change. See
  [Known limitations](#known-limitations).

## What changed against upstream

**Fixed — the integration now loads and runs on current Home Assistant**

- Removed the `distutils` import that made the integration unloadable on
  Python 3.12+ (Home Assistant 2024.4 and newer).
- Removed the self-updater, which also used the deleted `hass.components` API.
  Updates are HACS' job.
- Moved every filesystem access off the event loop. Home Assistant now reports
  blocking I/O inside the loop as a warning.

**Fixed — found by auditing this fork's own code**

- A bare `hub_ir:` line — what the docs tell you to add — failed config
  validation with *"expected a dictionary ... got None"*. Upstream has the same
  bug.
- Restoring the target temperature after a restart read it back in the *user's*
  unit, so a Fahrenheit device file on a Celsius system came back with a
  corrupted setpoint. A restored value outside the device's range is now
  discarded rather than advertised.
- Device files carry `_comment`/`$comment`/`_note` keys inside the command tree,
  sometimes as the first key of a level. The sparse-file fallback could have
  picked one and transmitted its prose as a code.
- A device file whose codes were never captured (`light/1040` is entirely empty
  placeholders, yet the docs list it as a Toshiba FRC-199T) produced an entity
  that silently did nothing. Setup now fails with an explanation.
- 7 codes in the database cannot be decoded even after re-padding. They now
  raise a message naming the device file instead of a bare `binascii` error.
- `codes/climate/1704.json` had the code for 25 °C with the code for 26 °C
  appended to it, making it undecodable. The duplicate was removed.
- The Pronto conversion's pulse-to-tick step truncates rather than rounds.
  That reads like a redundant `int()` cast, so it is now pinned byte-for-byte by
  a test — changing it would silently alter every emitted timing.

**Added — entities without editing YAML**

- A config flow: **Settings → Devices & services → Add integration → HubIR**
  creates an entity, and **Configure** changes its remote, delay and helper
  sensors afterwards with the entity reloading itself. Neither needs a restart,
  and neither needs SSH. YAML configuration is untouched and keeps working
  alongside it.
- Entities created that way are grouped into a Home Assistant device carrying
  the manufacturer and model from the device file — something a YAML platform
  cannot do at all.
- A device code that cannot produce a working entity — wrong hub, an encoding
  this fork cannot send, no usable operation modes — is now rejected in the form
  you are looking at, rather than becoming a broken entity and a traceback in
  the log a minute later.
- The learning panel finishes the job: after saving a recording it offers to
  create the entity, prefilled with the device code it just wrote and the remote
  you learned through. Saving over a device you already added reloads it, so
  extra codes take effect without a restart.
- HubIR has a logo. It lives in [brand/](brand/); the integrations page and HACS
  will show it once it is accepted into
  [home-assistant/brands](https://github.com/home-assistant/brands).

**Added — recording a device without leaving the browser**

- A sidebar panel that learns codes, shows what is left to capture, tests a code
  before keeping it, and writes a validated device file. Upstream had nothing
  here: adding a device meant Developer Tools, SSH into the server to read
  `.storage/broadlink_remote_<mac>_codes`, and hand-assembling the JSON. See
  [docs/PANEL.md](docs/PANEL.md).
- The rules that decide whether a device file is valid now live in one module
  (`device_file.py`), shared by the integration, the panel and
  `scripts/validate_codes.py`. The script used to carry its own copy of
  `is_recorded`, the packet checks and the annotation prefixes, which is exactly
  the kind of duplicate that drifts.

**Fixed — a failed command no longer looks like a successful one**

- Every platform caught and logged send failures, then published the state
  anyway. With the Broadlink unplugged, turning the air conditioner on left the
  UI showing `cool` while nothing had been transmitted. Failures now reach the
  caller, so the service call fails, and the entity rolls back to the state it
  last actually sent. IR remains open-loop — nothing can confirm the device
  obeyed — but a command that never left Home Assistant is no longer reported as
  if it had.
- `controller_data` pointing at an entity that does not exist was completely
  silent: `remote.send_command` matches no entity and Home Assistant only logs
  that the reference is missing. It is now validated as a `remote.*` entity_id,
  and a missing or unavailable remote raises an error naming it.
- Omitting `unique_id` silently costs the entity its registry entry, and with it
  renaming, area assignment, hiding and every other UI customisation. Setup now
  says so instead of leaving you to discover it.
- A fan switched on by its own remote reported `percentage: 0` while also
  reporting `on`. It now reports the speed as unknown, which is what it is.
- When a device file omitted the requested fan or swing mode, the substitute was
  whichever key happened to be written first — potentially the highest fan speed
  in place of the lowest. It is now the nearest entry in the file's own ordering.
- All 13 Fahrenheit climate files now declare `"temperatureUnit": "F"` instead of
  leaving the integration to infer it from the temperature range. The validator
  rejects any new file that leaves that to a guess.

**Fixed — long-standing bugs**

- Climate: a `power_sensor` turning on set `hvac_mode` to the literal `"on"`,
  which is not a valid mode. Home Assistant raises `ValueError` while writing
  that state. The mode now falls back to the last mode that was on.
- Climate: the entity reported the *user's* display unit as its own, so a
  Fahrenheit device file on a Celsius system advertised a 60–86 °C range. The
  unit now comes from the device file, and Home Assistant converts. A
  `temperature_unit` option overrides it.
- Climate: restoring a state of `unavailable`/`unknown`, or a mode the device
  file no longer offers, produced an invalid entity state.
- Climate: a device file missing the exact mode/fan/swing/temperature
  combination raised `KeyError` and silently sent nothing. Lookups now fall back
  to what the file does record — mostly this makes `dry` and `fan_only` usable on
  the 33 device files that record only some fan speeds under those modes, and
  the 1 that omits a swing mode.
- Fan: the `power_sensor` listener was registered inside the restore-state
  branch, so a fan with no previous state never followed its sensor.
- Fan: the power sensor set the speed to `None`, which broke the `percentage`
  property and the next command.
- Light: the brightness resync used the length of the *colour temperature* list.
- Light: an on/off-only device file reported `supported_color_modes: [unknown]`,
  which Home Assistant rejects.
- Light: a first sensor event with no previous state raised `AttributeError`.
- Media player: features were advertised for commands whose code was an empty
  placeholder, so the UI offered buttons that could not work.
- Media player: the power sensor was polled every 10 s; it is now event-driven.
- `delay` was validated as a string on three platforms, so it reached
  `remote.send_command` as `"0.5"`.
- 326 empty placeholder codes across 16 device files were transmitted as the
  literal payload `b64:`. They are now refused with a clear error.

**Device database**

- Removed the 28 non-Broadlink device files (Xiaomi, LOOKin, ESPHome).
- Fixed 3 device files that were not valid JSON and so could never load
  (`climate/2680`, `light/1020`, `media_player/1440`).
- Renamed the `fan` operation mode to `fan_only` in 24 climate files. Home
  Assistant has no `fan` mode, so the entity silently dropped it.
- `climate/3000`: removed a `heat` mode that has no codes.
- `climate/3380`: fixed fan-speed and temperature keys that did not match the
  file's own declarations.

## Known limitations

**A command that fails is reported; a command the device ignores is not.** IR is
open-loop. Once a code leaves the Broadlink there is no way to know whether the
air conditioner was listening, so these entities assume it was — that is what
`iot_class: assumed_state` declares. Add a `power_sensor` if you need the state to
reflect reality. What *is* now detected is a command that never got sent at all:
an unavailable remote, a wrong `controller_data`, a corrupt or missing code.

**YAML entities are not grouped into devices.** Home Assistant creates a device
only for entities belonging to a config entry. `entity_platform` guards device
registration with `if self.config_entry`, and
`device_registry.async_get_or_create` requires a `config_entry_id`, so returning
`device_info` from a YAML platform would have no effect — those entities are
handed none deliberately. `unique_id` still gives them a registry entry, which is
what renaming and area assignment actually need. Entities added from the UI do
get a device, which is the one thing the config flow buys that YAML cannot.

**Climate sends two codes.** For device files with a separate `on` code, the mode
command follows it after `delay`. If the second send fails the unit may be on
while Home Assistant reports it off. Nothing can close that gap without feedback
from the device.

**The panel learns IR, not RF.** Broadlink learns an RF code by sweeping for the
frequency first, which is a different flow with its own failure modes. For a
curtain motor or an RF ceiling fan, use `remote.learn_command` with
`command_type: rf` by hand.

## Development

Home Assistant only runs on Linux, so the tests run in a container:

```sh
scripts/run_tests.sh                 # full suite
scripts/run_tests.sh -k climate -vv  # a subset
HA_VERSION=2026.6.4 scripts/run_tests.sh
```

Validate the device database (no Home Assistant needed):

```sh
python scripts/validate_codes.py
```

Lint and format-check, using `ruff.toml` so the result does not depend on which
flags were typed:

```sh
scripts/lint.sh          # check
scripts/lint.sh --fix    # apply what ruff can fix safely
```

Beyond per-platform behaviour the suite covers:

- state restored after a restart, including values the device file can no longer
  express;
- every shipped device file, asserting that each mode/fan/swing/temperature a
  user can select resolves to a real code, and that each code decodes exactly
  the way the Broadlink integration will decode it;
- the shapes that only occur in real data — swing modes, Pronto encoding, a fan
  with `forward`/`reverse` instead of `default`, a light with no colour
  temperature, a media player with no sources;
- what happens when a command cannot be delivered: a missing or unavailable
  remote, a code that fails mid-send, a device file with the command absent. Each
  platform is checked to fail the service call and keep the state it last sent;
- the learning panel's server side: that a captured code really is recovered
  from Broadlink's storage, that a missed press is reported instead of passing
  silently, that consecutive captures do not return the first code over and over,
  and that a file the panel writes both validates and loads as a working entity;
- using a shipped device file as a starting point, checked against real files —
  a code the panel reports as missing must really be absent, or someone would be
  asked to record something they already had;
- the config flow and the options flow: that a bad device code is refused in
  the form rather than at setup, that a UI-created entity is grouped into a
  device while a YAML one is still not, that a YAML platform and a config entry
  coexist unchanged, and that changing the remote takes effect without a
  restart;
- that the panel's Create button really produces a working entity, and that
  pressing it for a device already set up reloads that entity instead of
  failing;
- that the domain, the package directory, the manifest, the panel's custom
  element and the websocket command names all still agree, which is what a
  careless rename breaks.

### Verified versions

The suite passes against Home Assistant **2026.7.4** and **2026.6.4** on
Python 3.14. The manifest's `2025.1.0` minimum reflects the oldest release whose
APIs this code only uses — it is a floor, not a tested claim. Releases older
than 2026.6 could not be installed for testing: their pinned dependencies no
longer resolve.

### Known device-file gaps

`scripts/validate_codes.py` reports **0 errors** and 67 files with warnings, all
of them inherited from upstream and all handled by the integration:

| Files | What |
| ----- | ---- |
| 34 | a fan or swing mode is missing under some operation mode, so a recorded one is substituted |
| 16 | some commands are empty placeholders; these are skipped and never transmitted |
| 11 | an operation mode Home Assistant has no equivalent for, so the entity ignores it |
| 8 | codes that decode but do not look like a valid Broadlink packet, so they may not work |
| 6 | codes that cannot be decoded at all (7 codes total) |

Nothing here can be repaired without the original hardware, so the exact sets
are pinned in `tests/test_real_device_files.py` — a *newly* broken code fails the
suite rather than blending into the noise. Run the validator for the per-file
detail.

## Credits

All device codes and the original implementation come from
[smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR) and its
community. Licensed under the [MIT License](LICENSE).
