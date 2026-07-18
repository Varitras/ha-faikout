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
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DISCOVERY_TOPIC,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

DISCOVERY_SECONDS = 3.0


class FaikoutConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Faikout config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> "FaikoutOptionsFlow":
        return FaikoutOptionsFlow()

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not host:
                errors["base"] = "invalid_host"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=host, data={CONF_HOST: host})

        discovered = await self._discover_hosts()
        if discovered:
            host_selector = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sorted(discovered), custom_value=True
                )
            )
        else:
            host_selector = selector.TextSelector()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): host_selector}),
            errors=errors,
        )

    async def _discover_hosts(self) -> set[str]:
        """Listen briefly on state/+ and collect module hostnames."""
        hosts: set[str] = set()

        @callback
        def _on_message(msg: mqtt.ReceiveMessage) -> None:
            parts = msg.topic.split("/")
            if len(parts) == 2 and parts[0] == "state" and parts[1]:
                hosts.add(parts[1])

        unsub = await mqtt.async_subscribe(self.hass, DISCOVERY_TOPIC, _on_message)
        try:
            await asyncio.sleep(DISCOVERY_SECONDS)
        finally:
            unsub()
        return hosts


class FaikoutOptionsFlow(OptionsFlow):
    """Options: throttle how often MQTT updates are pushed into HA."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL, default=current
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=3600,
                        step=1,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
