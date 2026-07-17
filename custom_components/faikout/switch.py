"""Switch platform for Faikout model-dependent toggles."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SWITCH_FIELDS, build_switch_command
from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    async_add_entities(
        FaikoutSwitch(coordinator, field)
        for field in SWITCH_FIELDS
        if field in data
    )


class FaikoutSwitch(FaikoutEntity, SwitchEntity):
    """A single boolean control field (powerful/econo/streamer/swingv/swingh)."""

    def __init__(self, coordinator, field: str) -> None:
        super().__init__(coordinator)
        self._field = field
        self._attr_translation_key = field
        self._attr_unique_id = f"{coordinator.host}_{field}"

    @property
    def is_on(self) -> bool:
        return bool(self._data.get(self._field))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_control(**build_switch_command(self._field, True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_control(**build_switch_command(self._field, False))
