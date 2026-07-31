# Broadlink IR for Home Assistant

Control **climate devices**, **media players**, **fans** and **lights** over IR/RF
with a [Broadlink](https://www.home-assistant.io/integrations/broadlink/) remote
and a database of pre-recorded device codes.

This is a maintained, Broadlink-only fork of
[smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR), whose last
release predates the Home Assistant versions people actually run today.

> Upstream asks that forks not carry the SmartIR name, so this integration uses
> the domain `broadlink_ir`. See [Migrating from SmartIR](#migrating-from-smartir).

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

Copy `custom_components/broadlink_ir` into your configuration directory:

```
<config directory>/
|-- custom_components/
|   |-- broadlink_ir/
|       |-- __init__.py
|       |-- climate.py
|       |-- controller.py
|       |-- fan.py
|       |-- light.py
|       |-- media_player.py
|       |-- manifest.json
```

Then restart Home Assistant. Adding `broadlink_ir:` to `configuration.yaml` is
optional — the platforms work without it.

## Configuration

Find your device's code in the tables below, then configure a platform:

```yaml
climate:
  - platform: broadlink_ir
    name: Office AC
    unique_id: office_ac
    device_code: 1000
    controller_data: remote.bedroom_remote
    temperature_sensor: sensor.temperature
    humidity_sensor: sensor.humidity
    power_sensor: binary_sensor.ac_power
```

Per-platform options and the full code tables:

- [Climate](docs/CLIMATE.md) — 333 device files
- [Media player](docs/MEDIA_PLAYER.md) — 53 device files
- [Fan](docs/FAN.md) — 16 device files
- [Light](docs/LIGHT.md) — 5 device files

Device files are downloaded from this repository on first use and cached under
`custom_components/broadlink_ir/codes/<platform>/`. To use your own recording,
drop the JSON file there and it will be used instead.

## Migrating from SmartIR

1. Remove the old `custom_components/smartir` directory (or uninstall it in HACS).
2. Install this integration.
3. In `configuration.yaml`, change every `platform: smartir` to
   `platform: broadlink_ir`, and drop the `smartir:` block if you had one.
4. Restart Home Assistant.

Because the platform name changes, entities are recreated. If you set
`unique_id`, rename the old entities in the entity registry — or accept the new
`_2` suffixed IDs and update your automations.

The `check_updates` and `update_branch` options are gone. If you leave them in
place, setup still succeeds and logs a warning telling you to remove them.

## What changed against upstream

**Fixed — the integration now loads and runs on current Home Assistant**

- Removed the `distutils` import that made the integration unloadable on
  Python 3.12+ (Home Assistant 2024.4 and newer).
- Removed the self-updater, which also used the deleted `hass.components` API.
  Updates are HACS' job.
- Moved every filesystem access off the event loop. Home Assistant now reports
  blocking I/O inside the loop as a warning.

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

The suite has 815 tests. Beyond per-platform behaviour, it walks every shipped
device file and asserts that every mode/fan/swing/temperature a user can select
resolves to a real code.

### Verified versions

The suite passes against Home Assistant **2026.7.4** and **2026.6.4** on
Python 3.14. The manifest's `2025.1.0` minimum reflects the oldest release whose
APIs this code only uses — it is a floor, not a tested claim. Releases older
than 2026.6 could not be installed for testing: their pinned dependencies no
longer resolve.

### Known device-file gaps

`scripts/validate_codes.py` reports 55 files with warnings and 0 with errors.
Eight files have whole branches where no code was ever recorded upstream; the
integration refuses to transmit those rather than sending an invalid payload.
They are listed in `tests/test_real_device_files.py` so a *new* gap fails the
suite.

## Credits

All device codes and the original implementation come from
[smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR) and its
community. Licensed under the [MIT License](LICENSE).
