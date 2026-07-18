"""Setup, coordinator and entity behaviour against a real Home Assistant core."""
import json

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component.common")

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import STATE_UNAVAILABLE  # noqa: E402

from custom_components.faikout.const import (  # noqa: E402
    CONF_UPDATE_INTERVAL,
    state_topic,
    status_topic,
)

from .conftest import STATUS_PAYLOAD, TEST_HOST  # noqa: E402
from .ha_helpers import make_transport, setup_integration  # noqa: E402

CLIMATE = f"climate.{TEST_HOST}"


async def test_setup_and_unload(hass):
    transport = make_transport()
    entry = await setup_integration(hass, transport)

    assert entry.state is ConfigEntryState.LOADED
    assert transport.connected
    # Both the /status and the bare state topic must be subscribed.
    assert set(transport.subs) == {status_topic(TEST_HOST), state_topic(TEST_HOST)}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert transport.stopped
    assert not transport.subs


async def test_climate_state_from_status(hass):
    await setup_integration(hass, make_transport())

    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 19.5
    assert state.attributes["temperature"] == 21.5
    assert state.attributes["fan_mode"] == "auto"
    assert state.attributes["swing_mode"] == "off"
    assert state.attributes["hvac_action"] == "heating"


async def test_climate_updates_on_new_message(hass):
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(
        status_topic(TEST_HOST), json.dumps({"mode": "C", "heat": False, "temp": 24})
    )
    await hass.async_block_till_done()

    state = hass.states.get(CLIMATE)
    assert state.state == "cool"
    assert state.attributes["temperature"] == 24
    # Fields not in the partial message must survive the merge.
    assert state.attributes["current_temperature"] == 19.5


@pytest.mark.parametrize(
    ("service", "data", "expected"),
    [
        ("set_temperature", {"temperature": 23}, {"temp": 23}),
        ("set_hvac_mode", {"hvac_mode": "cool"}, {"power": True, "mode": "C"}),
        ("set_hvac_mode", {"hvac_mode": "off"}, {"power": False}),
        ("set_fan_mode", {"fan_mode": "3"}, {"fan": 3}),
        ("set_fan_mode", {"fan_mode": "auto"}, {"fan": "A"}),
        ("set_swing_mode", {"swing_mode": "both"}, {"swingv": True, "swingh": True}),
        ("turn_off", {}, {"power": False}),
        ("turn_on", {}, {"power": True}),
    ],
)
async def test_climate_commands(hass, service, data, expected):
    transport = make_transport()
    await setup_integration(hass, transport)

    await hass.services.async_call(
        "climate", service, {"entity_id": CLIMATE, **data}, blocking=True
    )

    topic, payload = transport.published[-1]
    assert topic == f"command/{TEST_HOST}/control"
    assert payload == expected


async def test_switch_reflects_state_and_sends_command(hass):
    transport = make_transport()
    await setup_integration(hass, transport)

    entity_id = f"switch.{TEST_HOST}_powerful"
    assert hass.states.get(entity_id).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert transport.last_command == {"powerful": True}

    transport.feed(status_topic(TEST_HOST), json.dumps({"powerful": True}))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"


async def test_switch_appears_when_field_shows_up_later(hass):
    """A model-dependent control absent at setup still gets an entity later."""
    status = {k: v for k, v in STATUS_PAYLOAD.items() if k != "streamer"}
    transport = make_transport(status=status)
    await setup_integration(hass, transport)

    entity_id = f"switch.{TEST_HOST}_streamer"
    assert hass.states.get(entity_id) is None

    transport.feed(status_topic(TEST_HOST), json.dumps({"streamer": True}))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "on"


async def test_led_switch_disabled_by_default(hass):
    """LED only rides along other changes on S21, so it must not be enabled."""
    transport = make_transport(status={**STATUS_PAYLOAD, "led": True})
    await setup_integration(hass, transport)

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entry = registry.async_get(f"switch.{TEST_HOST}_led")
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_sensors_from_both_topics(hass):
    await setup_integration(hass, make_transport())

    # from /status
    assert hass.states.get(f"sensor.{TEST_HOST}_room_temperature").state == "19.5"
    assert hass.states.get(f"sensor.{TEST_HOST}_compressor").state == "42"
    assert hass.states.get(f"sensor.{TEST_HOST}_fan_speed").state == "780"
    # from the bare state topic (device_meta)
    assert hass.states.get(f"sensor.{TEST_HOST}_ip_address").state == "192.168.1.50"


async def test_energy_sensor_converts_wh_to_kwh(hass):
    transport = make_transport(status={**STATUS_PAYLOAD, "Whheating": 12345})
    await setup_integration(hass, transport)

    state = hass.states.get(f"sensor.{TEST_HOST}_energy_heating")
    assert state.state == "12.345"
    assert state.attributes["unit_of_measurement"] == "kWh"


async def test_device_info_from_meta_topic(hass):
    await setup_integration(hass, make_transport())

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={("faikout", TEST_HOST)})
    assert device is not None
    assert device.model == "Faikin S21"
    assert device.sw_version == "v1.10"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_lwt_marks_entities_unavailable(hass):
    """A bare 'false' on state/<host> is the module's LWT."""
    transport = make_transport()
    await setup_integration(hass, transport)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    transport.feed(state_topic(TEST_HOST), "false")
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    transport.feed(state_topic(TEST_HOST), "true")
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_unparseable_payload_keeps_last_state(hass):
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(status_topic(TEST_HOST), "not json at all")
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5


async def test_update_interval_throttles_pushes(hass, freezer):
    """With a throttle the newest value is still delivered, just later."""
    transport = make_transport()
    await setup_integration(hass, transport, options={CONF_UPDATE_INTERVAL: 60})

    transport.feed(status_topic(TEST_HOST), json.dumps({"temp": 25}))
    await hass.async_block_till_done()
    # Throttled: not visible yet.
    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async_fire_time_changed(hass, dt_util.utcnow() + __import__("datetime").timedelta(seconds=61))
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).attributes["temperature"] == 25
