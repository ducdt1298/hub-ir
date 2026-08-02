# The HubIR mark

<img src="logo.png" alt="HubIR" width="360">

A hub emitting infrared: one solid node, three widening arcs. No lettering in
the icon, because the same file has to read at 20 px in a sidebar and at 256 px
on the integrations page, and two characters at 20 px are a smudge.

| | |
| --- | --- |
| Wave gradient | `#FF6B35` → `#FFB627` |
| Panel | `#14243A` |

The warm end of the spectrum is where infrared actually is, and it keeps HubIR
apart from the dozens of integrations already using Home Assistant's blue.

## Files

`hub-ir-icon.svg` and `hub-ir-logo.svg` are the sources. Everything else is
rendered from them:

```sh
python scripts/build_brand.py
```

| File | Size | Used for |
| ---- | ---- | -------- |
| `icon.png` | 256×256 | the integrations page, HACS |
| `icon@2x.png` | 512×512 | the same, on high-density screens |
| `logo.png` | 720×256 | the top of the integration page, the README |
| `logo@2x.png` | 1440×512 | the same, on high-density screens |

The script uses `cairosvg`, `rsvg-convert`, `inkscape` or a headless Chrome,
whichever it finds first. Chrome is the fallback that needs nothing installed.

The panel carries a copy of the icon inline, in the `LOGO` constant at the top
of `custom_components/hub_ir/www/hub-ir-panel.js` — Home Assistant's content
security policy blocks anything that file tries to fetch, so it cannot reference
this directory. Keep the two in step.

## Getting it onto the integrations page

**Home Assistant does not read a logo out of `custom_components`.** The frontend
fetches integration icons from `brands.home-assistant.io`, so until HubIR is in
that index the integrations page shows the default puzzle piece — nothing in
this repository can change that.

To fix it, open a pull request against
[home-assistant/brands](https://github.com/home-assistant/brands) adding the
four PNGs above as:

```
custom_integrations/hub_ir/icon.png
custom_integrations/hub_ir/icon@2x.png
custom_integrations/hub_ir/logo.png
custom_integrations/hub_ir/logo@2x.png
```

The sidebar entry is separate and needs none of this: it takes a Material Design
Icons name, and `frontend.py` sets it to `mdi:remote-tv`.
