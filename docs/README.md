# Documentation

Setup instructions and the device-code tables for each platform:

* [Climate platform](CLIMATE.md)
* [Media Player platform](MEDIA_PLAYER.md)
* [Fan platform](FAN.md)
* [Light platform](LIGHT.md)

Installation, migration from SmartIR and the list of changes against upstream
are in the [top-level README](../README.md).

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
JSON file in `custom_components/broadlink_ir/codes/<platform>/`, named after the
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
