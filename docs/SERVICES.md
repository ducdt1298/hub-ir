# Services

A device file holds far more than an entity can offer as a control. A television
remote has a menu key, four arrows, OK, back and ten digits; an air conditioner
has an LED toggle and a beep; a climate file holds a code for every one of 180
mode/fan/temperature combinations. None of that fits the four entity models, and
until these services existed those codes sat on disk with no way to send them.

Two services, because there are two different situations.

## `hub_ir.send_command`

Sends a code from a HubIR entity's **own device file**, named by its path. It
reuses that entity's remote, controller and delay, so an automation repeats no
configuration.

```yaml
action: hub_ir.send_command
target:
  entity_id: media_player.living_room_tv
data:
  command: extras/menu
```

| Field | Required | Default | Description |
| ----- | :------: | :-----: | ----------- |
| `command` | yes | | Path to the code, separated by `/` |
| `repeat` | no | 1 | How many times to send it (1–100) |
| `delay` | no | 0.4 | Seconds between repeats |

### Command paths

A path is the same string the learning panel shows while capturing and the
validator prints when it reports a gap, so anything you have already seen in
either place is a path you can use here:

| Path | What it is |
| ---- | ---------- |
| `off` | a top-level command |
| `extras/menu` | a free-form button |
| `presets/turbo` | a one-touch button (climate) |
| `sources/HDMI1` | a media player source |
| `cool/low/16` | a climate mode / fan speed / temperature leaf |
| `forward/high` | a fan direction and speed |

An exact key wins at every level before the path is split, so a source called
`Channel 1.2` or even `A/B` resolves without renaming anything. Keys beginning
with `_` or `$` are documentation and can never be reached — transmitting a
`_comment` would send its prose as a code.

Naming a group rather than a code (`sources` on its own) fails, as does a
misspelling. The error lists the paths that device file does offer.

### It does not change the entity's state

Deliberately. Pressing `extras/menu` says nothing about whether the device is on,
which mode it is in, or which source it shows, and guessing would be worse than
staying quiet.

So for the things a platform **does** model, use that platform's own service —
`media_player.select_source` rather than `sources/HDMI1`,
`climate.set_preset_mode` rather than `presets/turbo` — and the entity stays
truthful. Reach for `hub_ir.send_command` for the codes nothing else can send.

One consequence worth knowing: if you set `source_names` on a media player, the
renamed key replaces the original, so the path becomes `sources/<new name>`.

### Repeats

`repeat` exists for the one thing a Home Assistant script genuinely does badly:
sending the same IR code several times quickly, where per-step script overhead is
visible.

```yaml
action: hub_ir.send_command
target:
  entity_id: media_player.living_room_tv
data:
  command: volumeUp
  repeat: 8
  delay: 0.2
```

## `hub_ir.send_code`

Sends a raw code through a Broadlink remote with **no entity involved** — for a
code that is not in a device file yet, something just learned or copied out of a
forum post.

```yaml
action: hub_ir.send_code
data:
  remote_entity_id: remote.bedroom_remote
  code: JgBQAAABKZMSEhI3…
```

| Field | Required | Default | Description |
| ----- | :------: | :-----: | ----------- |
| `remote_entity_id` | yes | | The Broadlink remote to transmit through |
| `code` | yes | | One code, or a list of codes to send in turn |
| `encoding` | no | `Base64` | `Base64`, `Hex` or `Pronto` |
| `delay` | no | 0.5 | Seconds between the codes of a list |

A list is how a two-packet toggle button is stored, and is sent in one
`remote.send_command` call with `delay` between the packets.

This is the same send path the learning panel's **Test** button uses, so a code
the panel proved works and a code an automation sends cannot come out
differently.

## Macros across several devices

There is deliberately **no** `commands: [...]` list field. Home Assistant's
`script:` already provides ordered steps, per-step `delay`, `repeat`,
conditions, error handling and a full execution trace — and the interesting
macros span several devices, which no single entity-targeted service call can
express whatever fields it grows.

"Turn on the TV, turn on the amplifier, switch to HDMI 2" is a script:

```yaml
script:
  movie_night:
    sequence:
      - action: media_player.turn_on
        target:
          entity_id: media_player.living_room_tv
      - action: switch.turn_on
        target:
          entity_id: switch.amplifier
      - delay:
          seconds: 3
      - action: hub_ir.send_command
        target:
          entity_id: media_player.living_room_tv
        data:
          command: extras/hdmi2
```

The `delay` matters: an amplifier that has just been switched on ignores input
for a second or two, and IR is open-loop — nothing reports back, so the wait has
to be stated rather than inferred.

## Finding out what a device file offers

The paths come from the device file. To see them:

* open the **HubIR** panel and load the device code as a template — the capture
  list is exactly the set of paths, in order; or
* read `custom_components/hub_ir/codes/<platform>/<device_code>.json`; or
* call the service with a wrong path on purpose and read the error, which lists
  what the file does have.
