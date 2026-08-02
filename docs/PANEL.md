# The learning panel

Teaching this integration a new device used to mean leaving the browser three
times: call `remote.learn_command` from Developer Tools once per code, SSH in to
read `.storage/broadlink_remote_<mac>_codes`, then hand-assemble a device file
and restart to find out whether it parses.

The panel does all of it in one place.

## Turning it on

**HubIR** appears in the sidebar for administrators, because the panel writes
into your configuration directory.

It comes up as soon as the integration loads, which happens the moment anything
uses it: a config entry added from **Settings → Devices & services → Add
integration → HubIR**, or a `platform: hub_ir` in `configuration.yaml`.

On a completely fresh install with neither, adding the integration once is the
shortest route — no restart. The old way still works if you prefer YAML:

```yaml
hub_ir:
```

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
operation modes, the fan speeds, and swing positions if it has them. See
[the lists you build by hand](#the-lists-you-build-by-hand) below.

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

**5 · Save, then add it.** The file is validated with exactly the same rules as
`scripts/validate_codes.py` before anything is written, so it cannot produce an
entity the integration would choke on.

The panel then offers to create the entity. It already knows the device code it
wrote and the remote you learned through, so the name is the only question left
— and it guesses that from the manufacturer and model you typed. Press **Create
the entity** and it appears. No `configuration.yaml`, no restart.

What you get is an ordinary config entry, so you can rename it, move it to an
area, or point it at a different remote from **Settings → Devices & services →
HubIR**.

If you keep your entities in YAML instead, the block to paste is still there,
folded under *Or write it into configuration.yaml yourself*. That route needs a
restart — and do not use both for one device, or you will get two entities
fighting over the same remote.

## Teaching more codes to a device you have already added

Reopen its device file, capture the codes you skipped, and save to the same
device code. Press **Create the entity** again: the panel notices the entity
already exists, reloads it onto the file you just saved, and says so. The new
codes work immediately.

Without that reload the running entity would keep the device file it parsed when
it was set up, and the codes you just learned would do nothing until a restart.

## The other device types

Fans, lights and media players have flat command lists rather than a tree, so
their wizard is a single screen: tick what the device has, then capture each
button once.

| Type | What it asks for |
| ---- | ---------------- |
| Fan | speeds slowest first, whether it reverses, whether it oscillates |
| Light | brightness steps, colour temperatures in Kelvin, whether it has a night light |
| Media player | which buttons exist, and the list of sources or channels |
| Switch or socket | whether the remote has separate on and off keys, or a single power key that toggles |

A switch is an amplifier, a projector, a bathroom heat lamp — anything only ever
on or off. **No switch device files are shipped**, so the panel is the only way to
get one. If the remote has a single power key whose code just alternates, tick
*One power button that toggles*; the entity then keeps track of which way round it
is, because sending that code when the device already matches would do the
opposite of what you asked. See [SWITCH.md](SWITCH.md).

## The lists you build by hand

Fan speeds, swing positions, a fan's speeds, brightness steps, colour
temperatures, sources, models: each is built one entry at a time. Type it and
press **Add** or Enter, or click one of the grey suggestion chips underneath. A
list that is still empty offers a one-click starting point — *Use auto · low ·
mid · high* — so nobody has to invent names for something that conventional.

Each entry has three controls: **↑** and **↓** to move it, **✕** to remove it.

The arrows are not decoration. **Fan speeds and a fan's speeds are matched
against the codes you capture by position, not by name** — the first speed in
your list gets the first code, and so on. Getting them out of order gives you a
unit that runs on high when Home Assistant asks for low, and nothing will warn
you. That is why the list is numbered and why the order can be changed.

Entries are refused, out loud, when they would cause trouble later:

| Refused | Why |
| ------- | --- |
| a duplicate, ignoring case | `High` and `high` would be two separate keys in the command tree |
| a name containing `/` | that character separates the levels of a command path, so it would split the key in two |
| a name containing `,` | commas are no longer how lists are typed |
| anything but a number, for brightness and colour temperature | the integration compares these numerically |
| nothing at all | — |

## Where the files go

`custom_components/hub_ir/codes/<platform>/<device_code>.json` — the same
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
| No panel in the sidebar | Nothing has loaded the integration yet — add it from Settings → Devices & services → Add integration — or you are not an administrator. |
| *There is already a HubIR entity for this device code and remote* | You added this device before. The existing entity was reloaded onto the file you just saved; there is nothing else to do. |
| *This version of HubIR cannot add entities from the panel* | The installed version has no config flow. Update through HACS, or use the `configuration.yaml` block folded under the create button. |

## What it does not do

Learning **RF** devices — curtains, some ceiling fans — is a different flow that
sweeps for the frequency first, and is not part of the panel. Use
`remote.learn_command` with `command_type: rf` by hand for those.
