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

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from .const import (
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
    CONF_USE_OWN_MQTT,
    DEFAULT_MQTT_PORT,
    DISCOVERY_TOPIC,
)

_LOGGER = logging.getLogger(__name__)

# How long to wait for the broker's CONNACK before giving up. The TCP connect
# succeeding says nothing about whether the broker accepted us.
CONNECT_TIMEOUT = 10

# CONNACK codes that mean "your credentials are wrong", so the user is asked to
# re-authenticate instead of Home Assistant retrying forever. 4/5 are MQTT 3.1.1
# (bad user/password, not authorised), 134/135 the MQTT 5 equivalents.
AUTH_FAILURE_CODES = {4, 5, 134, 135}


def collect_module(found: dict, topic: str, payload) -> None:
    """Record a module seen on ``state/+``, with its MAC when the payload has one.

    The bare state topic carries the app status including ``id`` (the MAC). Both
    discovery paths funnel through here so they agree on what counts as a module.
    """
    parts = topic.split("/")
    if len(parts) != 2 or parts[0] != "state" or not parts[1]:
        return
    host = parts[1]
    found.setdefault(host, None)
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode(errors="replace")
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return
    if isinstance(data, dict) and data.get("id"):
        found[host] = data["id"]


def _is_failure(reason_code) -> bool:
    """Whether a CONNACK reason code means the broker rejected us.

    paho hands back a ReasonCode object; fall back to comparing against 0 for
    the plain-int case.
    """
    is_failure = getattr(reason_code, "is_failure", None)
    if is_failure is not None:
        return bool(is_failure)
    return reason_code != 0


class MqttConnectionRefused(Exception):
    """The broker answered the CONNACK with a failure code."""

    def __init__(self, reason_code) -> None:
        super().__init__(f"Broker refused the connection: {reason_code}")
        self.reason_code = reason_code

    @property
    def is_auth_failure(self) -> bool:
        return getattr(self.reason_code, "value", self.reason_code) in AUTH_FAILURE_CODES


@dataclass
class FaikoutMessage:
    """A received MQTT message (mirrors the fields the coordinator reads)."""

    topic: str
    payload: str | bytes


class FaikoutTransport:
    """Transport interface."""

    def set_connection_listener(self, listener: Callable[[bool], None]) -> None:
        """Register a callback invoked with the live connection state.

        Only the own-client transport can lose its connection independently of
        Home Assistant; the HA transport never calls this.
        """

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

        self._paho = paho
        self.hass = hass
        self._host = host
        self._port = port
        self._subs: dict[str, Callable[[FaikoutMessage], None]] = {}
        self._connected = False
        self._listener: Callable[[bool], None] | None = None
        self._connack: asyncio.Future | None = None
        self._client = paho.Client(paho.CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def set_connection_listener(self, listener) -> None:
        self._listener = listener

    async def async_connect(self) -> None:
        """Connect and wait for the broker to actually accept us.

        A successful TCP connect only means the socket opened — a broker that
        rejects the credentials does so in the CONNACK, so treating connect()
        as success would leave the entry loaded with a dead connection.
        """
        self._connack = self.hass.loop.create_future()
        try:
            await self.hass.async_add_executor_job(
                self._client.connect, self._host, self._port, 60
            )
        except OSError as err:
            raise ConfigEntryNotReady(
                f"Cannot connect to MQTT broker {self._host}:{self._port}: {err}"
            ) from err
        self._client.loop_start()

        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                reason_code = await self._connack
        except TimeoutError as err:
            await self.async_stop()
            raise ConfigEntryNotReady(
                f"No CONNACK from MQTT broker {self._host}:{self._port} "
                f"within {CONNECT_TIMEOUT}s"
            ) from err

        if _is_failure(reason_code):
            await self.async_stop()
            message = (
                f"MQTT broker {self._host}:{self._port} refused the connection: "
                f"{reason_code}"
            )
            if getattr(reason_code, "value", reason_code) in AUTH_FAILURE_CODES:
                raise ConfigEntryAuthFailed(message)
            raise ConfigEntryNotReady(message)

    # -- paho thread callbacks (NOT on the HA event loop) --------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self.hass.loop.call_soon_threadsafe(self._handle_connect, reason_code)

    def _on_disconnect(self, client, userdata, *args):
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect)

    @callback
    def _handle_connect(self, reason_code) -> None:
        failed = _is_failure(reason_code)
        self._connected = not failed
        if self._connack is not None and not self._connack.done():
            self._connack.set_result(reason_code)
        if failed:
            return
        # Re-subscribe: paho drops subscriptions on reconnect.
        for topic in self._subs:
            self._client.subscribe(topic, 0)
        self._notify()

    @callback
    def _handle_disconnect(self) -> None:
        self._connected = False
        self._notify()

    @callback
    def _notify(self) -> None:
        if self._listener is not None:
            self._listener(self._connected)

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
        info = self._client.publish(topic, payload)
        if info.rc != self._paho.MQTT_ERR_SUCCESS:
            # Most commonly MQTT_ERR_NO_CONN. Raising makes the service call
            # fail visibly instead of pretending the device got the command.
            raise HomeAssistantError(
                f"Could not publish to {topic}: "
                f"{self._paho.error_string(info.rc)} (rc={info.rc})"
            )

    async def async_stop(self) -> None:
        self._client.loop_stop()
        await self.hass.async_add_executor_job(self._client.disconnect)


async def async_discover_on_broker(
    hass: HomeAssistant,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    seconds: float = 3.0,
) -> dict[str, str | None]:
    """Briefly connect to a broker and collect Faikout modules from state/+.

    Returns hostname -> MAC (None when the module did not report one in the
    listening window).

    Used by the config flow when the user sets up an own broker, so devices on
    that broker can be discovered without Home Assistant's MQTT integration.
    Raises OSError when the broker cannot be reached and MqttConnectionRefused
    when it rejects the CONNACK (e.g. wrong credentials) — without that check a
    bad password would look like "connected, no devices found".
    """
    import threading
    import time

    import paho.mqtt.client as paho

    hosts: dict[str, str | None] = {}

    def _collect() -> None:
        client = paho.Client(paho.CallbackAPIVersion.VERSION2)
        if username:
            client.username_pw_set(username, password or None)

        connected = threading.Event()
        result: dict = {}

        def _on_connect(c, userdata, flags, reason_code, properties=None):
            result["reason_code"] = reason_code
            connected.set()
            if not _is_failure(reason_code):
                c.subscribe(DISCOVERY_TOPIC, 0)

        def _on_message(c, userdata, msg):
            collect_module(hosts, msg.topic, msg.payload)

        client.on_connect = _on_connect
        client.on_message = _on_message
        client.connect(host, port, 60)
        client.loop_start()
        try:
            if not connected.wait(CONNECT_TIMEOUT):
                raise OSError(f"No CONNACK from {host}:{port}")
            reason_code = result.get("reason_code")
            if _is_failure(reason_code):
                raise MqttConnectionRefused(reason_code)
            time.sleep(seconds)
        finally:
            client.loop_stop()
            client.disconnect()

    await hass.async_add_executor_job(_collect)
    return hosts


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
