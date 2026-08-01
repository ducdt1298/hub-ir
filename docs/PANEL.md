# The learning panel

Teaching this integration a new device used to mean leaving the browser three
times: call `remote.learn_command` from Developer Tools once per code, SSH in to
read `.storage/broadlink_remote_<mac>_codes`, then hand-assemble a device file
and restart to find out whether it parses.

The panel does all of it in one place.

## Turning it on

Add this to `configuration.yaml` and restart:

```yaml
broadlink_ir:
```

**Broadlink IR** then appears in the sidebar, for administrators only — it writes
into your configuration directory.

That line used to be optional. It is what registers the panel, so a fresh
install with no platforms configured yet needs it.

## What it needs

A working [Broadlink](https://www.home-assistant.io/integrations/broadlink/)
remote. Only a remote from that integration can learn: the panel reads the
captured code back out of Broadlink's own storage, which is the step you used to
do over SSH. Remotes from other integrations are listed but greyed out.

The remote must also be **on**. Home Assistant's Broadlink integration quietly
declines to learn while the remote entity is off, so the panel checks first and
says so.

## Teaching an air conditioner

An air conditioner is the hard case, because most of them transmit their entire
state in every packet. A unit with 3 modes, 4 fan speeds and 15 temperatures
genuinely needs **180 codes** — one per combination. The panel's job is to make
that a sequence of button presses rather than a research project.

**1 · Describe the unit.** Device type, which Broadlink remote, manufacturer and
model. The device code is filled in for you from the range reserved for your own
files (90000 and up), so nothing you record can ever shadow one of the shipped
device files.

**2 · Describe what it can do.** Temperature range and step, the unit, the
operation modes, the fan speeds, and swing modes if it has them.

**3 · Say which modes ignore what.** This is the part worth spending a minute on.
Most units ignore the temperature in *dry* and *fan only*, and many ignore the
fan speed there too. Saying so turns 180 captures into around 120, and the panel
writes the one captured code everywhere it applies, so nothing is lost.

**4 · Capture.** The panel shows one target at a time — `cool · low · 16°C` —
in the order the buttons sit on your remote: temperature innermost and
ascending. Press *Start capturing*, set your remote to the target, and press
send. The moment a code arrives the panel moves to the next target on its own.
For most remotes that means holding *temp +* and pressing send, over and over,
watching a row fill in without touching the browser.

Each code times out after 30 seconds. Alongside:

- **Just this one** captures a single code and stops.
- **Skip** leaves a gap. The integration refuses to transmit an empty code and
  says which one, so a gap is safe — just not useful.
- **Test last code** transmits what was just captured, so you can confirm the
  air conditioner reacts before going any further.
- **Stop** ends the run; your progress stays, and you can save at any point and
  come back to it.

**5 · Save.** The file is validated with exactly the same rules as
`scripts/validate_codes.py` before anything is written, so it cannot produce an
entity the integration would choke on. The panel then shows the
`configuration.yaml` block to paste:

```yaml
climate:
  - platform: broadlink_ir
    name: My air conditioner
    unique_id: my_climate
    device_code: 90000
    controller_data: remote.bedroom_remote
```

Restart, and the entity appears.

## The other device types

Fans, lights and media players have flat command lists rather than a tree, so
their wizard is a single screen: tick what the device has, then capture each
button once.

| Type | What it asks for |
| ---- | ---------------- |
| Fan | speeds slowest first, whether it reverses, whether it oscillates |
| Light | brightness steps, colour temperatures in Kelvin, whether it has a night light |
| Media player | which buttons exist, and the list of sources or channels |

## Where the files go

`custom_components/broadlink_ir/codes/<platform>/<device_code>.json` — the same
directory the integration downloads shipped device files into, and one HACS
preserves across updates through `persistent_directory`.

Opening an existing device file is fine, including any of the 407 shipped ones:
use it as a starting point and save it under your own code. Saving can only ever
write to 90000 and above, so the originals stay as they are. That is also how to
deal with the handful of upstream codes that were captured badly — re-learn them
into a file of your own.

## When something goes wrong

| What you see | What it means |
| ------------ | ------------- |
| *No infrared code arrived within 30 seconds* | The Broadlink heard nothing. Hold the remote closer, point it straight at the device, and press once rather than holding. |
| *The remote entity … is turned off* | Turn the remote entity on; Broadlink cannot learn while it is off. |
| *… is unavailable* | The Broadlink is unreachable. Check power and network. |
| *… needs a Broadlink remote* | The selected remote belongs to another integration, whose codes this cannot read. |
| No panel in the sidebar | `broadlink_ir:` is missing from `configuration.yaml`, or you are not an administrator. |

## What it does not do

Learning **RF** devices — curtains, some ceiling fans — is a different flow that
sweeps for the frequency first, and is not part of the panel. Use
`remote.learn_command` with `command_type: rf` by hand for those.
