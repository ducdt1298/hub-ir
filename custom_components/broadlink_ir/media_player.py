"""Broadlink IR media player platform."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import Helper, is_recorded
from .controller import get_controller

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Broadlink IR Media Player"
DEFAULT_DEVICE_CLASS = MediaPlayerDeviceClass.TV
DEFAULT_DELAY = 0.5

CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_POWER_SENSOR = "power_sensor"
CONF_SOURCE_NAMES = "source_names"
CONF_DEVICE_CLASS = "device_class"

PLATFORM_SCHEMA = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): cv.string,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Optional(CONF_POWER_SENSOR): cv.entity_id,
        vol.Optional(CONF_SOURCE_NAMES): dict,
        vol.Optional(CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS): vol.Coerce(
            MediaPlayerDeviceClass
        ),
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the IR Media Player platform."""
    device_code = config[CONF_DEVICE_CODE]
    device_data = await Helper.load_device_data(hass, "media_player", device_code)

    async_add_entities([BroadlinkIRMediaPlayer(hass, config, device_data)])


class BroadlinkIRMediaPlayer(MediaPlayerEntity, RestoreEntity):
    """A media player entity driven by IR/RF codes from a device file."""

    _attr_should_poll = False

    def __init__(
        self, hass: HomeAssistant, config: ConfigType, device_data: dict[str, Any]
    ) -> None:
        """Set the entity up from its YAML config and its device file."""
        self.hass = hass
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._delay = config.get(CONF_DELAY)
        self._power_sensor = config.get(CONF_POWER_SENSOR)

        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]
        self._commands = device_data["commands"]

        self._state = MediaPlayerState.OFF
        self._sources_list = []
        self._source = None
        self._support_flags = MediaPlayerEntityFeature(0)

        self._device_class = config.get(CONF_DEVICE_CLASS)

        # Supported features. A command counts only when a code was actually
        # recorded: parts of the database leave empty placeholders behind, and
        # advertising those makes the UI offer buttons that cannot work.
        if self._has_code("off"):
            self._support_flags = self._support_flags | MediaPlayerEntityFeature.TURN_OFF

        if self._has_code("on"):
            self._support_flags = self._support_flags | MediaPlayerEntityFeature.TURN_ON

        if self._has_code("previousChannel"):
            self._support_flags = (
                self._support_flags | MediaPlayerEntityFeature.PREVIOUS_TRACK
            )

        if self._has_code("nextChannel"):
            self._support_flags = (
                self._support_flags | MediaPlayerEntityFeature.NEXT_TRACK
            )

        if self._has_code("volumeDown") or self._has_code("volumeUp"):
            self._support_flags = (
                self._support_flags | MediaPlayerEntityFeature.VOLUME_STEP
            )

        if self._has_code("mute"):
            self._support_flags = (
                self._support_flags | MediaPlayerEntityFeature.VOLUME_MUTE
            )

        if self._commands.get("sources"):
            self._support_flags = (
                self._support_flags
                | MediaPlayerEntityFeature.SELECT_SOURCE
                | MediaPlayerEntityFeature.PLAY_MEDIA
            )

            for source, new_name in (config.get(CONF_SOURCE_NAMES) or {}).items():
                if source in self._commands["sources"]:
                    if new_name is not None:
                        self._commands["sources"][new_name] = self._commands["sources"][
                            source
                        ]

                    del self._commands["sources"][source]

            # Sources list, minus any whose code was never recorded.
            self._sources_list = [
                source
                for source, command in self._commands["sources"].items()
                if is_recorded(command)
            ]
            if skipped := len(self._commands["sources"]) - len(self._sources_list):
                _LOGGER.warning(
                    "Device code %s has %s source(s) with no code recorded; "
                    "they are not offered",
                    self._device_code,
                    skipped,
                )

        self._temp_lock = asyncio.Lock()

        # Init the IR/RF controller
        self._controller = get_controller(
            self.hass,
            self._supported_controller,
            self._commands_encoding,
            self._controller_data,
            self._delay,
        )

    def _has_code(self, command: str) -> bool:
        """Return whether the device file records a usable code for a command."""
        return is_recorded(self._commands.get(command))

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and start watching the power sensor."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            # A restored state can be 'unavailable'/'unknown', which is not a
            # valid MediaPlayerState.
            if last_state.state == STATE_ON:
                self._state = MediaPlayerState.ON
            elif last_state.state == STATE_OFF:
                self._state = MediaPlayerState.OFF

        if self._power_sensor:
            async_track_state_change_event(
                self.hass, self._power_sensor, self._async_power_sensor_changed
            )

            # The sensor is the source of truth, so adopt its current reading
            # rather than waiting for it to change.
            if power_state := self.hass.states.get(self._power_sensor):
                self._update_from_power_state(power_state.state)

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return the name of the media player."""
        return self._name

    @property
    def device_class(self) -> MediaPlayerDeviceClass:
        """Return the device_class of the media player."""
        return self._device_class

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the player."""
        return self._state

    @property
    def media_title(self) -> str | None:
        """Return the title of current playing media."""
        return None

    @property
    def media_content_type(self) -> str:
        """Content type of current playing media."""
        return MediaType.CHANNEL

    @property
    def source_list(self) -> list[str]:
        """Return the list of selectable sources."""
        return self._sources_list

    @property
    def source(self) -> str | None:
        """Return the current source."""
        return self._source

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return self._support_flags

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Platform specific attributes."""
        return {
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
        }

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        await self.send_command(self._commands["off"])

        if self._power_sensor is None:
            self._state = MediaPlayerState.OFF
            self._source = None
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        await self.send_command(self._commands["on"])

        if self._power_sensor is None:
            self._state = MediaPlayerState.ON
            self.async_write_ha_state()

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        await self.send_command(self._commands["previousChannel"])
        self.async_write_ha_state()

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        await self.send_command(self._commands["nextChannel"])
        self.async_write_ha_state()

    async def async_volume_down(self) -> None:
        """Turn volume down for media player."""
        await self.send_command(self._commands["volumeDown"])
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        """Turn volume up for media player."""
        await self.send_command(self._commands["volumeUp"])
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        await self.send_command(self._commands["mute"])
        self.async_write_ha_state()

    async def async_select_source(self, source: str) -> None:
        """Select channel from source."""
        if source not in self._commands["sources"]:
            _LOGGER.error(
                "Device code %s has no source '%s'", self._device_code, source
            )
            return

        self._source = source
        await self.send_command(self._commands["sources"][source])
        self.async_write_ha_state()

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Support channel change through the play_media service."""
        if media_type != MediaType.CHANNEL:
            _LOGGER.error("Invalid media type %s, expected %s", media_type, MediaType.CHANNEL)
            return
        if not media_id.isdigit():
            _LOGGER.error("media_id must be a channel number")
            return

        missing = [
            digit for digit in media_id if f"Channel {digit}" not in self._commands["sources"]
        ]
        if missing:
            _LOGGER.error(
                "Device code %s has no 'Channel %s' source, cannot tune to %s",
                self._device_code,
                missing[0],
                media_id,
            )
            return

        if self._state == MediaPlayerState.OFF:
            await self.async_turn_on()

        self._source = f"Channel {media_id}"
        for digit in media_id:
            await self.send_command(self._commands["sources"][f"Channel {digit}"])
        self.async_write_ha_state()

    async def send_command(self, command: str | list[str]) -> None:
        """Send a raw command from the device file."""
        async with self._temp_lock:
            try:
                await self._controller.send(command)
            except Exception:
                _LOGGER.exception("Error sending command to %s", self._controller_data)

    async def _async_power_sensor_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        """Track the player being switched on or off by its own remote."""
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]

        if new_state is None:
            return

        if old_state is not None and new_state.state == old_state.state:
            return

        self._update_from_power_state(new_state.state)
        self.async_write_ha_state()

    @callback
    def _update_from_power_state(self, power_state: str) -> None:
        """Adopt the power sensor's reading as the player's state."""
        if power_state == STATE_OFF:
            self._state = MediaPlayerState.OFF
            self._source = None
        elif power_state == STATE_ON:
            self._state = MediaPlayerState.ON
