"""Tests for the Broadlink IR component setup."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.broadlink_ir import DOMAIN


async def test_setup_without_options(hass: HomeAssistant) -> None:
    """The component sets up with a bare broadlink_ir: key."""
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()


async def test_obsolete_updater_options_still_validate(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An old config with the updater options loads, with a warning.

    The self-updater is gone, but existing configuration.yaml files must not
    start failing validation because of it.
    """
    assert await async_setup_component(
        hass, DOMAIN, {DOMAIN: {"check_updates": True, "update_branch": "master"}}
    )
    await hass.async_block_till_done()

    assert "no longer do anything" in caplog.text
    assert "check_updates" in caplog.text
    assert "update_branch" in caplog.text


async def test_no_updater_services_are_registered(hass: HomeAssistant) -> None:
    """The removed self-updater no longer registers its services."""
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, "check_updates")
    assert not hass.services.has_service(DOMAIN, "update_component")
