"""Setup, coordinator and entity behaviour against a real Home Assistant core."""
import json
from datetime import timedelta

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component.common")

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import STATE_UNAVAILABLE  # noqa: E402
from homeassistant.exceptions import ServiceValidationError  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    async_fire_time_changed,
)

from custom_components.faikout.const import (  # noqa: E402
    CONF_UPDATE_INTERVAL,
    state_topic,
    status_topic,
)

from .conftest import META_PAYLOAD, STATUS_PAYLOAD, TEST_HOST, FakeTransport  # noqa: E402
from .ha_helpers import make_transport, outlast_grace, setup_integration  # noqa: E402

CLIMATE = f"climate.{TEST_HOST}"


async def test_setup_and_unload(hass):
    transport = make_transport()
    entry = await setup_integration(hass, transport)

    assert entry.state is ConfigEntryState.LOADED
    assert transport.connected
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
    assert state.attributes["swing_horizontal_mode"] == "off"
    assert state.attributes["hvac_action"] == "heating"


async def test_climate_reports_no_action_when_the_direction_is_unknowable(hass):
    """heat_cool with the compressor running: the entity must not name a state.

    Home Assistant leaves the attribute out entirely for an absent action and
    shows it as unknown, which is what an undecidable direction is. Claiming
    idle would contradict the compressor reading right next to it.
    """
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(
        status_topic(TEST_HOST),
        json.dumps({"power": True, "mode": "A", "heat": False, "comp": 35}),
    )
    await hass.async_block_till_done()

    state = hass.states.get(CLIMATE)
    assert state.state == "heat_cool"
    assert "hvac_action" not in state.attributes


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
        ("set_fan_mode", {"fan_mode": "3"}, {"fan": "3"}),
        ("set_fan_mode", {"fan_mode": "auto"}, {"fan": "A"}),
        ("set_swing_mode", {"swing_mode": "on"}, {"swingv": True}),
        ("set_swing_horizontal_mode", {"swing_horizontal_mode": "on"}, {"swingh": True}),
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


async def test_a_quantity_that_cannot_be_negative_rejects_a_negative_value(hass):
    """The action logic already treats a negative frequency as no reading;
    the sensor must not hand -1 Hz to automations while the climate entity
    ignores it. Temperatures stay allowed below zero."""
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(status_topic(TEST_HOST), json.dumps({"comp": -1, "outside": -5}))
    await hass.async_block_till_done()

    assert hass.states.get(f"sensor.{TEST_HOST}_compressor").state == "unknown"
    assert hass.states.get(f"sensor.{TEST_HOST}_outside_temperature").state == "-5"


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
    entry = await setup_integration(hass, make_transport())

    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        ("faikout", TEST_HOST), entry.entry_id
    )
    assert device is not None
    assert device.model == "Faikin S21"
    assert device.sw_version == "v1.10"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff") in device.connections


async def test_bare_false_on_the_state_topic_marks_entities_unavailable(hass):
    """A bare 'false' on state/<host> also counts as the module gone."""
    transport = make_transport()
    await setup_integration(hass, transport)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    transport.feed(state_topic(TEST_HOST), "false")
    await hass.async_block_till_done()
    await outlast_grace(hass)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    transport.feed(state_topic(TEST_HOST), "true")
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


@pytest.mark.parametrize(
    "goodbye",
    [{"up": False, "reason": "LWT"}, {"up": False}],
    ids=["last will", "clean close"],
)
async def test_module_losing_power_marks_entities_unavailable(hass, goodbye):
    """The firmware says goodbye with "up": false, never with a bare "false".

    Its will (broker-sent when the module loses power) and its clean close
    both look like this; reading only "false" left the last values standing
    as live after the breaker was switched off.
    """
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(state_topic(TEST_HOST), json.dumps(goodbye))
    await hass.async_block_till_done()
    await outlast_grace(hass)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    transport.feed(state_topic(TEST_HOST), json.dumps(HEARTBEAT))
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


HEARTBEAT = {"id": "AABBCCDDEEFF", "up": True, "uptime": 5}

# Each way the entities can lose their source: how it is lost, how it returns.
LOSSES = {
    "module gone": (
        lambda t: t.feed(state_topic(TEST_HOST), json.dumps({"up": False, "reason": "LWT"})),
        lambda t: t.feed(state_topic(TEST_HOST), json.dumps(HEARTBEAT)),
    ),
    "indoor unit lost": (
        lambda t: t.feed(status_topic(TEST_HOST), json.dumps({"online": False})),
        lambda t: t.feed(status_topic(TEST_HOST), json.dumps({"online": True})),
    ),
    "broker link lost": (
        lambda t: t.listener(False),
        lambda t: t.listener(True),
    ),
}


