"""Base entity for Faikout: device info + availability."""
from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_metadata
from .coordinator import FaikoutCoordinator


class FaikoutEntity(CoordinatorEntity[FaikoutCoordinator]):
    """Common base: one HA device per module."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FaikoutCoordinator) -> None:
        super().__init__(coordinator)
        meta = device_metadata(coordinator.device_meta)
        connections = (
            {(CONNECTION_NETWORK_MAC, format_mac(meta["mac"]))} if meta["mac"] else set()
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.host)},
            connections=connections,
            name=coordinator.host,
            manufacturer="Faikin / RevK",
            model=meta["model"],
            sw_version=meta["sw_version"],
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
            and self.coordinator.module_online
        )
