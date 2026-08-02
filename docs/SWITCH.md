# Switch platform

For the IR devices that are only ever on or off: an amplifier, a projector, a
bathroom heat lamp, a fan heater, an IR-controlled socket. Before this platform
existed they had to be squeezed into a media player, which then advertised
volume and source controls the device does not have.

**There are no shipped switch device codes yet.** Record your own with the
**HubIR** panel in the sidebar; it writes the file for you and offers to create
the entity.

## Adding one from the UI

**Settings → Devices & services → Add integration → HubIR → Switch or socket.**
`name` and `device_code` are asked once and fixed afterwards; the remote, the
delay and the power sensor are editable later under **Configure**, and the
entity reloads itself when you save.

## Configuration variables

| Name | Type | Default | Description |
| ---- | :--: | :-----: | ----------- |
| `name` | string | optional | The name of the device |
| `unique_id` | string | optional | An ID that uniquely identifies this device |
| `device_code` | number | required | (Accepts only positive numbers) |
| `controller_data` | string | required | The `entity_id` of the Broadlink remote **(must be an already configured device)** |
| `delay` | number | optional | Seconds between the codes of a multi-code command. The default is 0.5 |
| `power_sensor` | string | optional | *entity_id* for a sensor that reports whether the device is really `on` or `off`. Accepts only on/off states |
| `power_sensor_reassert` | boolean | optional | Re-send `on` when the `power_sensor` says the device is off but Home Assistant thinks it is on. Never transmits in the other direction. Ignored on a toggle-only remote — see below |
| `reassert_interval` | number | optional | Re-send the current state every N minutes. `0`, the default, disables it |

## The device file

```json
{
  "manufacturer": "Yamaha",
  "supportedModels": ["RX-V385"],
  "supportedController": "Broadlink",
  "commandsEncoding": "Base64",
  "commands": {
    "on": "JgBQ…",
    "off": "JgBQ…"
  }
}
```

Nothing beyond the common keys is required, because remotes come in two shapes
and a file only needs to describe the one it has.

### Two buttons, two codes

Record `on` and `off`. These are **absolute**: sending `on` to something already
on changes nothing, so a repeat is harmless and is useful when the first
transmission was missed.

### One power button

Some remotes — projectors especially — have a single power key whose code simply
alternates. Record it as `toggle` and leave `on` and `off` out:

```json
"commands": { "toggle": "JgBQ…" }
```

The entity then has to **believe** a state, because sending the toggle code when
the device already matches would do the opposite of what was asked. So:

| Call | Two buttons | One toggle button |
| ---- | ----------- | ----------------- |
| `switch.turn_on` while off | sends `on` | sends `toggle` |
| `switch.turn_on` while already on | sends `on` again | **sends nothing** |
| `switch.turn_off` while on | sends `off` | sends `toggle` |
| `switch.toggle` | sends `on` or `off` | sends `toggle` |

A file with only `on` and no `off` is allowed, but `switch.turn_off` on it fails
with an error naming `off` — the code that is missing, rather than a fallback.

A file with none of `on`, `off` or `toggle` is refused outright: it could never
do anything.

### `assumed_state`

Without a `power_sensor` the state is a belief, not an observation, so the entity
reports `assumed_state: true` and Home Assistant shows two separate buttons
rather than one toggle that can drift out of step. Set a `power_sensor` and the
state becomes a reading — adopted at startup, not only when it next changes.

## When the power sensor disagrees

IR is open-loop, so a dropped frame leaves Home Assistant believing a state the
device is not in. With `power_sensor_reassert` a sensor that says **off** while
Home Assistant thinks the device is on causes `on` to be **re-sent**. A sensor
that says **on** while Home Assistant thinks it is off never transmits anything —
that is almost always somebody with the original remote.

There is a one-minute settle window after any command, and it gives up after
three attempts with one warning. `reassert_attempts` is published as an
attribute. The full reasoning is in
[CLIMATE.md](CLIMATE.md#when-the-power-sensor-disagrees); it is the same
mechanism.

**Both options are ignored on a toggle-only remote**, with a warning at startup.
Nothing in a toggle pair is absolute, so re-sending that one code when the sensor
disagrees could switch the device either way — and could oscillate for ever. That
needs `on` and `off` recorded separately.

## Other buttons

Anything else on the remote goes under `commands.extras` and is reachable by name
from [`hub_ir.send_command`](SERVICES.md):

```json
"commands": {
  "on": "JgBQ…",
  "off": "JgBQ…",
  "extras": { "input_cd": "JgBQ…", "volume_up": "JgBQ…" }
}
```

```yaml
action: hub_ir.send_command
target:
  entity_id: switch.amplifier
data:
  command: extras/input_cd
```

## Example

```yaml
hub_ir:

switch:
  - platform: hub_ir
    name: Amplifier
    unique_id: amplifier
    device_code: 90000
    controller_data: remote.living_room_remote
    power_sensor: binary_sensor.amplifier_power
```

## Not covered: curtains and blinds

A `cover` platform is not part of this release. Most curtain motors are **RF**
rather than IR, and while sending RF already works through
`remote.send_command`, *learning* RF needs a frequency sweep the panel does not
do yet. Until then, `remote.learn_command` with `command_type: rf` by hand is the
way to capture those codes.
