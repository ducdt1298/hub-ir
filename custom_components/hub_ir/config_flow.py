"""Config flow for HubIR: entities created and edited from the browser.

Before this existed, adding an entity meant editing ``configuration.yaml`` and
restarting Home Assistant — which for most people means SSH. The YAML platforms
still work exactly as they did; this is a second way in, and the one the
learning panel hands off to once it has written a device file.

Two rules shape the module:

* It imports no platform module. ``climate.py`` and ``media_player.py`` build
  their PLATFORM_SCHEMAs from their Home Assistant components at import time,
  and that cost has no business running when someone opens "Add integration".
  The option names it needs live in ``const.py`` instead.
* Anything that can make an entity fail to construct is checked by
  ``validation.async_validate_device`` before an entry is created, so a device
  code that cannot work is an error message under the field rather than a
  broken entity and a traceback.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_TEMPERATURE_UNIT, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import DOMAIN
from .const import (
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_CODE,
    CONF_HUMIDITY_SENSOR,
    CONF_PLATFORM,
    CONF_POWER_SENSOR,
    CONF_POWER_SENSOR_RESTORE_STATE,
    CONF_SOURCE_NAMES,
    CONF_TEMPERATURE_SENSOR,
    DEFAULT_DELAY,
    DEFAULT_DEVICE_CLASS,
    DEFAULT_NAMES,
    MEDIA_PLAYER_DEVICE_CLASSES,
    entry_unique_id,
)
from .device_file import PLATFORMS
from .validation import DeviceFileError, async_validate_device

_REMOTE = EntitySelector(EntitySelectorConfig(domain="remote"))

# The docs describe a power sensor as something that reports on or off, so the
# picker offers only domains that do. The YAML schema keeps taking any
# entity_id, because changing it would break existing configurations.
_POWER_SENSOR = EntitySelector(
    EntitySelectorConfig(domain=["binary_sensor", "switch", "input_boolean"])
)

# No device_class filter here: a template sensor without one is a perfectly good
# temperature source, and filtering would hide it with no way to say so.
_MEASUREMENT_SENSOR = EntitySelector(
    EntitySelectorConfig(domain=["sensor", "input_number", "number"])
)

_DEVICE_CODE = NumberSelector(
    NumberSelectorConfig(min=0, step=1, mode=NumberSelectorMode.BOX)
)

_DELAY = NumberSelector(
    NumberSelectorConfig(min=0, max=10, step=0.1, mode=NumberSelectorMode.BOX)
)

_TEMPERATURE_UNIT = SelectSelector(
    SelectSelectorConfig(
        options=[UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT],
        mode=SelectSelectorMode.DROPDOWN,
    )
)

_MEDIA_PLAYER_DEVICE_CLASS = SelectSelector(
    SelectSelectorConfig(
        options=list(MEDIA_PLAYER_DEVICE_CLASSES),
        translation_key="media_player_device_class",
        mode=SelectSelectorMode.DROPDOWN,
    )
)


def _options_fields(platform: str) -> dict[Any, Any]:
    """Return the schema fields a platform's editable options need.

    Shared by the create form and the options form so the two cannot offer
    different settings for the same platform.
    """
    fields: dict[Any, Any] = {
        vol.Required(CONF_CONTROLLER_DATA): _REMOTE,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): _DELAY,
    }

    if platform == "climate":
        fields[vol.Optional(CONF_TEMPERATURE_UNIT)] = _TEMPERATURE_UNIT
        fields[vol.Optional(CONF_TEMPERATURE_SENSOR)] = _MEASUREMENT_SENSOR
        fields[vol.Optional(CONF_HUMIDITY_SENSOR)] = _MEASUREMENT_SENSOR

    fields[vol.Optional(CONF_POWER_SENSOR)] = _POWER_SENSOR

    if platform == "climate":
        fields[vol.Optional(CONF_POWER_SENSOR_RESTORE_STATE, default=False)] = (
            BooleanSelector()
        )

    if platform == "media_player":
        fields[vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS)] = (
            _MEDIA_PLAYER_DEVICE_CLASS
        )

    return fields


def _create_schema(platform: str) -> vol.Schema:
    """Return the form shown when adding a device of this platform.

    ``source_names`` is deliberately absent: it is a free-form mapping that only
    makes sense once you can see the source list the device file provides, which
    is after the entity exists. It lives in the options form instead.
    """
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=DEFAULT_NAMES[platform]): TextSelector(),
            vol.Required(CONF_DEVICE_CODE): _DEVICE_CODE,
            **_options_fields(platform),
        }
    )


def _options_schema(platform: str) -> vol.Schema:
    """Return the form shown by Configure on an existing entry."""
    fields = _options_fields(platform)
    if platform == "media_player":
        fields[vol.Optional(CONF_SOURCE_NAMES)] = ObjectSelector()
    return vol.Schema(fields)


def _split(platform: str, user_input: dict[str, Any]) -> tuple[dict, dict]:
    """Split a submitted create form into entry data and entry options.

    ``data`` is the device's identity and cannot be edited afterwards; changing
    the platform or the device code means a different device file, a different
    set of features, and therefore a different entity. Everything else is an
    option, so Configure can change it and reload the entry.
    """
    return (
        {
            CONF_PLATFORM: platform,
            # NumberSelector submits a float. Left alone, device_code 1000
            # becomes 1000.0, which asks the filesystem for `1000.0.json` and
            # the repository for a URL that 404s.
            CONF_DEVICE_CODE: int(user_input[CONF_DEVICE_CODE]),
            # The field is required, but a required text field can still be
            # submitted as spaces, and that would title the entry with nothing.
            CONF_NAME: user_input[CONF_NAME].strip() or DEFAULT_NAMES[platform],
        },
        _clean_options(
            {
                key: value
                for key, value in user_input.items()
                if key not in (CONF_DEVICE_CODE, CONF_NAME)
            }
        ),
    )


def _clean_options(options: dict[str, Any]) -> dict[str, Any]:
    """Drop cleared fields and normalise the ones that are left.

    An entity picker that the user has emptied comes back as an empty string or
    is missing entirely; storing the empty string would have the entity track a
    sensor called "".
    """
    cleaned = {
        key: value for key, value in options.items() if value not in (None, "", {})
    }
    if CONF_DELAY in cleaned:
        cleaned[CONF_DELAY] = float(cleaned[CONF_DELAY])
    return cleaned


class HubIRConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one HubIR entity, without touching configuration.yaml."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which kind of device this is."""
        return self.async_show_menu(step_id="user", menu_options=list(PLATFORMS))

    async def async_step_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add an air conditioner."""
        return await self._async_step_platform("climate", user_input)

    async def async_step_fan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a fan."""
        return await self._async_step_platform("fan", user_input)

    async def async_step_light(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a light."""
        return await self._async_step_platform("light", user_input)

    async def async_step_media_player(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a TV or media player."""
        return await self._async_step_platform("media_player", user_input)

    async def async_step_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a switch: an amplifier, a projector, a heater."""
        return await self._async_step_platform("switch", user_input)

    async def _async_step_platform(
        self, platform: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Show, validate and accept the create form for one platform."""
        errors: dict[str, str] = {}
        schema = _create_schema(platform)

        if user_input is not None:
            data, options = _split(platform, user_input)

            await self.async_set_unique_id(
                entry_unique_id(
                    platform, data[CONF_DEVICE_CODE], options[CONF_CONTROLLER_DATA]
                )
            )
            self._abort_if_unique_id_configured()

            try:
                await async_validate_device(
                    self.hass,
                    platform,
                    data[CONF_DEVICE_CODE],
                    options[CONF_CONTROLLER_DATA],
                    check_remote=True,
                )
            except DeviceFileError as err:
                errors["base"] = err.error_key
            else:
                return self.async_create_entry(
                    title=data[CONF_NAME], data=data, options=options
                )

            schema = self.add_suggested_values_to_schema(schema, user_input)

        return self.async_show_form(step_id=platform, data_schema=schema, errors=errors)

    async def async_step_panel(self, panel_input: dict[str, Any]) -> ConfigFlowResult:
        """Create an entry for a device the learning panel has just written.

        Everything was settled while learning — the platform, the device code
        the panel wrote, the remote the codes came through — so there is no form
        to show. Reached only through ``hub_ir/create_entity``.
        """
        platform = panel_input[CONF_PLATFORM]
        device_code = int(panel_input[CONF_DEVICE_CODE])
        controller_data = panel_input[CONF_CONTROLLER_DATA]

        await self.async_set_unique_id(
            entry_unique_id(platform, device_code, controller_data)
        )
        self._abort_if_unique_id_configured()

        try:
            await async_validate_device(
                self.hass,
                platform,
                device_code,
                controller_data,
                check_remote=True,
            )
        except DeviceFileError as err:
            # The panel turns this into the message beside its Create button.
            return self.async_abort(reason=err.error_key)

        name = str(panel_input[CONF_NAME]).strip() or DEFAULT_NAMES[platform]
        return self.async_create_entry(
            title=name,
            data={
                CONF_PLATFORM: platform,
                CONF_DEVICE_CODE: device_code,
                CONF_NAME: name,
            },
            options={
                CONF_CONTROLLER_DATA: controller_data,
                CONF_DELAY: DEFAULT_DELAY,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the flow behind the Configure button."""
        return HubIROptionsFlow()


class HubIROptionsFlow(OptionsFlow):
    """Change an existing entity's remote, delay and helper sensors.

    Saving reloads the config entry, so the entity is rebuilt around the new
    controller straight away. That is the whole point: changing which Broadlink
    a device sits in front of used to mean editing YAML and restarting.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show, validate and accept the options form."""
        entry = self.config_entry
        platform = entry.data[CONF_PLATFORM]
        device_code = entry.data[CONF_DEVICE_CODE]
        errors: dict[str, str] = {}
        schema = _options_schema(platform)

        if user_input is not None:
            options = _clean_options(dict(user_input))
            controller_data = options[CONF_CONTROLLER_DATA]

            unique_id = entry_unique_id(platform, device_code, controller_data)
            clash = any(
                other.unique_id == unique_id and other.entry_id != entry.entry_id
                for other in self.hass.config_entries.async_entries(DOMAIN)
            )
            if clash:
                errors["base"] = "already_configured"
            else:
                try:
                    await async_validate_device(
                        self.hass,
                        platform,
                        device_code,
                        controller_data,
                        check_remote=True,
                    )
                except DeviceFileError as err:
                    errors["base"] = err.error_key
                else:
                    # The unique id follows the remote, so that adding the same
                    # device on the remote this one just left is still possible.
                    # Only when it actually moved: async_update_entry fires the
                    # update listener, and reloading twice for one Save is a
                    # waste when all that changed was the delay.
                    if entry.unique_id != unique_id:
                        self.hass.config_entries.async_update_entry(
                            entry, unique_id=unique_id
                        )
                    return self.async_create_entry(data=options)

            schema = self.add_suggested_values_to_schema(schema, user_input)
        else:
            schema = self.add_suggested_values_to_schema(schema, dict(entry.options))

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
