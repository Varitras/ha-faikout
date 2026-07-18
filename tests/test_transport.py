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
    t = transport.create_transport(
        object(),
        _entry({CONF_USE_OWN_MQTT: True, CONF_MQTT_HOST: "192.168.1.10"}),
    )
    assert isinstance(t, transport.OwnMqttTransport)


def test_own_mqtt_falls_back_without_host():
    t = transport.create_transport(object(), _entry({CONF_USE_OWN_MQTT: True}))
    assert isinstance(t, transport.HaMqttTransport)
