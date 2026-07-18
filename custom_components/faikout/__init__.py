"""The Faikout integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, PLATFORMS
from .coordinator import FaikoutConfigEntry, FaikoutCoordinator
from .transport import create_transport


async def async_setup_entry(hass: HomeAssistant, entry: FaikoutConfigEntry) -> bool:
    """Set up Faikout from a config entry."""
    transport = create_transport(hass, entry)
    coordinator = FaikoutCoordinator(hass, entry, transport)
    coordinator.set_update_interval(
        entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )
    try:
        # Inside the try: async_start() connects and then subscribes twice, so a
        # failure part way through already leaves a live socket behind.
        await coordinator.async_start()
        await coordinator.async_wait_first_data()
        entry.runtime_data = coordinator
        entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # The own MQTT client has a live socket and a running network thread by
        # now; without this the failed setup would leak both.
        await coordinator.async_shutdown()
        raise
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: FaikoutConfigEntry) -> None:
    """Reload the entry when options (e.g. update interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FaikoutConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
