"""Sensor platform for Faikout: temperatures, humidity, power, energy, fan."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity


def _temp(key: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    )


def _energy(key: str, translation_key: str) -> SensorEntityDescription:
    return SensorEntityDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    )


# Keys map to the state/<host>/status payload. A sensor is only created when
# its key is present in the current state, so fields a given model does not
# report simply do not appear.
DESCRIPTIONS: list[SensorEntityDescription] = [
    _temp("home"),
    _temp("outside"),
    _temp("inlet"),
    _temp("liquid"),
    SensorEntityDescription(
        key="hum",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="consumption",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _energy("Whheating", "energy_heating"),
    _energy("Whcooling", "energy_cooling"),
    SensorEntityDescription(
        key="fanrpm",
        translation_key="fan_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="demand",
        translation_key="demand",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    async_add_entities(
        FaikoutSensor(coordinator, d) for d in DESCRIPTIONS if d.key in data
    )


class FaikoutSensor(FaikoutEntity, SensorEntity):
    """A single numeric reading from the module status."""

    def __init__(self, coordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.host}_{description.key}"

    @property
    def native_value(self):
        return self._data.get(self.entity_description.key)
