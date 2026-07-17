"""Per-device coordinator: subscribes to state, publishes control."""
from __future__ import annotations

import asyncio
import json
import logging

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_HOST, control_topic, merge_state, state_topic, status_topic

_LOGGER = logging.getLogger(__name__)

type FaikoutConfigEntry = ConfigEntry["FaikoutCoordinator"]


class FaikoutCoordinator(DataUpdateCoordinator[dict]):
    """Holds the latest state dict for one Faikout module."""

    def __init__(self, hass: HomeAssistant, entry: "FaikoutConfigEntry") -> None:
        host = entry.data[CONF_HOST]
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"faikout_{host}")
        self.host = host
        self._unsub = None
        self._unsub_meta = None
        self._first_data = asyncio.Event()
        # Device metadata (model/firmware/MAC) from the bare state/<host> topic.
        self.device_meta: dict = {}
        # Module presence: the bare state/<host> topic doubles as the LWT
        # ("false" when the module drops off MQTT). Optimistic until told otherwise.
        self.module_online = True

    async def async_start(self) -> None:
        self._unsub = await mqtt.async_subscribe(
            self.hass, status_topic(self.host), self._message_received
        )
        # Second subscription: bare state/<host> for device metadata + LWT presence.
        self._unsub_meta = await mqtt.async_subscribe(
            self.hass, state_topic(self.host), self._meta_received
        )

    @callback
    def _meta_received(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode(errors="replace")
        if payload in ("true", "false", "online", "offline"):
            self.module_online = payload in ("true", "online")
        else:
            try:
                parsed = json.loads(payload)
            except (ValueError, TypeError):
                return
            if not isinstance(parsed, dict):
                return
            self.device_meta = parsed
            self.module_online = parsed.get("online", True) is not False
        self.async_update_listeners()

    @callback
    def _message_received(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode(errors="replace")
        new_state = merge_state(self.data, payload)
        if new_state is None:
            _LOGGER.warning("Ignoring unparseable state on %s: %r", msg.topic, payload)
            return
        self.async_set_updated_data(new_state)
        # Only a real JSON state (not a bare presence "true"/"false") satisfies
        # the first-data wait, so entity/switch discovery sees the full field set.
        if payload not in ("true", "false"):
            self._first_data.set()

    async def async_wait_first_data(self, timeout: float = 10) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self._first_data.wait()
        except TimeoutError:
            _LOGGER.warning(
                "No initial state from %s within %ss; entities may be incomplete",
                self.host,
                timeout,
            )

    async def async_send_control(self, **fields) -> None:
        try:
            await mqtt.async_publish(
                self.hass, control_topic(self.host), json.dumps(fields)
            )
        except Exception:  # noqa: BLE001 - never let a command crash the entity
            _LOGGER.exception("Failed to publish control to %s: %s", self.host, fields)

    async def async_shutdown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._unsub_meta is not None:
            self._unsub_meta()
            self._unsub_meta = None
        await super().async_shutdown()
