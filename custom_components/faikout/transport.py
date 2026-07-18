"""MQTT transport for Faikout.

Two interchangeable transports with the same tiny interface:

- ``HaMqttTransport`` uses the shared Home Assistant MQTT integration
  (``homeassistant.components.mqtt``) — the default.
- ``OwnMqttTransport`` opens its own paho MQTT connection to a broker given in
  the options, for setups where the Faikout is on a different broker than HA's
  MQTT client.

Both deliver messages as objects with ``.topic`` and ``.payload`` (the
coordinator handles str or bytes payloads), so the coordinator does not care
which transport it is using.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
    CONF_USE_OWN_MQTT,
    DEFAULT_MQTT_PORT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class FaikoutMessage:
    """A received MQTT message (mirrors the fields the coordinator reads)."""

    topic: str
    payload: str | bytes


class FaikoutTransport:
    """Transport interface."""

    async def async_connect(self) -> None:
        """Establish the connection (no-op for the HA transport)."""

    async def async_subscribe(
        self, topic: str, callback: Callable[[FaikoutMessage], None]
    ) -> Callable[[], None]:
        """Subscribe to a topic; return an unsubscribe callable."""
        raise NotImplementedError

    async def async_publish(self, topic: str, payload: str) -> None:
        raise NotImplementedError

    async def async_stop(self) -> None:
        """Tear down the connection (no-op for the HA transport)."""


class HaMqttTransport(FaikoutTransport):
    """Transport backed by Home Assistant's own MQTT integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_subscribe(self, topic, callback):
        return await mqtt.async_subscribe(self.hass, topic, callback)

    async def async_publish(self, topic, payload):
        await mqtt.async_publish(self.hass, topic, payload)


class OwnMqttTransport(FaikoutTransport):
    """Transport backed by an own paho MQTT client on a given broker."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
    ) -> None:
        # Imported lazily so the module loads even where paho is unavailable and
        # this transport is not used. paho ships with HA's MQTT integration.
        import paho.mqtt.client as paho

        self.hass = hass
        self._host = host
        self._port = port
        self._subs: dict[str, Callable[[FaikoutMessage], None]] = {}
        self._connected = False
        self._client = paho.Client(paho.CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    async def async_connect(self) -> None:
        try:
            await self.hass.async_add_executor_job(
                self._client.connect, self._host, self._port, 60
            )
        except OSError as err:
            raise ConfigEntryNotReady(
                f"Cannot connect to MQTT broker {self._host}:{self._port}: {err}"
            ) from err
        self._client.loop_start()

    # -- paho thread callbacks (NOT on the HA event loop) --------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = True
        for topic in self._subs:
            client.subscribe(topic, 0)

    def _on_disconnect(self, client, userdata, *args):
        self._connected = False

    def _on_message(self, client, userdata, msg):
        callback = self._subs.get(msg.topic)
        if callback is not None:
            # Marshal onto the HA event loop; entity callbacks must run there.
            self.hass.loop.call_soon_threadsafe(
                callback, FaikoutMessage(msg.topic, msg.payload)
            )

    # -- interface -----------------------------------------------------------
    async def async_subscribe(self, topic, callback):
        self._subs[topic] = callback
        if self._connected:
            self._client.subscribe(topic, 0)

        def _unsub() -> None:
            self._subs.pop(topic, None)
            self._client.unsubscribe(topic)

        return _unsub

    async def async_publish(self, topic, payload):
        self._client.publish(topic, payload)

    async def async_stop(self) -> None:
        self._client.loop_stop()
        await self.hass.async_add_executor_job(self._client.disconnect)


def create_transport(hass: HomeAssistant, entry) -> FaikoutTransport:
    """Pick the transport from the config entry options."""
    options = entry.options
    if options.get(CONF_USE_OWN_MQTT) and options.get(CONF_MQTT_HOST):
        return OwnMqttTransport(
            hass,
            options[CONF_MQTT_HOST],
            int(options.get(CONF_MQTT_PORT, DEFAULT_MQTT_PORT)),
            options.get(CONF_MQTT_USERNAME) or None,
            options.get(CONF_MQTT_PASSWORD) or None,
        )
    return HaMqttTransport(hass)
