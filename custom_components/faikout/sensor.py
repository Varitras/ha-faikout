"""Sensor platform for Faikout: temperatures, humidity, power, energy, fan."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity


@dataclass(frozen=True, kw_only=True)
class FaikoutSensorDescription(SensorEntityDescription):
    """Sensor description with a scale factor and a source topic.

    ``source`` selects where the value is read: ``"status"`` = state/<host>/status
    (the default), ``"meta"`` = the bare state/<host> app status (WiFi/energy/etc.).
    """

    factor: float = 1.0
    source: str = "status"


def _temp(key: str) -> FaikoutSensorDescription:
    return FaikoutSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    )


def _energy(key: str, translation_key: str) -> FaikoutSensorDescription:
    # Device reports Wh; expose as kWh (factor 1/1000).
    return FaikoutSensorDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        factor=0.001,
    )


def _diag(key: str, translation_key: str, source: str = "meta", **kw) -> FaikoutSensorDescription:
    """A diagnostic sensor (device 'Diagnostic' section)."""
    return FaikoutSensorDescription(
        key=key,
        translation_key=translation_key,
        source=source,
        entity_category=EntityCategory.DIAGNOSTIC,
        **kw,
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
    _energy("Whoutside", "energy_total"),
    _energy("Whheating", "energy_heating"),
    _energy("Whcooling", "energy_cooling"),
    SensorEntityDescription(
        key="demand",
        translation_key="demand",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # /status carries unit-STABLE speeds regardless of the hafanrpm/hacomprpm
    # device setting (verified live): fanrpm is always RPM, comp always Hz.
    SensorEntityDescription(
        key="fanrpm",
        translation_key="fan_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="comp",
        translation_key="compressor",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # From the bare state/<host> topic (device_meta), not /status.
    FaikoutSensorDescription(
        key="rssi",
        translation_key="wifi_signal",
        source="meta",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Diagnostics (bare topic; 'protocol' from /status).
    _diag("ts", "last_report", device_class=SensorDeviceClass.TIMESTAMP),
    _diag(
        "uptime",
        "uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_registry_enabled_default=False,
    ),
    _diag(
        "mqtt-up",
        "mqtt_up",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_registry_enabled_default=False,
    ),
    _diag(
        "mem",
        "free_memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diag(
        "spi",
        "free_spiram",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diag(
        "flash",
        "flash",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
    ),
    _diag("chan", "wifi_channel"),
    _diag("rst", "reset_reason"),
    _diag("ssid", "ssid"),
    _diag("bssid", "bssid"),
    _diag("ipv4", "ip_address"),
    _diag("build", "build"),
    _diag("protocol", "protocol", source="status"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        # Create a sensor the first time its field appears — so a field that is
        # momentarily absent at setup still gets a sensor once it shows up.
        new = [
            d
            for d in DESCRIPTIONS
            if d.key not in added and d.key in _source_dict(coordinator, d)
        ]
        if new:
            added.update(d.key for d in new)
            async_add_entities(FaikoutSensor(coordinator, d) for d in new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def _source_dict(coordinator, description) -> dict:
    if getattr(description, "source", "status") == "meta":
        return coordinator.device_meta or {}
    return coordinator.data or {}


class FaikoutSensor(FaikoutEntity, SensorEntity):
    """A single numeric reading from the module status."""

    def __init__(self, coordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.host}_{description.key}"

    @property
    def native_value(self):
        raw = _source_dict(self.coordinator, self.entity_description).get(
            self.entity_description.key
        )
        if raw is None:
            return None
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(raw) if isinstance(raw, str) else raw
        factor = getattr(self.entity_description, "factor", 1.0)
        if factor == 1.0:
            return raw
        return round(raw * factor, 3)
