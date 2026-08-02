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


# --- discovery bounds -------------------------------------------------------
def test_collect_module_stops_adding_hosts_at_the_cap():
    """Discovery listens on a wildcard, so the host list is attacker-influenced."""
    from custom_components.faikout.const import MAX_DISCOVERED_HOSTS
    from custom_components.faikout.transport import collect_module

    found = {}
    for i in range(MAX_DISCOVERED_HOSTS + 50):
        collect_module(found, f"state/host{i}", "true")
    assert len(found) == MAX_DISCOVERED_HOSTS


def test_collect_module_still_updates_a_known_host_at_the_cap():
    from custom_components.faikout.const import MAX_DISCOVERED_HOSTS
    from custom_components.faikout.transport import collect_module

    found = {}
    for i in range(MAX_DISCOVERED_HOSTS):
        collect_module(found, f"state/host{i}", "true")
    collect_module(found, "state/host0", '{"id": "AABBCCDDEEFF"}')
    assert found["host0"] == "AABBCCDDEEFF"


def test_collect_module_refuses_an_oversized_payload():
    from custom_components.faikout.const import MAX_PAYLOAD_CHARS
    from custom_components.faikout.transport import collect_module

    found = {}
    huge = '{"id": "' + "x" * (MAX_PAYLOAD_CHARS + 10) + '"}'
    collect_module(found, "state/GuestAC", huge)
    # the host is still noted, but the payload was never parsed
    assert found == {"GuestAC": None}
