"""Shared test fixtures.

The pure tests (test_const.py) need nothing but the import path. The Home
Assistant tests additionally need pytest-homeassistant-custom-component; they
are skipped wholesale when it is unavailable, so the pure suite still runs
standalone on a machine without Home Assistant.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest_plugins = []

try:  # pragma: no cover - environment probe
    # Probe a real submodule: an uninstall can leave the package directory
    # behind (stale __pycache__), which still satisfies a top-level import.
    import pytest_homeassistant_custom_component.common  # noqa: F401

    HAS_PHCC = True
except ImportError:  # pragma: no cover
    HAS_PHCC = False


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    """Let Home Assistant load `custom_components/faikout` during tests."""
    if not HAS_PHCC:
        yield
        return
    request.getfixturevalue("enable_custom_integrations")
    yield


# --- Fake transport ---------------------------------------------------------
# Stands in for HaMqttTransport/OwnMqttTransport so the whole integration can be
# set up without a broker. Tests push messages in with `feed()` and inspect the
# commands the integration published in `published`.
TEST_HOST = "testac"

# A realistic /status payload (protocol format) as sent by a live module.
STATUS_PAYLOAD = {
    "power": True,
    "mode": "H",
    "heat": True,
    "temp": 21.5,
    "home": 19.5,
    "outside": 4.0,
    "liquid": 38.0,
    "fan": "A",
    "swingv": False,
    "swingh": False,
    "powerful": False,
    "econo": False,
    "comp": 42,
    "fanrpm": 780,
    "protocol": "S21",
}

# The bare state/<host> topic (word/app format) carries device + WiFi metadata.
META_PAYLOAD = {
    "app": "Faikin",
    "version": "v1.10",
    "build-suffix": "-S21",
    "id": "AABBCCDDEEFF",
    "rssi": -58,
    "uptime": 12345,
    "ipv4": "192.168.1.50",
}


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakeTransport:
    """Records subscriptions and publishes; lets tests inject messages."""

    def __init__(self, initial=None):
        self.subs = {}
        self.published = []
        self.connected = False
        self.stopped = False
        self.listener = None
        # Delivered as soon as the coordinator subscribes, so setup does not
        # sit in async_wait_first_data waiting for a device that isn't there.
        self._initial = initial or {}

    def set_connection_listener(self, listener):
        self.listener = listener

    async def async_connect(self):
        self.connected = True

    async def async_subscribe(self, topic, callback):
        self.subs[topic] = callback
        if topic in self._initial:
            callback(FakeMessage(topic, self._initial[topic]))

        def _unsub():
            self.subs.pop(topic, None)

        return _unsub

    async def async_publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))

    async def async_stop(self):
        self.stopped = True

    # -- test helpers --------------------------------------------------------
    def feed(self, topic, payload):
        """Deliver a message as the broker would."""
        callback = self.subs.get(topic)
        assert callback is not None, f"nothing subscribed to {topic}"
        callback(FakeMessage(topic, payload))

    @property
    def last_command(self):
        assert self.published, "no command was published"
        return self.published[-1][1]
