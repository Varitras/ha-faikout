"""Climate platform for Faikout."""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import const
from .coordinator import FaikoutConfigEntry
from .entity import FaikoutEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FaikoutConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FaikoutClimate(entry.runtime_data)])


class FaikoutClimate(FaikoutEntity, ClimateEntity):
    """A Faikout air conditioner."""

    _attr_name = None  # primary entity → uses device name
    _enable_turn_on_off_backwards_compatibility = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode(m) for m in const.HVAC_MODES]
    _attr_fan_modes = const.FAN_MODES
    _attr_swing_modes = const.SWING_MODES
    _attr_min_temp = const.TEMP_MIN
    _attr_max_temp = const.TEMP_MAX
    _attr_target_temperature_step = const.TEMP_STEP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.host}_climate"

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = const.hvac_mode_from_state(self._data)
        return HVACMode(mode) if mode is not None else None

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction(const.hvac_action_from_state(self._data))

    @property
    def current_temperature(self):
        return self._data.get("home")

    @property
    def target_temperature(self):
        return self._data.get("temp")

    @property
    def fan_mode(self):
        return const.fan_dev_to_ha(self._data.get("fan"))

    @property
    def swing_mode(self):
        return const.swing_dev_to_ha(self._data.get("swingv"), self._data.get("swingh"))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_send_control(
            **const.build_hvac_mode_command(hvac_mode)
        )

    async def async_turn_on(self) -> None:
        await self.coordinator.async_send_control(power=True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_send_control(power=False)

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.coordinator.async_send_control(
                **const.build_temperature_command(temp)
            )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_send_control(**const.build_fan_command(fan_mode))

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.coordinator.async_send_control(**const.build_swing_command(swing_mode))
