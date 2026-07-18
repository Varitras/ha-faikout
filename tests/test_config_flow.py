"""Config and options flow, both connection paths."""
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component.common")

from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.faikout.const import (  # noqa: E402
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_USERNAME,
    CONF_UPDATE_INTERVAL,
    CONF_USE_OWN_MQTT,
    DOMAIN,
)

from .conftest import TEST_HOST  # noqa: E402


@pytest.fixture(autouse=True)
def _no_discovery_delay():
    """Discovery listens for 3s in production; not in tests."""
    with patch("custom_components.faikout.config_flow.DISCOVERY_SECONDS", 0):
        yield


@pytest.fixture
def mock_setup_entry():
    with patch(
        "custom_components.faikout.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )


async def test_first_step_offers_both_transports(hass):
    result = await _start(hass)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"ha_mqtt", "own_mqtt"}


# --- via Home Assistant's MQTT integration -----------------------------------
async def test_ha_mqtt_path_creates_entry(hass, mock_setup_entry):
    result = await _start(hass)

    with (
        patch(
            "custom_components.faikout.config_flow.mqtt.async_wait_for_mqtt_client",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.faikout.config_flow.mqtt.async_subscribe",
            AsyncMock(return_value=lambda: None),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ha_mqtt"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "ha_mqtt"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: TEST_HOST}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_HOST
    # Nothing was discovered here, so the hostname is the fallback identity.
    assert result["data"] == {
        CONF_HOST: TEST_HOST,
        CONF_MAC: None,
        CONF_DEVICE_ID: TEST_HOST,
    }
    # No broker details -> the HA MQTT transport is used.
    assert not result["options"].get(CONF_USE_OWN_MQTT)


async def test_ha_mqtt_path_aborts_without_mqtt(hass):
    result = await _start(hass)
    with patch(
        "custom_components.faikout.config_flow.mqtt.async_wait_for_mqtt_client",
        AsyncMock(return_value=False),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ha_mqtt"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"


async def test_duplicate_host_aborts(hass):
    MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: TEST_HOST}, unique_id=TEST_HOST
    ).add_to_hass(hass)

    result = await _start(hass)
    with (
        patch(
            "custom_components.faikout.config_flow.mqtt.async_wait_for_mqtt_client",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.faikout.config_flow.mqtt.async_subscribe",
            AsyncMock(return_value=lambda: None),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ha_mqtt"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: TEST_HOST}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- via an own broker --------------------------------------------------------
BROKER = {
    CONF_MQTT_HOST: "10.0.0.5",
    CONF_MQTT_PORT: 1883,
    CONF_MQTT_USERNAME: "user",
    CONF_MQTT_PASSWORD: "secret",
}


async def test_own_broker_path_stores_broker_in_options(hass, mock_setup_entry):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )
    assert result["step_id"] == "own_mqtt"

    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(return_value={TEST_HOST: "AA:BB:CC:DD:EE:FF"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BROKER
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "own_host"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The MAC seen during discovery becomes the identity, normalised.
    assert result["data"] == {
        CONF_HOST: TEST_HOST,
        CONF_MAC: "AA:BB:CC:DD:EE:FF",
        CONF_DEVICE_ID: "aa:bb:cc:dd:ee:ff",
    }
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"
    assert result["options"] == {CONF_USE_OWN_MQTT: True, **BROKER}


async def test_same_hostname_on_two_brokers_can_coexist(hass, mock_setup_entry):
    """The whole point of the MAC identity: same name, different module."""
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: TEST_HOST,
            CONF_MAC: "11:11:11:11:11:11",
            CONF_DEVICE_ID: "11:11:11:11:11:11",
        },
        unique_id="11:11:11:11:11:11",
    ).add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )
    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(return_value={TEST_HOST: "22:22:22:22:22:22"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BROKER
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "22:22:22:22:22:22"


async def test_same_module_rediscovered_still_aborts(hass):
    """Same MAC must still be refused, whatever hostname it reports now."""
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "old-name",
            CONF_MAC: "22:22:22:22:22:22",
            CONF_DEVICE_ID: "22:22:22:22:22:22",
        },
        unique_id="22:22:22:22:22:22",
    ).add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )
    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(return_value={TEST_HOST: "22:22:22:22:22:22"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BROKER
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_own_broker_unreachable_shows_error(hass):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )

    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(side_effect=OSError("refused")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BROKER
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


# --- options ------------------------------------------------------------------
async def test_options_flow_saves_update_interval(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: TEST_HOST}, unique_id=TEST_HOST
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 30, CONF_USE_OWN_MQTT: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UPDATE_INTERVAL] == 30


async def test_options_flow_requires_host_for_own_mqtt(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: TEST_HOST}, unique_id=TEST_HOST
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_UPDATE_INTERVAL: 0, CONF_USE_OWN_MQTT: True, CONF_MQTT_HOST: ""},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MQTT_HOST: "host_required"}


# --- re-authentication --------------------------------------------------------
async def test_reauth_updates_credentials_and_reloads(hass):
    """A changed broker password must be fixable without removing the device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_DEVICE_ID: TEST_HOST},
        options={CONF_USE_OWN_MQTT: True, **BROKER},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with (
        patch(
            "custom_components.faikout.config_flow.async_discover_on_broker",
            AsyncMock(return_value={TEST_HOST: None}),
        ),
        patch("custom_components.faikout.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**BROKER, CONF_MQTT_PASSWORD: "new-secret"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.options[CONF_MQTT_PASSWORD] == "new-secret"
    # The device itself must not be re-identified by a credential change.
    assert entry.data[CONF_HOST] == TEST_HOST


async def test_reauth_reports_wrong_credentials(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_DEVICE_ID: TEST_HOST},
        options={CONF_USE_OWN_MQTT: True, **BROKER},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)

    from custom_components.faikout.transport import MqttConnectionRefused

    class _Code:
        value = 5
        is_failure = True

    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(side_effect=MqttConnectionRefused(_Code())),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**BROKER, CONF_MQTT_PASSWORD: "still-wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_hostname_entry_blocks_adding_the_same_module_by_mac(hass):
    """Added by hand first, discovered later: still one device, not two."""
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_MAC: None, CONF_DEVICE_ID: TEST_HOST},
        unique_id=TEST_HOST,
    ).add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )
    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(return_value={TEST_HOST: "33:33:33:33:33:33"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BROKER
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
