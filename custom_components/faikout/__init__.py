"""The Faikout integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import CONF_HOST, PLATFORMS
from .coordinator import FaikoutConfigEntry, FaikoutCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: FaikoutConfigEntry) -> bool:
    """Set up Faikout from a config entry."""
    coordinator = FaikoutCoordinator(hass, entry.data[CONF_HOST])
    await coordinator.async_start()
    await coordinator.async_wait_first_data()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FaikoutConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.async_shutdown()
    return unloaded
