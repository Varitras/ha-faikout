"""Switch platform for Faikout model-dependent toggles."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SWITCH_FIELDS, build_switch_command
from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity

# The LED control only takes effect alongside another real state change on S21
# units (verified live: a LED-only command does not trigger an S21 frame, so the
# new LED value "rides along" the next actual change to temp/mode/etc.). That
# makes it unreliable as a standalone switch, so it is disabled by default.
_DISABLED_BY_DEFAULT = {"led"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        # Create a switch the first time its field appears, so a model-dependent
        # control that is absent at setup still shows up once the device reports it.
        data = coordinator.data or {}
        new = [f for f in SWITCH_FIELDS if f in data and f not in added]
        if new:
            added.update(new)
            async_add_entities(FaikoutSwitch(coordinator, f) for f in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class FaikoutSwitch(FaikoutEntity, SwitchEntity):
    """A single boolean control field (powerful/econo/streamer/swingv/swingh)."""

    def __init__(self, coordinator, field: str) -> None:
        super().__init__(coordinator)
        self._field = field
        self._attr_translation_key = field
        self._attr_unique_id = f"{coordinator.host}_{field}"
        if field in _DISABLED_BY_DEFAULT:
            self._attr_entity_registry_enabled_default = False

    @property
    def is_on(self) -> bool:
        return bool(self._data.get(self._field))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_control(**build_switch_command(self._field, True))

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_control(**build_switch_command(self._field, False))
