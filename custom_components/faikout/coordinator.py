"""Per-device coordinator: subscribes to state, publishes control."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import UndefinedType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    DOMAIN,
    MAX_PAYLOAD_CHARS,
    MAX_STATE_FIELDS,
    as_bool,
    control_topic,
    device_metadata,
    error_kind,
    log_identifier,
    masked_topic,
    merge_state,
    parse_device_meta,
    state_topic,
    status_topic,
)
from .transport import FaikoutTransport

_LOGGER = logging.getLogger(__name__)

# How long a loss must last before the entities show it. Short WLAN or broker
# dropouts would otherwise flip every entity to unavailable and back, which
# breaks history graphs and fires automations watching for "unavailable".
OFFLINE_GRACE = 60


def _decoded(payload) -> str | None:
    """Decode an MQTT payload, refusing anything oversized.

    The size is checked on the raw bytes first: decoding a huge payload just
    to discard it would already have cost the event loop the work.
    """
    if payload is None or len(payload) > MAX_PAYLOAD_CHARS:
        return None
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode(errors="replace")
    return payload


def _indoor_unit_online(state: dict | None) -> bool:
    """Whether a status dict says the module still reaches the indoor unit."""
    if not state:
        return False
    return as_bool(state.get("online", True))


def _module_up(meta: dict) -> bool:
    """Whether a bare-topic JSON says the module itself is still there.

    The firmware's will and its clean close are {"up": false, ...}; every
    message the module sends while running carries "up": true.
    """
    return as_bool(meta.get("up", True)) and as_bool(meta.get("online", True))


class _Presence:
    """One source the entities depend on, with short losses bridged.

    A loss shows only once it has lasted OFFLINE_GRACE; a return shows at once
    and drops a pending loss. A loss reported before the source was ever seen
    present (a retained will at startup) has nothing to bridge and shows at once.
    """

    def __init__(self, hass: HomeAssistant, on_change: Callable[[bool], None]) -> None:
        self._hass = hass
        self._on_change = on_change
        self.shown = True
        self._seen = False
        self._timer: Callable[[], None] | None = None

    @callback
    def report(self, present: bool) -> bool:
        """Take the latest reading; True when what the entities see changed now."""
        if present:
            self._seen = True
            self.cancel()
            return self._show(True)
        if not self._seen:
            return self._show(False)
        if self.shown and self._timer is None:
            self._timer = async_call_later(self._hass, OFFLINE_GRACE, self._expired)
        return False

    @callback
    def cancel(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _expired(self, _now) -> None:
        self._timer = None
        self._show(False)

    def _show(self, shown: bool) -> bool:
        if shown == self.shown:
            return False
        self.shown = shown
        self._on_change(shown)
        return True


type FaikoutConfigEntry = ConfigEntry["FaikoutCoordinator"]


class FaikoutCoordinator(DataUpdateCoordinator[dict]):
    """Holds the latest state dict for one Faikout module."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FaikoutConfigEntry,
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
        self._unsub: Callable[[], None] | None = None
        self._unsub_meta: Callable[[], None] | None = None
        self._first_data = asyncio.Event()
        # Device metadata (model/firmware/MAC) from the bare state/<host> topic.
        self.device_meta: dict = {}
        self._registered_meta: dict | None = None
        # Module presence: the bare state/<host> topic doubles as the LWT
        # ({"up": false} when the module drops off MQTT). Optimistic until told otherwise.
        self._module = _Presence(hass, self._module_presence_changed)
        # Broker link. The module LWT only tells us the device dropped off MQTT;
        # if our own connection to the broker dies we stop hearing anything at
        # all, so entities must go unavailable on that too.
        self._broker = _Presence(hass, self._broker_presence_changed)
        # The module's link to the indoor unit, from the status `online` field.
        self._indoor_unit = _Presence(hass, self._availability_changed)
        transport.set_connection_listener(self._transport_connection_changed)
        transport.set_auth_failure_listener(self._auth_failed)
        # Update throttle: 0 = real-time; N>0 = at most one push per N seconds,
        # the latest value flushed by a trailing timer. Availability bypasses it.
        self._min_interval = 0.0
        self._pending: dict | None = None
        # None = nothing pushed yet. Must not start at 0.0: loop.time() is a
        # monotonic clock since boot, so on a machine that just started the
        # elapsed check would be false and the very first state would be held
        # back for a whole interval.
        self._last_push: float | None = None
        self._flush_unsub: Callable[[], None] | None = None

    @property
    def module_available(self) -> bool:
        """The module and our broker link, as the entities should show them."""
        return self._module.shown and self._broker.shown

    @property
    def indoor_unit_available(self) -> bool:
        """The module's link to the indoor unit, as the entities should show it."""
        return bool(self.data) and self._indoor_unit.shown

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
        except Exception as err:
            if isinstance(err, HomeAssistantError) and err.translation_domain == DOMAIN:
                raise  # ours, masked already
            # Home Assistant only retries a config entry with backoff when setup
            # raises ConfigEntryNotReady. HA's own mqtt.async_subscribe raises a
            # plain HomeAssistantError while its broker connection is still
            # coming up (common on a restart), which would otherwise leave this
            # entry permanently in SETUP_ERROR until reloaded by hand.
            device, reason = log_identifier(self.host), error_kind(err)
            raise ConfigEntryNotReady(
                f"Cannot subscribe to the topics for {device}: {reason}",
                translation_domain=DOMAIN,
                translation_key="subscribe_failed",
                translation_placeholders={"device": device, "reason": reason},
            ) from None
        self._broker.report(True)

    @callback
    def _transport_connection_changed(self, connected: bool) -> None:
        self._broker.report(connected)

    @callback
    def _broker_presence_changed(self, connected: bool) -> None:
        if not connected:
            _LOGGER.warning(
                "Lost the MQTT connection carrying %s", log_identifier(self.host)
            )
        self._availability_changed(connected)

    @callback
    def _module_presence_changed(self, online: bool) -> None:
        # Once each way, and at info: a module the user switched off is not
        # an error and not something they can act on, which is what Home
        # Assistant's log-when-unavailable rule asks for.
        message = (
            "Module %s is reporting again" if online else "Module %s stopped reporting"
        )
        _LOGGER.info(message, log_identifier(self.host))
        self._availability_changed(online)

    @callback
    def _availability_changed(self, _available: bool) -> None:
        # Not through the update throttle as well: once the grace is over,
        # "this device is gone" is exactly the news a user needs.
        self._flush()

    @callback
    def _auth_failed(self) -> None:
        """The broker rejected our credentials while the entry was running."""
        _LOGGER.warning(
            "Broker rejected the credentials for %s", log_identifier(self.host)
        )
        if self.config_entry is not None:
            self.config_entry.async_start_reauth(self.hass)

    @callback
    def _meta_received(self, msg) -> None:
        payload = _decoded(msg.payload)
        if payload is None:
            return
        if payload in ("true", "false", "online", "offline"):
            module_up = payload in ("true", "online")
        else:
            parsed = parse_device_meta(payload)
            if parsed is None:
                return
            # Merge, do not replace. The device sends a short heartbeat on this
            # topic (id/ts/up/uptime/mqtt-up) far more often than the full app
            # status that carries mem/flash/wifi/ip/build. Replacing would drop
            # every diagnostic the heartbeat omits, leaving those entities
            # Unavailable until the next full status. New keys stop at the cap,
            # known keys always update - same rule the status topic uses.
            for key, value in parsed.items():
                if key in self.device_meta or len(self.device_meta) < MAX_STATE_FIELDS:
                    self.device_meta[key] = value
            module_up = _module_up(parsed)
            self._update_device_registry()
        if self._module.report(module_up):
            return  # flushed already
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
        if self.config_entry is None:
            return
        registry = dr.async_get(self.hass)
        # By identifier *and* entry: identifiers stopped being unique across
        # config entries, so the plain lookup is ambiguous and deprecated.
        device = registry.async_get_device_by_identifier(
            (DOMAIN, self.device_id), self.config_entry.entry_id
        )
        if device is None:
            return  # entities not created yet; they will pick it up themselves
        self._registered_meta = meta
        connections: set[tuple[str, str]] | UndefinedType = dr.UNDEFINED
        if meta["mac"]:
            # The full set, not an addition: merging is deprecated, so the
            # device's own connections are carried over here instead.
            connections = device.connections | {
                (dr.CONNECTION_NETWORK_MAC, dr.format_mac(meta["mac"]))
            }
        registry.async_update_device(
            device.id,
            model=meta["model"],
            sw_version=meta["sw_version"],
            new_connections=connections,
        )

    @callback
    def _message_received(self, msg) -> None:
        payload = _decoded(msg.payload)
        if payload is None:
            _LOGGER.warning("Ignoring oversized state on %s", masked_topic(msg.topic))
            return
        base = self._pending if self._pending is not None else self.data
        new_state = merge_state(base, payload)
        if new_state is None:
            # Size only, never the content: the payload is untrusted, and even
            # its first characters can carry an address or a network name.
            _LOGGER.warning(
                "Ignoring unparseable state on %s (%d characters)",
                masked_topic(msg.topic),
                len(payload),
            )
            return
        self._pending = new_state
        # Only a real JSON state (not a bare "true"/"false") satisfies
        # the first-data wait, so entity/switch discovery sees the full field set.
        if payload not in ("true", "false"):
            self._first_data.set()
        if self._indoor_unit.report(_indoor_unit_online(new_state)):
            return  # flushed already
        self._maybe_push()

    @callback
    def _maybe_push(self) -> None:
        """Flush the latest state to HA, honouring the update-interval throttle."""
        if self._min_interval <= 0:
            self._flush()
            return
        if self._last_push is None:
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
        # A flush that jumps the queue (availability change) makes any armed
        # timer redundant; leaving it would fire one extra update later.
        if self._flush_unsub is not None:
            self._flush_unsub()
            self._flush_unsub = None
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
                "Nothing received on %s within %ss. The module may be switched "
                "off, or the hostname may be wrong - it is the middle part of "
                "the MQTT topics. Entities stay unavailable until it reports",
                masked_topic(status_topic(self.host)),
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
        except Exception as err:
            if isinstance(err, HomeAssistantError) and err.translation_domain == DOMAIN:
                raise  # ours, masked already
            device, reason = log_identifier(self.host), error_kind(err)
            raise HomeAssistantError(
                f"Failed to send {fields} to {device}: {reason}",
                translation_domain=DOMAIN,
                translation_key="send_failed",
                translation_placeholders={
                    "command": str(fields),
                    "device": device,
                    "reason": reason,
                },
            ) from None

    async def async_shutdown(self) -> None:
        for presence in (self._module, self._broker, self._indoor_unit):
            presence.cancel()
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
