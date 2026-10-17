"""The default transport against Home Assistant's real MQTT integration.

Everything else runs on a fake transport, which models the integration's side
of the contract away: topic and payload of a command, retain, and how a
received message is delivered. These run through `mqtt_mock` instead.
"""
import asyncio
import json
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component.common")

from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.faikout.const import (  # noqa: E402
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    device_id_for,
    status_topic,
)

from .conftest import STATUS_PAYLOAD, TEST_HOST  # noqa: E402

CLIMATE = f"climate.{TEST_HOST}"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The MQTT integration's own housekeeping timer outlives each test."""
    return True


async def _set_up(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_MAC: None, CONF_DEVICE_ID: TEST_HOST},
        options={CONF_UPDATE_INTERVAL: 0},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    # Not under test here, and a broker mock answers nothing on its own.
    with patch(
        "custom_components.faikout.coordinator.FaikoutCoordinator.async_wait_first_data"
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_state_arrives_through_home_assistants_mqtt(hass, mqtt_mock):
    await _set_up(hass)

    async_fire_mqtt_message(hass, status_topic(TEST_HOST), json.dumps(STATUS_PAYLOAD))
    await hass.async_block_till_done()

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes["temperature"] == 21.5


async def test_commands_reach_home_assistants_mqtt_unretained(hass, mqtt_mock):
    """A retained command would be replayed to the module after every reboot."""
    await _set_up(hass)
    async_fire_mqtt_message(hass, status_topic(TEST_HOST), json.dumps(STATUS_PAYLOAD))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": CLIMATE}, blocking=True
    )

    mqtt_mock.async_publish.assert_called_once()
    topic, payload, qos, retain = mqtt_mock.async_publish.call_args.args
    assert (topic, json.loads(payload)) == (f"command/{TEST_HOST}/control", {"power": False})
    assert (qos, retain) == (0, False)


async def test_discovery_reads_modules_from_a_real_state_message(hass, mqtt_mock):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    with patch("custom_components.faikout.config_flow.DISCOVERY_SECONDS", 0.2):
        listening = hass.async_create_task(
            hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "ha_mqtt"}
            )
        )
        await asyncio.sleep(0.05)
        async_fire_mqtt_message(hass, "state/GuestAC", '{"app":"Faikin","id":"AABBCCDDEEFF"}')
        result = await listening

    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.faikout.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "GuestAC"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == device_id_for("AABBCCDDEEFF", "GuestAC")
