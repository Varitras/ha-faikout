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
    CONF_MQTT_CLEAR_PASSWORD,
    CONF_MQTT_HOST,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TLS,
    CONF_MQTT_TLS_INSECURE,
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
def options_flow_environment():
    """Saving options reloads the entry and probes a changed broker.

    Neither may reach a real broker here; a test about a failing probe patches
    it again inside.
    """
    with (
        patch("custom_components.faikout.async_setup_entry", return_value=True),
        patch(
            "custom_components.faikout.config_flow.async_discover_on_broker",
            AsyncMock(return_value={}),
        ),
    ):
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


# --- via your own broker ------------------------------------------------------
BROKER = {
    CONF_MQTT_HOST: "10.0.0.5",
    CONF_MQTT_PORT: 1883,
    CONF_MQTT_USERNAME: "user",
    CONF_MQTT_PASSWORD: "secret",
    CONF_MQTT_TLS: False,
    CONF_MQTT_TLS_INSECURE: False,
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
    # The existing entry is on Home Assistant's broker, this flow on another.
    assert result["reason"] == "rename_other_broker"


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


def _entry_with_credentials(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST},
        options={
            CONF_UPDATE_INTERVAL: 0,
            CONF_USE_OWN_MQTT: True,
            CONF_MQTT_HOST: "10.0.0.5",
            CONF_MQTT_PORT: 1883,
            CONF_MQTT_USERNAME: "faikout",
            CONF_MQTT_PASSWORD: "stored-secret",
        },
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    return entry




async def test_options_form_does_not_hand_the_stored_password_to_the_browser(hass):
    """Checked on what the frontend receives, not on the schema object.

    Defaults and suggested values are both serialised into the form response.
    """
    import probatio
    from homeassistant.helpers import config_validation as cv

    entry = _entry_with_credentials(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    sent = repr(
        probatio.to_field_list(
            result["data_schema"], custom_serializer=cv.custom_serializer
        )
    )

    assert "faikout" in sent  # the username is still shown
    assert "stored-secret" not in sent


@pytest.mark.usefixtures("options_flow_environment")
async def test_options_flow_keeps_the_stored_password_when_left_empty(hass):
    """Credentials are a pair: a blank password beside the same username means
    "unchanged", not "none" - the form no longer shows the old one to re-type."""
    entry = _entry_with_credentials(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UPDATE_INTERVAL: 0,
            CONF_USE_OWN_MQTT: True,
            CONF_MQTT_HOST: "10.0.0.5",
            CONF_MQTT_PORT: 1883,
            CONF_MQTT_USERNAME: "faikout",
            CONF_MQTT_PASSWORD: "",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MQTT_PASSWORD] == "stored-secret"


@pytest.mark.usefixtures("options_flow_environment")
async def test_options_flow_can_clear_only_the_password(hass):
    """A username without a password is a configuration paho accepts, and the
    form cannot show the stored password to be deleted - so clearing it is an
    explicit action of its own."""
    entry = _entry_with_credentials(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UPDATE_INTERVAL: 0,
            CONF_USE_OWN_MQTT: True,
            CONF_MQTT_HOST: "10.0.0.5",
            CONF_MQTT_PORT: 1883,
            CONF_MQTT_USERNAME: "faikout",
            CONF_MQTT_PASSWORD: "",
            CONF_MQTT_CLEAR_PASSWORD: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MQTT_USERNAME] == "faikout"
    assert result["data"][CONF_MQTT_PASSWORD] == ""
    assert CONF_MQTT_CLEAR_PASSWORD not in result["data"], "an action, not a setting"


@pytest.mark.usefixtures("options_flow_environment")
async def test_options_flow_never_stores_a_password_without_a_username(hass):
    """The form says clearing the username removes both; the code has to mean it
    even when a password was typed in the same edit."""
    entry = _entry_with_credentials(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UPDATE_INTERVAL: 0,
            CONF_USE_OWN_MQTT: True,
            CONF_MQTT_HOST: "10.0.0.5",
            CONF_MQTT_PORT: 1883,
            CONF_MQTT_USERNAME: "",
            CONF_MQTT_PASSWORD: "typed-anyway",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MQTT_PASSWORD] == ""


@pytest.mark.usefixtures("options_flow_environment")
async def test_options_flow_drops_the_password_with_the_username(hass):
    """Clearing the username is the way to an anonymous broker."""
    entry = _entry_with_credentials(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_UPDATE_INTERVAL: 0,
            CONF_USE_OWN_MQTT: True,
            CONF_MQTT_HOST: "10.0.0.5",
            CONF_MQTT_PORT: 1883,
            CONF_MQTT_USERNAME: "",
            CONF_MQTT_PASSWORD: "",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MQTT_USERNAME] == ""
    assert result["data"][CONF_MQTT_PASSWORD] == ""


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


# --- TLS ----------------------------------------------------------------------
async def test_tls_switch_is_stored_and_moves_the_port(hass, mock_setup_entry):
    """Ticking TLS must not leave the connection on the plaintext port."""
    from custom_components.faikout.transport import async_discover_on_broker  # noqa: F401

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )

    seen = {}

    async def _capture(hass_, host, port, user, pw, seconds, tls=False, insecure=False):
        seen.update(port=port, tls=tls, insecure=insecure)
        return {TEST_HOST: None}

    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker", _capture
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**BROKER, CONF_MQTT_TLS: True, CONF_MQTT_TLS_INSECURE: True},
        )

    # Discovery already has to speak TLS, or a TLS-only broker is unreachable.
    assert seen == {"port": 8883, "tls": True, "insecure": True}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )
    assert result["options"][CONF_MQTT_TLS] is True
    assert result["options"][CONF_MQTT_TLS_INSECURE] is True
    # The stored port stays what the user entered; 8883 is derived on use.
    assert result["options"][CONF_MQTT_PORT] == 1883


