"""Configuration keys and defaults shared across HubIR.

A leaf module on purpose: it imports nothing, from this package or from Home
Assistant. ``config_flow.py`` has to know every platform's option names to build
its forms, and importing ``climate.py`` or ``media_player.py`` to find them would
drag the whole climate and media_player component stacks into the import that
runs the moment someone clicks "Add integration".

``DOMAIN`` and ``VERSION`` deliberately stay in ``__init__.py``. There is no
circular-import pressure on them, and tests read them out of that file by
regular expression.
"""

from __future__ import annotations

# Option names. These are the same strings the four platform modules define for
# their PLATFORM_SCHEMAs; they are gathered here so the config flow and the
# platforms cannot drift apart.
CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_POWER_SENSOR = "power_sensor"
CONF_POWER_SENSOR_RESTORE_STATE = "power_sensor_restore_state"
CONF_SOURCE_NAMES = "source_names"
CONF_DEVICE_CLASS = "device_class"

# Which of the four platforms a config entry represents. Lives in entry.data and
# never changes: a different platform is a different entity, not a setting.
CONF_PLATFORM = "platform"

# How the entity receives its DeviceInfo. Not a user-facing option and never
# stored in a config entry: async_setup_entry puts it into the config dict it
# hands the entity, because only the config-entry path has a device to belong
# to. A YAML entity never carries it, so its device_info stays None.
CONF_DEVICE_INFO = "device_info"

DEFAULT_DELAY = 0.5

DEFAULT_NAMES = {
    "climate": "HubIR Climate",
    "fan": "HubIR Fan",
    "light": "HubIR Light",
    "media_player": "HubIR Media Player",
}

# MediaPlayerDeviceClass, spelled out so this module stays free of the
# media_player component. Pinned by a test against the real enum.
DEFAULT_DEVICE_CLASS = "tv"
MEDIA_PLAYER_DEVICE_CLASSES = ("tv", "speaker", "receiver")

# The flow source the learning panel starts a flow with. Not SOURCE_IMPORT:
# that stays reserved for a genuine configuration.yaml migration, which would
# want to raise a repair issue telling the user to delete the YAML.
SOURCE_PANEL = "panel"


def entry_unique_id(platform: str, device_code: int, controller_data: str) -> str:
    """Return the config entry unique_id for one device on one remote.

    The remote is part of it on purpose: two identical air conditioners in two
    rooms are two entities, and they differ only by the Broadlink in front of
    them.
    """
    return f"{platform}:{device_code}:{controller_data}"