@pytest.mark.parametrize("loss", LOSSES.values(), ids=LOSSES.keys())
async def test_a_short_dropout_never_reaches_the_entities(hass, loss):
    """A WLAN or broker hiccup must not flip every entity to unavailable.

    Each flip breaks the history graphs and fires automations watching for
    "unavailable", for a module that was back seconds later.
    """
    lose, regain = loss
    transport = make_transport()
    await setup_integration(hass, transport)

    lose(transport)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    regain(transport)
    await hass.async_block_till_done()
    await outlast_grace(hass)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


@pytest.mark.parametrize("loss", LOSSES.values(), ids=LOSSES.keys())
async def test_a_lasting_loss_shows_once_it_outlasts_the_grace(hass, loss):
    lose, regain = loss
    transport = make_transport()
    await setup_integration(hass, transport)

    lose(transport)
    await hass.async_block_till_done()
    await outlast_grace(hass)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    # Coming back has nothing to bridge: it shows at once.
    regain(transport)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("status", "meta"),
    [
        (None, {"up": False, "reason": "LWT"}),
        ({**STATUS_PAYLOAD, "online": False}, None),
    ],
    ids=["retained will", "retained lost indoor unit"],
)
async def test_a_loss_known_at_startup_shows_at_once(hass, status, meta):
    """Home Assistant restarting while the unit is off: nothing to bridge.

    The broker hands over the retained goodbye first thing; holding it back
    would show values from before the power cut as live.
    """
    await setup_integration(hass, make_transport(status=status, meta=meta))

    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_a_short_dropout_is_not_logged(hass, caplog):
    import logging

    transport = make_transport()
    await setup_integration(hass, transport)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="custom_components.faikout.coordinator"):
        for lose, regain in LOSSES.values():
            lose(transport)
            await hass.async_block_till_done()
            regain(transport)
            await hass.async_block_till_done()
        await outlast_grace(hass)

    assert not caplog.records


async def _after(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


@pytest.mark.parametrize("loss", LOSSES.values(), ids=LOSSES.keys())
async def test_the_grace_is_a_minute(hass, loss):
    """Literal seconds, not the constant: the README promises a minute."""
    lose, _regain = loss
    transport = make_transport()
    await setup_integration(hass, transport)

    lose(transport)
    await hass.async_block_till_done()
    await _after(hass, 55)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    await _after(hass, 61)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_a_loss_reported_again_and_again_still_shows_after_a_minute(hass, freezer):
    """A module that keeps saying "indoor unit lost" must not push the deadline back.

    Restarting the wait on every report would keep stale values live forever,
    because the module repeats itself for as long as the link is down.
    """
    transport = make_transport()
    await setup_integration(hass, transport)

    for _ in range(7):
        transport.feed(status_topic(TEST_HOST), json.dumps({"online": False}))
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_the_indoor_unit_link_on_the_state_topic_is_not_the_module(hass, caplog):
    """With the firmware's Home Assistant support on, every module message
    carries "online": its link to the indoor unit, not the module itself.

    Read as the module, it took the network diagnostics down with it and
    logged "stopped reporting" for a module that was still reporting.
    """
    import logging

    transport = make_transport()
    await setup_integration(hass, transport)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="custom_components.faikout.coordinator"):
        transport.feed(state_topic(TEST_HOST), json.dumps({**HEARTBEAT, "online": False}))
        await hass.async_block_till_done()
        await outlast_grace(hass)

    assert hass.states.get(f"sensor.{TEST_HOST}_ip_address").state == "192.168.1.50"
    assert not [r for r in caplog.records if "stopped reporting" in r.getMessage()]
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE

    transport.feed(state_topic(TEST_HOST), json.dumps({**HEARTBEAT, "online": True}))
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE


async def test_fresh_values_after_a_return_are_not_held_back(hass, freezer):
    """Coming back is not a data push: the first fresh status shows at once.

    Counting it as one held the new values back for a whole update interval
    while the ones from before the loss were shown as live.
    """
    transport = make_transport()
    await setup_integration(hass, transport, options={CONF_UPDATE_INTERVAL: 30})
    freezer.tick(timedelta(seconds=120))

    transport.feed(state_topic(TEST_HOST), json.dumps({"up": False, "reason": "LWT"}))
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    transport.feed(state_topic(TEST_HOST), json.dumps(HEARTBEAT))
    await hass.async_block_till_done()
    transport.feed(status_topic(TEST_HOST), json.dumps({"home": 25.0}))
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).attributes["current_temperature"] == 25.0


