"""Transport selection tests (needs Home Assistant installed)."""
import types

import pytest

pytest.importorskip("homeassistant")

from custom_components.faikout import transport  # noqa: E402
from custom_components.faikout.const import (  # noqa: E402
    CONF_MQTT_HOST,
    CONF_USE_OWN_MQTT,
)


def _entry(options):
    return types.SimpleNamespace(options=options)


def test_default_uses_ha_mqtt():
    t = transport.create_transport(object(), _entry({}))
    assert isinstance(t, transport.HaMqttTransport)


def test_own_mqtt_selected_when_enabled_with_host():
    pytest.importorskip("paho.mqtt.client")  # own transport needs paho
    t = transport.create_transport(
        object(),
        _entry({CONF_USE_OWN_MQTT: True, CONF_MQTT_HOST: "192.168.1.10"}),
    )
    assert isinstance(t, transport.OwnMqttTransport)


def test_own_mqtt_falls_back_without_host():
    t = transport.create_transport(object(), _entry({CONF_USE_OWN_MQTT: True}))
    assert isinstance(t, transport.HaMqttTransport)


# --- discovery payload parsing ----------------------------------------------
# collect_module is what actually turns broker traffic into "which modules exist
# and what is their MAC", so it gets tested directly rather than through a mock.
def test_collect_module_extracts_mac():
    from custom_components.faikout.transport import collect_module

    found = {}
    collect_module(found, "state/GuestAC", '{"app":"Faikin","id":"AABBCCDDEEFF"}')
    assert found == {"GuestAC": "AABBCCDDEEFF"}


def test_collect_module_accepts_bytes():
    from custom_components.faikout.transport import collect_module

    found = {}
    collect_module(found, "state/GuestAC", b'{"id":"AABBCCDDEEFF"}')
    assert found["GuestAC"] == "AABBCCDDEEFF"


def test_collect_module_records_host_without_mac():
    """A module still counts as found when the payload carries no id."""
    from custom_components.faikout.transport import collect_module

    found = {}
    collect_module(found, "state/GuestAC", "true")
    assert found == {"GuestAC": None}


def test_collect_module_keeps_known_mac_on_later_presence_message():
    from custom_components.faikout.transport import collect_module

    found = {}
    collect_module(found, "state/GuestAC", '{"id":"AABBCCDDEEFF"}')
    collect_module(found, "state/GuestAC", "false")
    assert found["GuestAC"] == "AABBCCDDEEFF"


@pytest.mark.parametrize(
    "topic", ["state", "state/", "state/GuestAC/status", "other/GuestAC"]
)
def test_collect_module_ignores_other_topics(topic):
    from custom_components.faikout.transport import collect_module

    found = {}
    collect_module(found, topic, '{"id":"AABBCCDDEEFF"}')
    assert found == {}
