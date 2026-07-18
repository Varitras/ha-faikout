"""Config flow for Faikout: discover modules on the broker, pick a host."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_HOST,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
    CONF_UPDATE_INTERVAL,
    CONF_USE_OWN_MQTT,
    DEFAULT_MQTT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DISCOVERY_TOPIC,
    CONF_DEVICE_ID,
    CONF_MAC,
    DOMAIN,
    device_id_for,
    is_valid_host,
    normalize_mac,
)
from .transport import (
    MqttConnectionRefused,
    async_discover_on_broker,
    collect_module,
)

_LOGGER = logging.getLogger(__name__)

DISCOVERY_SECONDS = 3.0


def _broker_from_input(user_input: dict) -> dict:
    return {
        CONF_MQTT_HOST: user_input[CONF_MQTT_HOST].strip(),
        CONF_MQTT_PORT: int(user_input.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)),
        CONF_MQTT_USERNAME: user_input.get(CONF_MQTT_USERNAME, ""),
        CONF_MQTT_PASSWORD: user_input.get(CONF_MQTT_PASSWORD, ""),
    }


def _broker_schema(defaults=None) -> vol.Schema:
    """Broker connection form, shared by initial setup and re-authentication."""
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_MQTT_HOST, default=d.get(CONF_MQTT_HOST, vol.UNDEFINED)
            ): selector.TextSelector(),
            vol.Optional(
                CONF_MQTT_PORT, default=d.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_MQTT_USERNAME, default=d.get(CONF_MQTT_USERNAME, "")
            ): selector.TextSelector(),
            vol.Optional(CONF_MQTT_PASSWORD, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


def _host_selector(discovered):
    """Dropdown of discovered hosts (free text still allowed), else a text box."""
    if discovered:
        return selector.SelectSelector(
            selector.SelectSelectorConfig(options=sorted(discovered), custom_value=True)
        )
    return selector.TextSelector()


class FaikoutConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Faikout config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> "FaikoutOptionsFlow":
        return FaikoutOptionsFlow()

    def __init__(self) -> None:
        self._broker: dict = {}
        # hostname -> MAC (None when the module did not announce one)
        self._discovered: dict = {}

    def _duplicate_of_existing(self, host: str, mac) -> bool:
        """Whether this module is already set up under a different identity.

        A module discovered with its MAC and the same module added by hand (no
        MAC, so identified by hostname) produce different unique ids, so the
        unique-id check alone would happily add it twice. Two entries sharing a
        hostname are only legitimate when BOTH carry a MAC and the MACs differ —
        that is the genuine "same name on two brokers" case.
        """
        new_mac = normalize_mac(mac)
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) != host:
                continue
            existing_mac = normalize_mac(entry.data.get(CONF_MAC))
            if existing_mac is None or new_mac is None:
                return True
        return False

    @staticmethod
    def _entry_data(host: str, mac) -> dict:
        """Freeze the identity at setup time so it never shifts later."""
        return {
            CONF_HOST: host,
            CONF_MAC: mac,
            CONF_DEVICE_ID: device_id_for(mac, host),
        }

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Let the user pick how the integration should reach the module."""
        return self.async_show_menu(
            step_id="user", menu_options=["ha_mqtt", "own_mqtt"]
        )

    # -- via Home Assistant's MQTT integration -------------------------------
    async def async_step_ha_mqtt(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not is_valid_host(host):
                errors["base"] = "invalid_host"
            else:
                mac = self._discovered.get(host)
                if self._duplicate_of_existing(host, mac):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(device_id_for(mac, host))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=host, data=self._entry_data(host, mac)
                )

        self._discovered = await self._discover_hosts()
        return self.async_show_form(
            step_id="ha_mqtt",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST): _host_selector(self._discovered)}
            ),
            errors=errors,
        )

    # -- via an own broker ---------------------------------------------------
    async def async_step_own_mqtt(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Collect broker details and discover modules on that broker."""
        errors: dict[str, str] = {}
        if user_input is not None:
            broker = _broker_from_input(user_input)
            try:
                self._discovered = await async_discover_on_broker(
                    self.hass,
                    broker[CONF_MQTT_HOST],
                    broker[CONF_MQTT_PORT],
                    broker[CONF_MQTT_USERNAME] or None,
                    broker[CONF_MQTT_PASSWORD] or None,
                    DISCOVERY_SECONDS,
                )
            except MqttConnectionRefused as err:
                _LOGGER.debug("Broker refused the connection", exc_info=True)
                errors["base"] = (
                    "invalid_auth" if err.is_auth_failure else "cannot_connect"
                )
            except Exception:  # noqa: BLE001 - any other connect problem
                _LOGGER.debug("Broker discovery failed", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                self._broker = broker
                return await self.async_step_own_host()

        return self.async_show_form(
            step_id="own_mqtt", data_schema=_broker_schema(), errors=errors
        )

    # -- re-authentication ---------------------------------------------------
    async def async_step_reauth(self, entry_data) -> ConfigFlowResult:
        """Broker rejected our credentials; ask the user for new ones."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            broker = _broker_from_input(user_input)
            try:
                await async_discover_on_broker(
                    self.hass,
                    broker[CONF_MQTT_HOST],
                    broker[CONF_MQTT_PORT],
                    broker[CONF_MQTT_USERNAME] or None,
                    broker[CONF_MQTT_PASSWORD] or None,
                    DISCOVERY_SECONDS,
                )
            except MqttConnectionRefused as err:
                errors["base"] = (
                    "invalid_auth" if err.is_auth_failure else "cannot_connect"
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Reauth broker check failed", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                # Only the credentials change; host stays what the entry uses.
                return self.async_update_reload_and_abort(
                    entry, options={**entry.options, **broker}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_broker_schema(entry.options),
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )

    async def async_step_own_host(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Pick the module found on the own broker."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not is_valid_host(host):
                errors["base"] = "invalid_host"
            else:
                mac = self._discovered.get(host)
                if self._duplicate_of_existing(host, mac):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(device_id_for(mac, host))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=host,
                    data=self._entry_data(host, mac),
                    options={CONF_USE_OWN_MQTT: True, **self._broker},
                )

        return self.async_show_form(
            step_id="own_host",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST): _host_selector(self._discovered)}
            ),
            errors=errors,
        )

    async def _discover_hosts(self) -> dict:
        """Listen briefly on state/+ and collect modules with their MACs."""
        hosts: dict = {}

        @callback
        def _on_message(msg: mqtt.ReceiveMessage) -> None:
            collect_module(hosts, msg.topic, msg.payload)

        unsub = await mqtt.async_subscribe(self.hass, DISCOVERY_TOPIC, _on_message)
        try:
            await asyncio.sleep(DISCOVERY_SECONDS)
        finally:
            unsub()
        return hosts


class FaikoutOptionsFlow(OptionsFlow):
    """Options: update throttle and an optional own MQTT client."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_USE_OWN_MQTT) and not user_input.get(CONF_MQTT_HOST):
                errors[CONF_MQTT_HOST] = "host_required"
            else:
                return self.async_create_entry(data=user_input)

        o = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=o.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_USE_OWN_MQTT,
                    default=o.get(CONF_USE_OWN_MQTT, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_MQTT_HOST,
                    default=o.get(CONF_MQTT_HOST, ""),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_MQTT_PORT,
                    default=o.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_MQTT_USERNAME,
                    default=o.get(CONF_MQTT_USERNAME, ""),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_MQTT_PASSWORD,
                    default=o.get(CONF_MQTT_PASSWORD, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