async def test_same_hostname_and_same_broker_is_refused(hass):
    """After a device swap the new MAC must not get its own entry.

    On one broker the hostname is the topic, so a second entry would
    subscribe to and control exactly the same topics as the first.
    """
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: TEST_HOST,
            CONF_MAC: "11:11:11:11:11:11",
            CONF_DEVICE_ID: "11:11:11:11:11:11",
        },
        options={CONF_USE_OWN_MQTT: True, **BROKER},
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

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_hostname_on_a_different_broker_is_still_allowed(hass, mock_setup_entry):
    """The case the MAC identity exists for must keep working."""
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: TEST_HOST,
            CONF_MAC: "11:11:11:11:11:11",
            CONF_DEVICE_ID: "11:11:11:11:11:11",
        },
        options={CONF_USE_OWN_MQTT: True, **BROKER},
        unique_id="11:11:11:11:11:11",
    ).add_to_hass(hass)

    other = {**BROKER, CONF_MQTT_HOST: "10.0.0.99"}
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "own_mqtt"}
    )
    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(return_value={TEST_HOST: "22:22:22:22:22:22"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], other
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_ID] == "22:22:22:22:22:22"


async def test_home_assistant_broker_id_carries_its_tls_setting(hass):
    """A TLS broker reached both ways is still one broker.

    Home Assistant stores its TLS choice as the CA it validates against, not
    as a flag. Ignoring that made an encrypted HA connection compare unequal
    to a separate client pointing at the same endpoint.
    """
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.faikout.config_flow import FaikoutConfigFlow

    MockConfigEntry(
        domain="mqtt",
        data={"broker": "10.0.0.5", "port": 8883, "certificate": "auto"},
        state=ConfigEntryState.LOADED,
    ).add_to_hass(hass)

    flow = FaikoutConfigFlow()
    flow.hass = hass
    assert flow._ha_broker_id() == ("10.0.0.5", 8883, True)
    assert flow._broker_id(
        {
            CONF_USE_OWN_MQTT: True,
            CONF_MQTT_HOST: "10.0.0.5",
            CONF_MQTT_PORT: 8883,
            CONF_MQTT_TLS: True,
        }
    ) == flow._ha_broker_id()