class _MetaFirstTransport(FakeTransport):
    """The broker hands the retained module message over before the status."""

    async def async_subscribe(self, topic, callback):
        unsub = await super().async_subscribe(topic, callback)
        if topic == state_topic(TEST_HOST):
            self.feed(status_topic(TEST_HOST), json.dumps(STATUS_PAYLOAD))
        return unsub


async def test_a_module_message_before_the_status_does_not_hold_it_back(hass):
    transport = _MetaFirstTransport({state_topic(TEST_HOST): json.dumps(META_PAYLOAD)})
    await setup_integration(hass, transport, options={CONF_UPDATE_INTERVAL: 3600})

    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5


class _LinkDropsWhileSubscribing(FakeTransport):
    async def async_subscribe(self, topic, callback):
        unsub = await super().async_subscribe(topic, callback)
        if topic == state_topic(TEST_HOST):
            self.listener(False)
        return unsub


async def test_a_link_lost_during_setup_is_not_overwritten(hass):
    transport = _LinkDropsWhileSubscribing(make_transport()._initial)
    await setup_integration(hass, transport)

    await outlast_grace(hass)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


class _StopReportsTheLink(FakeTransport):
    """paho reports its own disconnect while the client is being stopped."""

    async def async_stop(self):
        await super().async_stop()
        self.listener(False)


async def test_nothing_fires_after_unload(hass, caplog):
    """A loss in its grace, or one the stopping client reports, dies with the entry."""
    import logging

    transport = _StopReportsTheLink(make_transport()._initial)
    entry = await setup_integration(hass, transport)
    coordinator = entry.runtime_data
    transport.feed(state_topic(TEST_HOST), json.dumps({"up": False, "reason": "LWT"}))
    await hass.async_block_till_done()
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="custom_components.faikout.coordinator"):
        await coordinator.async_shutdown()
        await outlast_grace(hass)

    assert not caplog.records


async def test_entities_are_unavailable_until_the_first_status(hass):
    """Nothing to show yet is not "all fine": a typo in the host looks like this."""
    from unittest.mock import AsyncMock, patch

    transport = FakeTransport({state_topic(TEST_HOST): json.dumps(META_PAYLOAD)})
    with patch(
        "custom_components.faikout.coordinator.FaikoutCoordinator.async_wait_first_data",
        AsyncMock(),
    ):
        await setup_integration(hass, transport)

    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_availability_is_logged_once_each_way_and_never_as_an_error(
    hass, caplog
):
    """A module the user switched off is news, not a fault.

    Home Assistant's log-when-unavailable rule asks for one line each way at
    info level; anything louder puts a red row in the log panel every time
    someone turns their air conditioner off.
    """
    import logging

    transport = make_transport()
    await setup_integration(hass, transport)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="custom_components.faikout.coordinator"):
        transport.feed(state_topic(TEST_HOST), "false")
        await hass.async_block_till_done()
        await outlast_grace(hass)
        transport.feed(state_topic(TEST_HOST), "false")  # no second line for the same news
        await hass.async_block_till_done()
        transport.feed(state_topic(TEST_HOST), "true")
        await hass.async_block_till_done()

    lines = [record for record in caplog.records if "Module" in record.getMessage()]
    assert [record.levelno for record in lines] == [logging.INFO, logging.INFO]
    assert "stopped reporting" in lines[0].getMessage()
    assert "is reporting again" in lines[1].getMessage()
    assert TEST_HOST not in lines[0].getMessage()


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
    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    async_fire_time_changed(hass, dt_util.utcnow() + __import__("datetime").timedelta(seconds=61))
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).attributes["temperature"] == 25


async def test_non_numeric_value_does_not_break_a_scaled_sensor(hass):
    """The device is untrusted: a string must not raise inside the property."""
    transport = make_transport(status={**STATUS_PAYLOAD, "Whheating": 1000})
    await setup_integration(hass, transport)
    entity_id = f"sensor.{TEST_HOST}_energy_heating"
    assert hass.states.get(entity_id).state == "1.0"

    transport.feed(status_topic(TEST_HOST), json.dumps({"Whheating": "nonsense"}))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state in ("unknown", "unavailable")


