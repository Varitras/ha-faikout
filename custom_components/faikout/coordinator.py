"""Per-device coordinator: subscribes to state, publishes control."""
from __future__ import annotations

import asyncio
import json
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    DOMAIN,
    control_topic,
    device_metadata,
    merge_state,
    parse_device_meta,
    state_topic,
    status_topic,
)
from .transport import FaikoutTransport

_LOGGER = logging.getLogger(__name__)

type FaikoutConfigEntry = ConfigEntry["FaikoutCoordinator"]


class FaikoutCoordinator(DataUpdateCoordinator[dict]):
    """Holds the latest state dict for one Faikout module."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: "FaikoutConfigEntry",
        transport: FaikoutTransport,
    ) -> None:
        host = entry.data[CONF_HOST]
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"faikout_{host}")
        self.host = host
        # Identity HA keys on. Frozen in the config entry at setup time, so it
        # never changes under a running installation.
        self.device_id = entry.data.get(CONF_DEVICE_ID) or host
        self.mac = entry.data.get(CONF_MAC)
        self._transport = transport
        self._unsub = None
        self._unsub_meta = None
        self._first_data = asyncio.Event()
        # Device metadata (model/firmware/MAC) from the bare state/<host> topic.
        self.device_meta: dict = {}
        self._registered_meta: dict | None = None
        # Module presence: the bare state/<host> topic doubles as the LWT
        # ("false" when the module drops off MQTT). Optimistic until told otherwise.
        self.module_online = True
        # Broker link state. The module LWT only tells us the device dropped off
        # MQTT; if our own connection to the broker dies we stop hearing anything
        # at all, so entities must go unavailable on that too.
        self.transport_online = True
        transport.set_connection_listener(self._transport_connection_changed)
        # Update throttle: 0 = real-time; N>0 = push to HA at most every N seconds
        # (latest value always flushed via a trailing timer).
        self._min_interval = 0.0
        self._pending: dict | None = None
        self._last_push = 0.0
        self._flush_unsub = None

    @callback
    def set_update_interval(self, seconds) -> None:
        self._min_interval = max(0.0, float(seconds or 0))

    async def async_start(self) -> None:
        await self._transport.async_connect()
        try:
            self._unsub = await self._transport.async_subscribe(
                status_topic(self.host), self._message_received
            )
            # Second subscription: bare state/<host> for device metadata + LWT.
            self._unsub_meta = await self._transport.async_subscribe(
                state_topic(self.host), self._meta_received
            )
        except ConfigEntryNotReady:
            raise
        except Exception as err:
            # Home Assistant only retries a config entry with backoff when setup
            # raises ConfigEntryNotReady. HA's own mqtt.async_subscribe raises a
            # plain HomeAssistantError while its broker connection is still
            # coming up (common on a restart), which would otherwise leave this
            # entry permanently in SETUP_ERROR until reloaded by hand.
            raise ConfigEntryNotReady(
                f"Cannot subscribe to the topics for {self.host}: {err}"
            ) from err

    @callback
    def _transport_connection_changed(self, connected: bool) -> None:
        if connected == self.transport_online:
            return
        self.transport_online = connected
        if not connected:
            _LOGGER.warning("Lost the MQTT connection carrying %s", self.host)
        # Availability changed for every entity — push immediately rather than
        # letting the update throttle delay the bad news.
        self.async_update_listeners()

    @callback
    def _meta_received(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode(errors="replace")
        if payload in ("true", "false", "online", "offline"):
            self.module_online = payload in ("true", "online")
        else:
            parsed = parse_device_meta(payload)
            if parsed is None:
                return
            self.device_meta = parsed
            self.module_online = parsed.get("online", True) is not False
            self._update_device_registry()
        self._maybe_push()

    @callback
    def _update_device_registry(self) -> None:
        """Push late-arriving model/firmware/MAC onto the device entry.

        Entities capture DeviceInfo when they are created. If the bare state
        topic only arrives afterwards (or the module is upgraded), the device
        would keep showing the stale values without this.
        """
        meta = device_metadata(self.device_meta)
        if meta == self._registered_meta:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.device_id)})
        if device is None:
            return  # entities not created yet; they will pick it up themselves
        self._registered_meta = meta
        registry.async_update_device(
            device.id,
            model=meta["model"],
            sw_version=meta["sw_version"],
            merge_connections=(
                {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(meta["mac"]))}
                if meta["mac"]
                else None
            )
            or dr.UNDEFINED,
        )

    @callback
    def _message_received(self, msg) -> None:
        payload = msg.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode(errors="replace")
        base = self._pending if self._pending is not None else self.data
        new_state = merge_state(base, payload)
        if new_state is None:
            _LOGGER.warning("Ignoring unparseable state on %s: %r", msg.topic, payload)
            return
        self._pending = new_state
        # Only a real JSON state (not a bare presence "true"/"false") satisfies
        # the first-data wait, so entity/switch discovery sees the full field set.
        if payload not in ("true", "false"):
            self._first_data.set()
        self._maybe_push()

    @callback
    def _maybe_push(self) -> None:
        """Flush the latest state to HA, honouring the update-interval throttle."""
        if self._min_interval <= 0:
            self._flush()
            return
        now = self.hass.loop.time()
        elapsed = now - self._last_push
        if elapsed >= self._min_interval:
            self._flush()
        elif self._flush_unsub is None:
            self._flush_unsub = async_call_later(
                self.hass, self._min_interval - elapsed, self._scheduled_flush
            )

    @callback
    def _scheduled_flush(self, _now) -> None:
        self._flush_unsub = None
        self._flush()

    @callback
    def _flush(self) -> None:
        self._last_push = self.hass.loop.time()
        if self._pending is not None:
            self.async_set_updated_data(self._pending)
        else:
            self.async_update_listeners()

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
        """Publish a control command.

        Failures propagate: a service call that silently did nothing is worse
        than one that reports an error, because the user sees the entity snap
        back and has no idea why.
        """
        try:
            await self._transport.async_publish(
                control_topic(self.host), json.dumps(fields)
            )
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to send {fields} to {self.host}: {err}"
            ) from err

    async def async_shutdown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._unsub_meta is not None:
            self._unsub_meta()
            self._unsub_meta = None
        if self._flush_unsub is not None:
            self._flush_unsub()
            self._flush_unsub = None
        await self._transport.async_stop()
        await super().async_shutdown()
