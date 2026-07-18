"""Base entity for Faikout: device info + availability."""
from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_metadata, normalize_mac
from .coordinator import FaikoutCoordinator


class FaikoutEntity(CoordinatorEntity[FaikoutCoordinator]):
    """Common base: one HA device per module."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FaikoutCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = self._build_device_info()

    def _build_device_info(self) -> DeviceInfo:
        coordinator = self.coordinator
        meta = device_metadata(coordinator.device_meta)
        mac = meta["mac"] or normalize_mac(coordinator.mac)
        connections = (
            {(CONNECTION_NETWORK_MAC, format_mac(mac))} if mac else set()
        )
        return DeviceInfo(
            identifiers={(DOMAIN, coordinator.device_id)},
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
            and self.coordinator.transport_online
        )