# --- demand (settable) -------------------------------------------------------
async def test_demand_number_reflects_state_and_sends_command(hass):
    """Demand is a control, not a reading: the device accepts writes."""
    transport = make_transport()
    await setup_integration(hass, transport)

    entity_id = f"number.{TEST_HOST}_demand"
    state = hass.states.get(entity_id)
    assert state.state == "100"
    assert state.attributes["min"] == 30
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 5

    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 85}, blocking=True
    )
    topic, payload = transport.published[-1]
    assert topic == f"command/{TEST_HOST}/control"
    assert payload == {"demand": 85}

    transport.feed(status_topic(TEST_HOST), json.dumps({"demand": 85}))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "85"


async def test_demand_is_not_also_a_sensor(hass):
    """One entity per value; the read-only sensor was replaced."""
    await setup_integration(hass, make_transport())
    assert hass.states.get(f"sensor.{TEST_HOST}_demand") is None


async def test_no_demand_entity_when_the_model_does_not_report_it(hass):
    status = {k: v for k, v in STATUS_PAYLOAD.items() if k != "demand"}
    await setup_integration(hass, make_transport(status=status))
    assert hass.states.get(f"number.{TEST_HOST}_demand") is None


async def test_heartbeat_meta_does_not_drop_diagnostics(hass):
    """Short bare-state heartbeat must not wipe fields from the full status."""
    transport = make_transport()
    await setup_integration(hass, transport)
    assert hass.states.get(f"sensor.{TEST_HOST}_ip_address").state == "192.168.1.50"

    # device sends its periodic heartbeat: only id/ts/up/uptime, no ip/mem/wifi
    transport.feed(
        state_topic(TEST_HOST),
        json.dumps({"id": "AABBCCDDEEFF", "ts": "2026-07-18T20:00:00Z", "uptime": 999}),
    )
    await hass.async_block_till_done()

    assert hass.states.get(f"sensor.{TEST_HOST}_ip_address").state == "192.168.1.50"


# --- capabilities follow the actual unit ------------------------------------
async def test_capabilities_follow_a_non_s21_protocol(hass):
    """CN_WIRED has three fan steps and a 1 degree setpoint, not S21's five."""
    status = {**STATUS_PAYLOAD, "protocol": "CN_WIRED", "fan": "3"}
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert a["fan_modes"] == ["auto", "quiet", "1", "3", "5"]
    assert a["target_temp_step"] == 1.0


async def test_capabilities_follow_s21(hass):
    """The same assertions for S21, so the two cannot silently converge."""
    await setup_integration(hass, make_transport())

    a = hass.states.get(CLIMATE).attributes
    assert a["fan_modes"] == ["auto", "quiet", "1", "2", "3", "4", "5"]
    assert a["target_temp_step"] == 0.5


async def test_unknown_protocol_falls_back_to_the_s21_defaults(hass):
    status = {**STATUS_PAYLOAD, "protocol": "SOMETHING_NEW"}
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert a["target_temp_step"] == 0.5
    assert "1" in a["fan_modes"]


async def test_fan_mode_is_never_missing_from_the_advertised_list(hass):
    """`fantype` can override the protocol and is not published over MQTT.

    A CN_WIRED unit set to five levels reports a step the protocol-derived
    list does not contain; advertising that list would stop the user from
    selecting the mode the unit is already in.
    """
    status = {**STATUS_PAYLOAD, "protocol": "CN_WIRED", "fan": "4"}
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert a["fan_mode"] == "4"
    assert a["fan_mode"] in a["fan_modes"]


@pytest.mark.parametrize(
    ("fields", "vertical", "horizontal"),
    [
        (("swingv", "swingh"), True, True),
        (("swingv",), True, False),
        (("swingh",), False, True),
    ],
)
async def test_swing_axes_are_advertised_separately(hass, fields, vertical, horizontal):
    status = {k: v for k, v in STATUS_PAYLOAD.items() if not k.startswith("swing")}
    status.update(dict.fromkeys(fields, False))
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert (a.get("swing_modes") == ["off", "on"]) is vertical
    assert (a.get("swing_horizontal_modes") == ["off", "on"]) is horizontal


