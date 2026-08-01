"""Serve the learning panel and put it in the sidebar."""

from __future__ import annotations

import logging
import os

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from . import COMPONENT_ABS_DIR, DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "broadlink-ir"
PANEL_TITLE = "Broadlink IR"
PANEL_ICON = "mdi:remote"

WEBCOMPONENT_NAME = "broadlink-ir-panel"
_MODULE_FILE = "broadlink-ir-panel.js"

# The version sits in the path rather than in a query string, so a browser
# holding the previous panel in cache fetches the new one after an update.
STATIC_URL = f"/{DOMAIN}_panel/{VERSION}"
MODULE_URL = f"{STATIC_URL}/{_MODULE_FILE}"

_REGISTERED = f"{DOMAIN}_panel_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel's module and add it to the sidebar.

    The sidebar entry needs the frontend, which a headless Home Assistant may not
    have, and registering the same panel twice raises. Neither is a reason to
    stop the platforms from working, so both are handled here rather than
    allowed to fail component setup.
    """
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL,
                os.path.join(COMPONENT_ABS_DIR, "www"),
                cache_headers=False,
            )
        ]
    )

    # Imported here so a Home Assistant without the frontend still loads this
    # module far enough to serve the static files.
    from homeassistant.components import panel_custom  # noqa: PLC0415

    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=WEBCOMPONENT_NAME,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=MODULE_URL,
            embed_iframe=False,
            # The panel writes device files into the configuration directory.
            require_admin=True,
        )
    except Exception as err:
        hass.data[_REGISTERED] = False
        _LOGGER.warning(
            "Could not add the Broadlink IR panel to the sidebar (%s). The "
            "platforms still work; only the code-learning panel is unavailable",
            err,
        )
        return

    _LOGGER.debug("Registered the Broadlink IR panel at /%s", PANEL_URL_PATH)
