"""Helpers for the Home Assistant integration tests.

Importing this module requires Home Assistant; the test modules that use it
guard with `pytest.importorskip`.
"""
import json
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.faikout.const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MAC,
    DOMAIN,
    state_topic,
    status_topic,
    device_id_for,
)

from .conftest import META_PAYLOAD, STATUS_PAYLOAD, TEST_HOST, FakeTransport


def make_transport(status=None, meta=None):
    """A FakeTransport that answers the initial subscriptions like a live module."""
    return FakeTransport(
        {
            status_topic(TEST_HOST): json.dumps(
                STATUS_PAYLOAD if status is None else status
            ),
            state_topic(TEST_HOST): json.dumps(
                META_PAYLOAD if meta is None else meta
            ),
        }
    )


async def setup_integration(hass, transport, options=None, mac=None, host=TEST_HOST):
    """Set up the integration with a fake transport; return the config entry."""
    device_id = device_id_for(mac, host)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: host, CONF_MAC: mac, CONF_DEVICE_ID: device_id},
        options=options or {},
        unique_id=device_id,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.faikout.create_transport", return_value=transport
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry
