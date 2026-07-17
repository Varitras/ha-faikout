"""Base entity for Faikout: device info + availability."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FaikoutCoordinator


class FaikoutEntity(CoordinatorEntity[FaikoutCoordinator]):
    """Common base: one HA device per module."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FaikoutCoordinator) -> None:
        super().__init__(coordinator)
        data = coordinator.data or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.host)},
            name=coordinator.host,
            manufacturer="Faikin / RevK",
            model=data.get("model"),
        )

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.data)
            and self._data.get("online") is not False
        )
