"""Failure paths: refused connections, failed publishes, dropped broker link.

These cover the cases where the integration used to report success while
nothing had actually happened.
"""
import json
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component.common")
paho = pytest.importorskip("paho.mqtt.client")

from homeassistant.const import STATE_UNAVAILABLE  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from homeassistant.exceptions import (  # noqa: E402
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from custom_components.faikout.transport import (  # noqa: E402
    CONNECT_TIMEOUT,
    MqttConnectionRefused,
    OwnMqttTransport,
    _is_failure,
)

from .conftest import TEST_HOST  # noqa: E402
from .ha_helpers import make_transport, setup_integration  # noqa: E402

CLIMATE = f"climate.{TEST_HOST}"


class FakeReasonCode:
    """Stand-in for paho's ReasonCode."""

    def __init__(self, value, failure=True):
        self.value = value
        self.is_failure = failure

    def __str__(self):
        return f"reason {self.value}"


def _transport(hass):
    with patch("paho.mqtt.client.Client"):
        return OwnMqttTransport(hass, "broker.invalid", 1883, "u", "p")


# --- reason code handling ----------------------------------------------------
@pytest.mark.parametrize(
    ("code", "expected"),
    [(FakeReasonCode(0, False), False), (FakeReasonCode(5), True), (0, False), (5, True)],
)
def test_is_failure_handles_reason_code_and_plain_int(code, expected):
    assert _is_failure(code) is expected


# --- connect -----------------------------------------------------------------
def _answer_connack(transport, reason_code):
    """Make the mocked client deliver a CONNACK, like a real broker would."""

    def _connect(host, port, keepalive):
        transport.hass.loop.call_soon_threadsafe(
            transport._handle_connect, reason_code
        )

    transport._client.connect.side_effect = _connect


async def test_connect_succeeds_on_accepting_broker(hass):
    transport = _transport(hass)
    _answer_connack(transport, FakeReasonCode(0, failure=False))

    await transport.async_connect()

    assert transport._connected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (4, ConfigEntryAuthFailed),
        (5, ConfigEntryAuthFailed),
        (134, ConfigEntryAuthFailed),
        (3, ConfigEntryNotReady),
    ],
)
async def test_refused_connection_raises(hass, code, expected):
    """A rejected CONNACK must fail setup, not look like success."""
    transport = _transport(hass)
    _answer_connack(transport, FakeReasonCode(code))

    with pytest.raises(expected):
        await transport.async_connect()

    assert not transport._connected


async def test_connect_waits_for_connack(hass):
    """A silent broker must time out, not count as connected."""
    transport = _transport(hass)  # no CONNACK is ever delivered

    with patch("custom_components.faikout.transport.CONNECT_TIMEOUT", 0.05):
        with pytest.raises(ConfigEntryNotReady, match="CONNACK"):
            await transport.async_connect()

    assert not transport._connected
    assert CONNECT_TIMEOUT >= 1  # the shipped default is not a test value


# --- publish -----------------------------------------------------------------
async def test_failed_publish_raises(hass):
    """A publish that paho could not queue must surface to the caller."""
    transport = _transport(hass)
    transport._client.publish.return_value = type(
        "Info", (), {"rc": paho.MQTT_ERR_NO_CONN}
    )()

    with pytest.raises(HomeAssistantError, match="Could not publish"):
        await transport.async_publish("command/x/control", "{}")


async def test_successful_publish_is_silent(hass):
    transport = _transport(hass)
    transport._client.publish.return_value = type(
        "Info", (), {"rc": paho.MQTT_ERR_SUCCESS}
    )()
    await transport.async_publish("command/x/control", "{}")


async def test_service_call_reports_publish_failure(hass):
    """The whole way through: a dead link makes the service call fail."""
    transport = make_transport()

    async def _boom(topic, payload):
        raise HomeAssistantError("no connection")

    await setup_integration(hass, transport)
    transport.async_publish = _boom

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE, "temperature": 22},
            blocking=True,
        )


