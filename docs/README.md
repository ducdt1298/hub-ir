# Documentation

Setup instructions and the device-code tables for each platform:

* [Climate platform](CLIMATE.md)
* [Media Player platform](MEDIA_PLAYER.md)
* [Fan platform](FAN.md)
* [Light platform](LIGHT.md)
* [Switch platform](SWITCH.md) — amplifiers, projectors, heaters: on and off only
* [Learning panel](PANEL.md) — recording a device the tables do not cover
* [Services](SERVICES.md) — sending the codes no entity control can reach

Installation, migration from SmartIR and the list of changes against upstream
are in the [top-level README](../README.md).

## Creating entities

There are two ways, and they work side by side.

**From the UI** — Settings → Devices & services → **Add integration** → HubIR.
Pick the kind of device, give it a name, a device code and a Broadlink remote.
The entity appears without a restart and is grouped into a Home Assistant device
carrying the manufacturer and model from the device file. The kind of device and
the device code are fixed at creation; the remote, the delay, the helper sensors
and a media player's source names are changed afterwards under **Configure**,
which reloads the entity by itself.

**In `configuration.yaml`** — exactly as before, with every option each platform
page documents. Nothing about it has changed, and YAML entities behave
identically to how they always did, except that they are not grouped into
devices; Home Assistant only builds one for entities that belong to a config
entry.

Configure any one device through one route or the other. Doing both produces two
entities sending to the same remote.

The [learning panel](PANEL.md) offers to create the entity for you as soon as it
has written a recording, which is the shortest path of all for a device the
tables do not cover.

## Controller support

This fork supports the [Broadlink](https://www.home-assistant.io/integrations/broadlink/)
controller only. Upstream SmartIR also supported Xiaomi IR, LOOK.in, ESPHome and
MQTT; those controllers and their device files have been removed. If you need
one of them, use [upstream SmartIR](https://github.com/smartHomeHub/SmartIR)
instead.

Device files declare which controller they were recorded for. A file whose
`supportedController` is not `Broadlink` is rejected at setup with an
explanatory error.

## Adding your own device codes

If no code in the tables works for your device, record your own and place the
JSON file in `custom_components/hub_ir/codes/<platform>/`, named after the
`device_code` you configure. A local file always takes precedence over the
copy in this repository.

Before contributing a file back, check it:

```sh
python scripts/validate_codes.py
```

This verifies the file parses, declares the Broadlink controller with a
supported encoding, and — for climate files — that every mode, fan speed, swing
mode and temperature a user can select resolves to a code.

## See also

* [Discussion about SmartIR Climate (Home Assistant Community)](https://community.home-assistant.io/t/smartir-control-your-climate-tv-and-fan-devices-via-ir-rf-controllers/)
