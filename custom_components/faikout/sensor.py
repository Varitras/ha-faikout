"""Sensor platform for Faikout temperature readings."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TEMP_SENSORS
from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity

DESCRIPTIONS = [
    SensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    )
    for key in TEMP_SENSORS
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(FaikoutTempSensor(coordinator, d) for d in DESCRIPTIONS)


class FaikoutTempSensor(FaikoutEntity, SensorEntity):
    """A single temperature reading from the module."""

    def __init__(self, coordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.host}_{description.key}"

    @property
    def native_value(self):
        return self._data.get(self.entity_description.key)