# --- broker link loss --------------------------------------------------------
async def test_entities_go_unavailable_when_broker_link_drops(hass):
    """Losing the broker must not leave stale values looking live."""
    transport = make_transport()
    entry = await setup_integration(hass, transport)
    coordinator = entry.runtime_data
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    # Go through the listener the transport was handed, not the coordinator's
    # own method: that wiring is the part that would silently break.
    assert transport.listener is not None, "coordinator never registered a listener"
    del coordinator

    transport.listener(False)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    transport.listener(True)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_setup_releases_transport_when_subscribing_fails(hass):
    """A failure part way through async_start() must not leak the connection."""
    transport = make_transport()
    calls = {"n": 0}
    real_subscribe = transport.async_subscribe

    async def _fail_on_second(topic, callback):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("subscribe failed")
        return await real_subscribe(topic, callback)

    transport.async_subscribe = _fail_on_second

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.faikout.const import CONF_DEVICE_ID, CONF_HOST, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_DEVICE_ID: TEST_HOST},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.faikout.create_transport", return_value=transport
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is not ConfigEntryState.LOADED
    assert transport.connected, "precondition: the connection was established"
    assert transport.stopped, "the established connection was left open"


# --- late device metadata ----------------------------------------------------
async def test_late_metadata_updates_device_entry(hass):
    """Model/firmware arriving after entity creation must reach the registry."""
    from homeassistant.helpers import device_registry as dr

    transport = make_transport(meta={})
    await setup_integration(hass, transport)

    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={("faikout", TEST_HOST)})
    assert device.model in (None, "Faikout")

    transport.feed(
        f"state/{TEST_HOST}",
        json.dumps(
            {"app": "Faikin", "version": "v2.0", "build-suffix": "-S21", "id": "001122334455"}
        ),
    )
    await hass.async_block_till_done()

    device = registry.async_get_device(identifiers={("faikout", TEST_HOST)})
    assert device.model == "Faikin S21"
    assert device.sw_version == "v2.0"
    assert (dr.CONNECTION_NETWORK_MAC, "00:11:22:33:44:55") in device.connections


# --- discovery ---------------------------------------------------------------
async def test_discovery_refusal_maps_to_invalid_auth(hass):
    """Wrong credentials must say so instead of "no devices found"."""
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.faikout.const import DOMAIN

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )

    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        side_effect=MqttConnectionRefused(FakeReasonCode(5)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"mqtt_host": "10.0.0.5", "mqtt_port": 1883,
             "mqtt_username": "u", "mqtt_password": "bad"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_subscribe_failure_asks_home_assistant_to_retry(hass):
    """HA only retries on ConfigEntryNotReady; anything else is a hard failure."""
    from homeassistant.exceptions import ConfigEntryNotReady as _NotReady

    from custom_components.faikout.coordinator import FaikoutCoordinator

    transport = make_transport()

    async def _plain_error(topic, callback):
        raise HomeAssistantError("MQTT is not enabled")

    transport.async_subscribe = _plain_error

    from custom_components.faikout.const import CONF_DEVICE_ID, CONF_HOST, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_DEVICE_ID: TEST_HOST},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    coordinator = FaikoutCoordinator(hass, entry, transport)

    with pytest.raises(_NotReady):
        await coordinator.async_start()


async def test_stop_does_not_block_the_event_loop(hass):
    """loop_stop() joins paho's thread, so it must run in an executor."""
    transport = _transport(hass)
    threads = []

    def _record():
        import threading

        threads.append(threading.current_thread().name)

    transport._client.loop_stop.side_effect = _record

    await transport.async_stop()

    assert threads, "loop_stop was never called"
    assert threads[0] != "MainThread", f"loop_stop ran on {threads[0]}"


async def test_failed_reconnect_reports_disconnected(hass):
    """A refused reconnect must report the link as down on its own."""
    transport = _transport(hass)
    seen = []
    transport.set_connection_listener(seen.append)

    transport._handle_connect(FakeReasonCode(5))

    assert seen == [False]
