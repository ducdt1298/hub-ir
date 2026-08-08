# The learning panel

Recording a new device previously required leaving the browser three times: call
`remote.learn_command` from Developer Tools once per code, connect over SSH to
read `.storage/broadlink_remote_<mac>_codes`, then assemble a device file by hand
and restart to find out whether it parses.

The panel does all of it in one place.

## Enabling it

**HubIR** appears in the sidebar for administrators, because the panel writes
into the configuration directory.

It becomes available as soon as the integration loads, which happens when
anything uses it: a config entry added from **Settings → Devices & services → Add
integration → HubIR**, or a `platform: hub_ir` in `configuration.yaml`.

On a fresh install with neither, adding the integration once is the shortest
route and requires no restart. YAML also works:

```yaml
hub_ir:
```

## Requirements

A working [Broadlink](https://www.home-assistant.io/integrations/broadlink/)
remote. Only a remote from that integration can learn: the panel reads the
captured code back out of Broadlink's own storage. Remotes from other
integrations are listed but disabled.

The remote must also be **on**. Home Assistant's Broadlink integration declines
to learn while the remote entity is off, so the panel checks first and reports
it.

## Recording an air conditioner

An air conditioner is the difficult case, because most units transmit their
entire state in every packet. A unit with 3 modes, 4 fan speeds and 15
temperatures requires **180 codes**, one per combination.

**1 · Device.** Device type, Broadlink remote, and the device code. The device
code is filled in from the range reserved for locally recorded files (90000 and
above), so a recording can never shadow a shipped device file.

**2 · Identification.** Manufacturer and model. An existing device code can also
be loaded here as a starting point; see
[Reusing an existing device file](#reusing-an-existing-device-file).

**3 · Temperatures and modes.** Temperature range and step, the unit, the
operation modes, the fan speeds, and swing positions where present. See
[the lists built by hand](#the-lists-built-by-hand).

The same card carries **Mode dependencies**. Most units ignore the temperature
in *dry* and *fan only*, and many ignore the fan speed there as well. Declaring
this reduces 180 captures to roughly 120; the panel writes the single captured
code to every position it applies to.

**4 · One-touch buttons.** Turbo, Eco, Sleep, Quiet. See
[below](#one-touch-buttons-turbo-eco-sleep), because these require a decision
before any button is pressed.

**5 · Other buttons.** Anything the entity cannot express: an LED toggle, a beep,
a filter reset. These are not bound to a control; they are called by name from
[`hub_ir.send_command`](SERVICES.md).

**Capture.** The panel shows one target at a time — `cool · low · 16°C` — in the
order the buttons are laid out on the remote, with temperature innermost and
ascending. Press *Start capturing*, set the remote to the target, and press send.
The panel advances to the next target as soon as a code arrives. For most remotes
this means holding *temp +* and pressing send repeatedly without touching the
browser.

Each code times out after 30 seconds. The remaining controls:

- **Capture one** captures a single code and stops.
- **Skip** leaves a gap. The integration refuses to transmit an empty code and
  reports which one, so a gap is safe but not useful.
- **Test last code** transmits what was just captured, to confirm the unit
  responds before continuing.
- **Stop** ends the run. **Save draft** stores the session; see
  [Saving a draft](#saving-a-draft).
- **Two-packet button** is for a remote whose button alternates between two
  packets; Samsung power keys are the common case. The symptom is a captured code
  that works on every second press. Enabling this makes the panel request both
  packets and store them as a pair.

**Save.** The file is validated with the same rules as
`scripts/validate_codes.py` before anything is written, so it cannot produce an
entity the integration would reject.

The panel then offers to create the entity. It already holds the device code it
wrote and the remote the codes were captured through, so the name is the only
remaining input, and it is derived from the manufacturer and model entered
earlier. Press **Create the entity**. No `configuration.yaml`, no restart.

The result is an ordinary config entry, so it can be renamed, assigned to an
area, or pointed at a different remote from **Settings → Devices & services →
HubIR**.

For entities kept in YAML, the block to paste is under *Configure in
configuration.yaml instead*. That route requires a restart. Do not use both for
one device, or two entities will contend for the same remote.

## Saving a draft

An air conditioner is around 120 codes even after mode dependencies are declared.
Until *Save as device code* is pressed, the entire session exists only in the
browser tab, where a reload or a crash discards it.

**Save draft** stores the session on the server. It retains everything the panel
cannot recompute: the settings, every code captured so far, the positions
skipped, and the current position. The button is on the capture screen and next
to *Build the list of codes*, so the declaration work can be kept on its own.

Drafts are listed at the top of the first screen with the work outstanding and
the time they were last modified. **Resume** restores the session at the position
where it stopped.

Three points:

- Drafts are held in `.storage/hub_ir.drafts`, on the server rather than in the
  browser. A unit can be declared on a laptop, captured from a phone beside the
  unit, and finished at the desk.
- The list of codes is **not** stored. Resuming asks the server to derive it from
  the settings again, so a draft written by an older version cannot restore a
  plan the current version disagrees with. If the settings are edited before
  resuming, the codes already captured are matched against the new plan and only
  genuinely new targets remain.
- Saving the device file discards the draft. The file supersedes it, and step 2
  reopens it to add further codes.

The limit is 20 drafts. Saving over an existing draft is always permitted, so the
limit only blocks a twenty-first *new* recording.

## Adding codes to a device already configured

Reopen its device file, capture the codes that were skipped, and save to the same
device code. Press **Create the entity** again: the panel detects the existing
entity, reloads it onto the file just saved, and reports this. The new codes take
effect immediately.

Without that reload the running entity would retain the device file it parsed at
setup, and the new codes would do nothing until a restart.

## The other device types

Fans, lights and media players have flat command lists rather than a tree, so
their sequence is a single screen: declare what the device has, then capture each
button once.

| Type | What it asks for |
| ---- | ---------------- |
| Fan | speeds slowest first, whether it reverses, whether it oscillates |
| Light | brightness steps, colour temperatures in kelvin, whether it has a night light |
| Media player | which buttons exist, and the list of sources or channels |
| Switch or socket | whether the remote has separate on and off keys, or a single power key that toggles |

A switch covers anything with only on and off states: an amplifier, a projector,
a heat lamp. **No switch device files are shipped**, so the panel is the only way
to produce one. If the remote has a single power key whose code alternates, select
*One power button that toggles*. The entity then tracks which state it is in,
because sending that code when the device already matches would invert the
requested state. See [SWITCH.md](SWITCH.md).

## One-touch buttons (Turbo, Eco, Sleep)

Read this before recording them: an incorrect recording fails silently.

On most air conditioners Turbo does **not** send a discrete "turbo on" packet. It
transmits the unit's entire state — mode, fan speed, temperature — with one
additional bit set. The recorded code therefore returns the unit to whichever
state the remote was displaying at the time of recording. Record Turbo while the
remote shows 30°C, and every subsequent Turbo from Home Assistant drives the room
to 30°C.

The panel therefore requires that state to be declared **once**: a mode, a fan
speed and a temperature. It then displays the same state on every preset capture
screen and writes it into the device file as `presetBaseline`, so the entity can
report what the code actually commanded rather than leaving Home Assistant
showing a temperature the unit is not set to.

Presets are a flat list rather than another dimension of the matrix, so the whole
group costs two or three additional presses.

There is no "turbo off" code, because remotes do not provide one. Selecting
`none`, or changing the mode, fan speed, swing or temperature, re-sends the
ordinary state frame, which clears the preset. See
[CLIMATE.md](CLIMATE.md#one-touch-buttons-turbo-eco-sleep-quiet).

## Replacing a file already recorded

The device code is filled in with the first free code in the local range. If it
is pointed at a code already in use, the panel reports this and **Save** stays
disabled until *Replace the existing file* is selected. The server enforces the
same rule, so a second browser tab cannot bypass the warning.

Codes below 90000 belong to the shipped database and cannot be written at all.

## The lists built by hand

Fan speeds, swing positions, a fan's speeds, brightness steps, colour
temperatures, sources and models are each built one entry at a time. Type an
entry and press **Add** or Enter, or select one of the suggestion chips beneath.
An empty list offers a one-click starting point, such as *Use auto · low · mid ·
high*.

Each entry has three controls: **↑** and **↓** to move it, **✕** to remove it.

The arrows are functional. **Fan speeds and a fan's speeds are matched against
the captured codes by position, not by name**: the first speed in the list takes
the first code, and so on. An incorrect order produces a unit that runs on high
when Home Assistant requests low, with no warning. This is why the list is
numbered and reorderable.

Entries are rejected when they would cause problems later:

| Rejected | Reason |
| -------- | ------ |
| a duplicate, ignoring case | `High` and `high` would be two separate keys in the command tree |
| a name containing `/` | that character separates the levels of a command path and would split the key |
| a name containing `,` | commas are not how lists are entered |
| a non-number, for brightness and colour temperature | the integration compares these numerically |
| an empty value | — |

## Contributing a recording

A device file is only useful to others if it leaves the machine, so the saved
screen offers three actions, in this order:

1. **Copy JSON** and **Download `<code>.json`**, with the file's size alongside.
2. **Open a pre-filled issue**, a link to a new GitHub issue with the
   manufacturer, models, code count, Home Assistant and HubIR versions, and any
   validator warnings already filled in.
3. **Show raw JSON**, for when the clipboard is unavailable.

**The link carries no codes.** A three-mode air conditioner is about 23 kB, which
a URL cannot hold, so the file travels as an attachment or a paste. Including
part of it in the link would truncate it silently. Nothing is submitted until the
button on GitHub is pressed.

## Retaining local recordings

**Download recorded files before reinstalling.** HACS installs only
`custom_components/hub_ir/`, so the repository-root `codes/` directory is not
shipped and a locally recorded file does not exist upstream to be downloaded
again.

Each local recording on the *Identification* step has a **⭳** beside it. Return
the JSON to `custom_components/hub_ir/codes/<platform>/` afterwards and it is
picked up unchanged.

There is deliberately no upload control: an endpoint that writes arbitrary JSON
into the configuration directory is attack surface, and replacing the file and
pressing *Load device file* achieves the same result.

## Reusing an existing device file

Any existing device code can be loaded on the *Identification* step, including
the 407 shipped ones. Its settings and every code it holds are carried over, and
only the gaps remain to capture.

Saving can only write to 90000 and above, so the originals are never modified.
This is also how to handle the few upstream codes that were captured incorrectly:
re-record them into a local file.

## Where the files are written

`custom_components/hub_ir/codes/<platform>/<device_code>.json`, the same
directory the integration downloads shipped device files into, and one HACS
preserves across updates through `persistent_directory`.

## Troubleshooting

| Message | Meaning |
| ------- | ------- |
| *No infrared code was received within 30 seconds* | The Broadlink received nothing. Move the remote closer, point it directly at the device, and press once rather than holding. |
| *The remote entity … is turned off* | Turn the remote entity on; Broadlink cannot learn while it is off. |
| *… is unavailable* | The Broadlink is unreachable. Check power and network. |
| *… needs a Broadlink remote* | The selected remote belongs to another integration, whose codes this cannot read. |
| No panel in the sidebar | Nothing has loaded the integration yet — add it from Settings → Devices & services → Add integration — or the account is not an administrator. |
| *A HubIR entity already exists for this device code and remote* | The device was added previously. The existing entity was reloaded onto the file just saved; no further action is required. |
| *This version of HubIR cannot add entities from the panel* | The installed version has no config flow. Update through HACS, or use the `configuration.yaml` block under the create button. |

## Out of scope

Learning **RF** devices, such as curtains and some ceiling fans, requires a
different flow that sweeps for the frequency first, and is not part of the panel.
Use `remote.learn_command` with `command_type: rf` directly for those.