async def test_no_swing_axes_means_no_swing_support(hass):
    """A unit without swing must not advertise a control it cannot obey."""
    from homeassistant.components.climate import ClimateEntityFeature

    status = {k: v for k, v in STATUS_PAYLOAD.items() if not k.startswith("swing")}
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert a.get("swing_modes") is None
    assert a.get("swing_horizontal_modes") is None
    assert not a["supported_features"] & ClimateEntityFeature.SWING_MODE
    assert not a["supported_features"] & ClimateEntityFeature.SWING_HORIZONTAL_MODE


async def test_setup_failure_releases_the_transport_when_cancelled(hass):
    """A cancelled setup is not an Exception; the socket must still be closed."""
    import asyncio

    transport = make_transport()

    async def _cancel(*args, **kwargs):
        raise asyncio.CancelledError

    transport.async_subscribe = _cancel

    from unittest.mock import patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.faikout.const import CONF_DEVICE_ID, CONF_HOST, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_DEVICE_ID: TEST_HOST},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.faikout.create_transport", return_value=transport):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert transport.connected, "precondition: the connection was established"
    assert transport.stopped, "cancelled setup left the connection open"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
async def test_scaled_sensor_rejects_non_finite_values(hass, bad):
    transport = make_transport(status={**STATUS_PAYLOAD, "Whheating": 1000})
    await setup_integration(hass, transport)
    entity_id = f"sensor.{TEST_HOST}_energy_heating"
    assert hass.states.get(entity_id).state == "1.0"

    # json.dumps spells these NaN / Infinity, which json.loads accepts back.
    transport.feed(status_topic(TEST_HOST), json.dumps({"Whheating": bad}))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state in ("unknown", "unavailable")


async def test_no_swing_axes_reports_no_swing_mode(hass):
    """Advertising no swing capability while publishing "off" contradicts itself."""
    status = {k: v for k, v in STATUS_PAYLOAD.items() if not k.startswith("swing")}
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert a.get("swing_mode") is None
    assert a.get("swing_horizontal_mode") is None


# --- untrusted values must not reach Home Assistant -------------------------
@pytest.mark.parametrize("bad", [{"a": 1}, [1, 2], float("nan"), float("inf")])
async def test_unscaled_sensor_rejects_unusable_values(hass, bad):
    """Only the scaled energy counters used to be validated."""
    transport = make_transport()
    await setup_integration(hass, transport)
    entity_id = f"sensor.{TEST_HOST}_compressor"
    assert hass.states.get(entity_id).state == "42"

    transport.feed(status_topic(TEST_HOST), json.dumps({"comp": bad}))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state in ("unknown", "unavailable")


async def test_timestamp_sensor_rejects_a_non_string(hass):
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(state_topic(TEST_HOST), json.dumps({"ts": 1234567890}))
    await hass.async_block_till_done()

    assert hass.states.get(f"sensor.{TEST_HOST}_last_report").state in (
        "unknown",
        "unavailable",
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "80", True])
async def test_demand_number_rejects_unusable_values(hass, bad):
    transport = make_transport()
    await setup_integration(hass, transport)
    entity_id = f"number.{TEST_HOST}_demand"
    assert hass.states.get(entity_id).state == "100"

    transport.feed(status_topic(TEST_HOST), json.dumps({"demand": bad}))
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state in ("unknown", "unavailable")


async def test_oversized_payload_is_dropped_before_decoding(hass):
    from custom_components.faikout.const import MAX_PAYLOAD_CHARS

    transport = make_transport()
    await setup_integration(hass, transport)
    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5

    class ExplodingPayload(bytes):
        # Same size either way, so only a payload that refuses to be decoded
        # can tell "measured first" from "decoded, then measured".
        def decode(self, *args, **kwargs):
            raise AssertionError("payload was decoded before its size was checked")

    huge = ExplodingPayload(('{"temp": 30, "pad": "' + "x" * MAX_PAYLOAD_CHARS + '"}').encode())
    transport.feed(status_topic(TEST_HOST), huge)
    await hass.async_block_till_done()

    assert hass.states.get(CLIMATE).attributes["temperature"] == 21.5


async def test_string_false_online_marks_the_device_unavailable(hass):
    """A bridge that stringifies booleans must not read as permanently on."""
    transport = make_transport()
    await setup_integration(hass, transport)
    assert hass.states.get(CLIMATE).state != STATE_UNAVAILABLE

    transport.feed(status_topic(TEST_HOST), json.dumps({"online": "false"}))
    await hass.async_block_till_done()
    await outlast_grace(hass)

    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE


async def test_fan_control_is_not_offered_without_a_fan(hass):
    """A visible control the device ignores looks like a broken integration."""
    from homeassistant.components.climate import ClimateEntityFeature

    status = {k: v for k, v in STATUS_PAYLOAD.items() if k != "fan"}
    await setup_integration(hass, make_transport(status=status))

    a = hass.states.get(CLIMATE).attributes
    assert not a["supported_features"] & ClimateEntityFeature.FAN_MODE


@pytest.mark.parametrize("bad", ["warm", True, {"v": 1}, float("nan")])
async def test_climate_temperatures_reject_unusable_values(hass, bad):
    transport = make_transport()
    await setup_integration(hass, transport)
    assert hass.states.get(CLIMATE).attributes["current_temperature"] == 19.5

    transport.feed(status_topic(TEST_HOST), json.dumps({"home": bad, "temp": bad}))
    await hass.async_block_till_done()

    a = hass.states.get(CLIMATE).attributes
    assert a.get("current_temperature") is None
    assert a.get("temperature") is None


async def test_demand_below_the_device_minimum_is_not_reported(hass):
    """The entity advertises 30..100; a lower value could not be set again."""
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(status_topic(TEST_HOST), json.dumps({"demand": 0}))
    await hass.async_block_till_done()

    assert hass.states.get(f"number.{TEST_HOST}_demand").state in (
        "unknown",
        "unavailable",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "9999-12-31T23:59:59-23:59",   # parses, then overflows on conversion
        "0001-01-01T00:00:00+23:59",
        "2026-02-30T12:00:00Z",        # right shape, impossible day: raises in the parser
        "2026-13-01T12:00:00Z",
        "not a timestamp",
        "",
    ],
)
async def test_timestamp_sensor_survives_extreme_values(hass, raw):
    """A crafted value must not break the entity update over and over."""
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(state_topic(TEST_HOST), json.dumps({"ts": raw}))
    await hass.async_block_till_done()

    assert hass.states.get(f"sensor.{TEST_HOST}_last_report").state in (
        "unknown",
        "unavailable",
    )
    # the rest of the entity keeps working
    assert hass.states.get(CLIMATE).state == "heat"


async def test_set_temperature_with_hvac_mode_sets_the_mode_too(hass):
    """The climate service contract lets set_temperature carry a mode.

    Dropping it published only the setpoint: `{temperature: 23, hvac_mode: off}`
    from a schedule left the unit running and reported success.
    """
    transport = make_transport()
    await setup_integration(hass, transport)

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE, "temperature": 23, "hvac_mode": "cool"},
        blocking=True,
    )
    assert transport.last_command == {"temp": 23, "power": True, "mode": "C"}

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": CLIMATE, "temperature": 23, "hvac_mode": "off"},
        blocking=True,
    )
    assert transport.last_command == {"temp": 23, "power": False}

    published = len(transport.published)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": CLIMATE, "temperature": 23, "hvac_mode": "auto"},
            blocking=True,
        )
    assert len(transport.published) == published


async def test_module_diagnostics_stay_available_when_the_indoor_unit_is_lost(hass):
    """The module reports its network data itself, not through the indoor unit.

    Tying them to /status made the IP address and signal disappear exactly
    when someone needs them to diagnose a lost indoor unit.
    """
    transport = make_transport()
    await setup_integration(hass, transport)

    transport.feed(status_topic(TEST_HOST), json.dumps({"online": False}))
    await hass.async_block_till_done()
    await outlast_grace(hass)

    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
    assert hass.states.get(f"sensor.{TEST_HOST}_ip_address").state == "192.168.1.50"

    transport.feed(state_topic(TEST_HOST), "false")
    await hass.async_block_till_done()
    await outlast_grace(hass)
    assert hass.states.get(f"sensor.{TEST_HOST}_ip_address").state == STATE_UNAVAILABLE


@pytest.mark.parametrize("offline", [json.dumps({"online": False}), "false"])
async def test_indoor_unit_going_offline_is_not_delayed_by_the_throttle(hass, offline):
    """Availability bypasses the throttle on the status topic as well.

    Only the module topic did; a lost indoor unit kept showing stale values as
    live for up to the whole update interval, on top of the grace.
    """
    transport = make_transport()
    await setup_integration(hass, transport, options={CONF_UPDATE_INTERVAL: 3600})
    transport.feed(status_topic(TEST_HOST), offline)
    await hass.async_block_till_done()
    await outlast_grace(hass)
    assert hass.states.get(CLIMATE).state == STATE_UNAVAILABLE