async def test_own_client_pointing_at_home_assistants_broker_is_the_same_broker(hass):
    """Both routes can reach one broker; a device swap must not yield two entries."""
    from homeassistant.config_entries import ConfigEntryState

    # Marked loaded so Home Assistant does not try to set up a real broker
    # connection; only its stored address matters here.
    MockConfigEntry(
        domain="mqtt",
        data={"broker": "10.0.0.5", "port": 1883},
        state=ConfigEntryState.LOADED,
    ).add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: TEST_HOST,
            CONF_MAC: "11:11:11:11:11:11",
            CONF_DEVICE_ID: "11:11:11:11:11:11",
        },
        options={},  # uses Home Assistant's MQTT integration
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
        # BROKER points at 10.0.0.5:1883 - the very broker Home Assistant uses
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BROKER
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_HOST}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- audit 2026-10-14: recovery and options semantics -----------------------
def _failing_then_working_transport():
    """create_transport side effect: the broker first rejects us, then accepts."""
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from .ha_helpers import make_transport

    rejected = make_transport()
    rejected.async_connect = AsyncMock(side_effect=ConfigEntryAuthFailed("rejected"))
    return [rejected, make_transport()]


async def _entry_stuck_on_rejected_credentials(hass) -> MockConfigEntry:
    from homeassistant.config_entries import ConfigEntryState

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: TEST_HOST, CONF_DEVICE_ID: TEST_HOST},
        options={CONF_USE_OWN_MQTT: True, CONF_UPDATE_INTERVAL: 0, **BROKER},
        unique_id=TEST_HOST,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    return entry


async def test_reauth_revives_an_entry_whose_setup_was_rejected(hass):
    """The reload hung on an update listener that a failed setup never added.

    The user saw "re-authentication successful" and the entry stayed dead until
    Home Assistant was restarted.
    """
    from homeassistant.config_entries import ConfigEntryState

    with patch(
        "custom_components.faikout.create_transport",
        side_effect=_failing_then_working_transport(),
    ):
        entry = await _entry_stuck_on_rejected_credentials(hass)
        result = await entry.start_reauth_flow(hass)
        with patch(
            "custom_components.faikout.config_flow.async_discover_on_broker",
            AsyncMock(return_value={TEST_HOST: None}),
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {**BROKER, CONF_MQTT_PASSWORD: "new-secret"}
            )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.state is ConfigEntryState.LOADED


async def test_fixing_the_password_in_the_options_revives_a_rejected_entry(hass):
    from homeassistant.config_entries import ConfigEntryState

    with patch(
        "custom_components.faikout.create_transport",
        side_effect=_failing_then_working_transport(),
    ):
        entry = await _entry_stuck_on_rejected_credentials(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        with patch(
            "custom_components.faikout.config_flow.async_discover_on_broker",
            AsyncMock(return_value={TEST_HOST: None}),
        ):
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    CONF_UPDATE_INTERVAL: 0,
                    CONF_USE_OWN_MQTT: True,
                    **BROKER,
                    CONF_MQTT_PASSWORD: "new-secret",
                },
            )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.state is ConfigEntryState.LOADED


def _changed_broker(**overrides) -> dict:
    return {
        CONF_UPDATE_INTERVAL: 0,
        CONF_USE_OWN_MQTT: True,
        CONF_MQTT_HOST: "10.0.0.5",
        CONF_MQTT_PORT: 1883,
        CONF_MQTT_USERNAME: "faikout",
        **overrides,
    }


async def test_options_refuse_a_broker_that_cannot_be_reached(hass):
    """A typo in the broker host belongs in the form, not in a retry loop."""
    entry = _entry_with_credentials(hass)
    probe = AsyncMock(side_effect=OSError("unreachable"))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch("custom_components.faikout.config_flow.async_discover_on_broker", probe):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _changed_broker(**{CONF_MQTT_HOST: "10.0.0.55"})
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert probe.await_count == 1
    assert entry.options[CONF_MQTT_HOST] == "10.0.0.5"


async def test_options_report_rejected_credentials_on_the_form(hass):
    from custom_components.faikout.transport import MqttConnectionRefused

    class _Code:
        value = 5
        is_failure = True

    entry = _entry_with_credentials(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(side_effect=MqttConnectionRefused(_Code())),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _changed_broker(**{CONF_MQTT_PASSWORD: "wrong"})
        )

    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.usefixtures("options_flow_environment")
async def test_options_do_not_probe_when_only_the_interval_changes(hass):
    entry = _entry_with_credentials(hass)
    probe = AsyncMock(side_effect=OSError("must not be called"))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch("custom_components.faikout.config_flow.async_discover_on_broker", probe):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _changed_broker(**{CONF_UPDATE_INTERVAL: 30})
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert probe.await_count == 0


