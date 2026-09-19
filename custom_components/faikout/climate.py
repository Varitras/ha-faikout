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
    _attr_min_temp = const.TEMP_MIN
    _attr_max_temp = const.TEMP_MAX

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_climate"

    # -- capabilities ---------------------------------------------------------
    # Advertised from what this unit actually is, not from one hardware variant.
    # Offering controls the device does not have means commands it silently
    # discards, which looks like a broken integration to the user.

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if "fan" in self._data:
            features |= ClimateEntityFeature.FAN_MODE
        # any(), not the tuple itself: a two-element tuple is always truthy,
        # so testing it directly advertised swing on units without any axis.
        vertical, horizontal = self._swing_axes
        if vertical:
            features |= ClimateEntityFeature.SWING_MODE
        if horizontal:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        return features

    @property
    def _swing_axes(self) -> tuple[bool, bool]:
        """Which swing axes this unit reports, vertical and horizontal."""
        data = self._data
        return "swingv" in data, "swingh" in data

    @property
    def swing_modes(self) -> list[str] | None:
        return const.SWING_MODES if self._swing_axes[0] else None

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        return const.SWING_MODES if self._swing_axes[1] else None

    @property
    def fan_modes(self) -> list[str]:
        modes = const.fan_modes_for(self._data.get("protocol"))
        current = const.fan_dev_to_ha(self._data.get("fan"))
        if current is not None and current not in modes:
            # The step count also depends on the device's `fantype` setting,
            # which overrides the protocol default and is not published over
            # MQTT (checked live), so the protocol alone can give too narrow a
            # list. Never advertise one that excludes the unit's own current
            # value: Home Assistant would then refuse to select it again.
            modes = [*modes, current]
        return modes

    @property
    def target_temperature_step(self) -> float:
        return const.temp_step_for(self._data.get("protocol"))

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = const.hvac_mode_from_state(self._data)
        return HVACMode(mode) if mode is not None else None

    @property
    def hvac_action(self) -> HVACAction | None:
        action = const.hvac_action_from_state(self._data)
        return HVACAction(action) if action is not None else None

    @property
    def current_temperature(self) -> float | None:
        return const.as_number(self._data.get("home"))

    @property
    def target_temperature(self) -> float | None:
        return const.as_number(self._data.get("temp"))

    @property
    def fan_mode(self):
        return const.fan_dev_to_ha(self._data.get("fan"))

    @property
    def swing_mode(self) -> str | None:
        if not self._swing_axes[0]:
            return None
        return const.swing_axis_to_ha(self._data.get("swingv"))

    @property
    def swing_horizontal_mode(self) -> str | None:
        if not self._swing_axes[1]:
            return None
        return const.swing_axis_to_ha(self._data.get("swingh"))

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
        await self.coordinator.async_send_control(
            **const.build_swing_command("swingv", swing_mode)
        )

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        await self.coordinator.async_send_control(
            **const.build_swing_command("swingh", swing_horizontal_mode)
        )
