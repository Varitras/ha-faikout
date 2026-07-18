"""Number platform for Faikout: the output demand limit."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEMAND_MAX, DEMAND_MIN, DEMAND_STEP, build_demand_command
from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added = False

    @callback
    def _add_new() -> None:
        # Created the first time the field appears, like the other platforms, so
        # a model that does not report demand simply has no entity.
        nonlocal added
        if not added and "demand" in (coordinator.data or {}):
            added = True
            async_add_entities([FaikoutDemand(coordinator)])

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class FaikoutDemand(FaikoutEntity, NumberEntity):
    """How hard the unit is allowed to work, in percent."""

    _attr_translation_key = "demand"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = DEMAND_MIN
    _attr_native_max_value = DEMAND_MAX
    _attr_native_step = DEMAND_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_demand"

    @property
    def native_value(self) -> float | None:
        raw = self._data.get("demand")
        return raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_control(**build_demand_command(value))