@pytest.mark.usefixtures("options_flow_environment")
async def test_an_emptied_username_reaches_the_flow_as_a_missing_key(hass):
    """The frontend drops an emptied optional text field from the submission.

    With the stored username as the field default, the schema filled it straight
    back in: an anonymous broker could not be configured through the UI.
    """
    entry = _entry_with_credentials(hass)
    submitted = _changed_broker()
    del submitted[CONF_MQTT_USERNAME]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.faikout.config_flow.async_discover_on_broker",
        AsyncMock(return_value={}),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], submitted
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not result["data"].get(CONF_MQTT_USERNAME)
    assert result["data"][CONF_MQTT_PASSWORD] == ""


async def test_an_emptied_broker_host_is_reported(hass):
    entry = _entry_with_credentials(hass)
    submitted = _changed_broker()
    del submitted[CONF_MQTT_HOST]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )

    assert result["errors"] == {CONF_MQTT_HOST: "host_required"}


RENAMED_MAC = "22:22:22:22:22:22"


def _entry_known_as(hass, host, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=host,
        data={CONF_HOST: host, CONF_MAC: RENAMED_MAC, CONF_DEVICE_ID: RENAMED_MAC},
        options=options or {},
        unique_id=RENAMED_MAC,
    )
    entry.add_to_hass(hass)
    return entry


async def _pick_on_ha_mqtt(hass, discovered, host):
    result = await _start(hass)
    with (
        patch(
            "custom_components.faikout.config_flow.mqtt.async_wait_for_mqtt_client",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.faikout.config_flow.FaikoutConfigFlow._discover_hosts",
            AsyncMock(return_value=discovered),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "ha_mqtt"}
        )
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: host}
        )


async def test_a_renamed_module_is_followed_to_its_new_topic(hass):
    """Same MAC under a new hostname is the same device with new topics.

    The entry kept subscribing to the old name and the only way out was to
    delete it and lose the entity customisations. The title follows too, as
    the device name already does.
    """
    entry = _entry_known_as(hass, "old-name")

    with patch("custom_components.faikout.async_setup_entry", return_value=True):
        result = await _pick_on_ha_mqtt(hass, {TEST_HOST: RENAMED_MAC}, TEST_HOST)

    assert result["reason"] == "module_renamed"
    assert entry.data[CONF_HOST] == TEST_HOST
    assert entry.title == TEST_HOST
    assert entry.data[CONF_DEVICE_ID] == RENAMED_MAC


async def test_a_module_found_on_another_broker_is_not_renamed(hass):
    """The rename may only move an entry's topics on the broker it talks to.

    Rewritten from a flow on another broker, the entry kept its own broker and
    could end up on a topic another entry there already controls.
    """
    entry = _entry_known_as(
        hass, "old-name", options={CONF_USE_OWN_MQTT: True, CONF_MQTT_HOST: "10.0.0.9"}
    )

    result = await _pick_on_ha_mqtt(hass, {TEST_HOST: RENAMED_MAC}, TEST_HOST)

    assert result["reason"] == "rename_other_broker"
    assert entry.data[CONF_HOST] == "old-name"
    assert entry.title == "old-name"


async def test_a_name_the_module_still_announces_is_not_a_rename(hass):
    """A retained status under a second name is not proof of a rename."""
    entry = _entry_known_as(hass, "old-name")

    result = await _pick_on_ha_mqtt(
        hass, {"old-name": RENAMED_MAC, TEST_HOST: RENAMED_MAC}, TEST_HOST
    )

    # Not "already configured": the user would believe the entry works.
    assert result["reason"] == "rename_old_name_still_announced"
    assert entry.data[CONF_HOST] == "old-name"


async def test_a_title_the_user_chose_survives_a_rename(hass):
    entry = _entry_known_as(hass, "old-name")
    hass.config_entries.async_update_entry(entry, title="Living room")

    with patch("custom_components.faikout.async_setup_entry", return_value=True):
        result = await _pick_on_ha_mqtt(hass, {TEST_HOST: RENAMED_MAC}, TEST_HOST)

    assert result["reason"] == "module_renamed"
    assert entry.data[CONF_HOST] == TEST_HOST
    assert entry.title == "Living room"
